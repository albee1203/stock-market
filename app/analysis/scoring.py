"""外資籌碼分數 × 新聞情緒分數 → 建議買入 / 賣出清單。

外資分數 foreign_score（-1~1）=
    0.5 × tanh(買賣超金額 / amount_scale)          # 規模：用「金額」而非張數，避免低價股灌水
  + 0.3 × 連續買賣超天數(上限 5) / 5 × 方向         # 持續性
  + 0.2 × tanh(買賣超股數佔成交量 / ratio_scale)     # 相對強度：對中小型股較公平

新聞分數 news_score（-1~1）= tanh(Σ 權重 × 個股情緒 / 1.5)，標題提到該股的新聞權重 1.5。

綜合分數 total = w_f × foreign_score + w_n × news_score
  - 建議買入：外資候選 + foreign_score > 0 + news_score > 0，依 total 由高到低
  - 建議賣出/避開：外資候選 + foreign_score < 0 + news_score < 0，依 total 由低到高
外資候選 = 連續買/賣超 ≥ min_streak 日，或單日金額 ≥ big_amount，或單日買/賣超金額排名前 top_n_by_amount。
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..collectors.news import news_window
from ..config import ScoringConfig, settings
from ..db import DailyPrice, ForeignFlow, NewsArticle, Recommendation, Stock


@dataclass
class ForeignMetrics:
    code: str
    name: str
    market: str
    net_shares: int
    close: float | None
    volume: int | None
    streak: int  # >0 連買天數；<0 連賣天數
    net_5d_shares: int
    net_amount: float | None = None
    net_ratio: float | None = None
    score: float = 0.0
    flags: list[str] = field(default_factory=list)

    @property
    def net_lots(self) -> int:
        return round(self.net_shares / 1000)


@dataclass
class NewsMetrics:
    score: float = 0.0
    count: int = 0
    bullish: int = 0
    bearish: int = 0
    keywords: Counter = field(default_factory=Counter)
    reasons: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)


def calc_streak(nets_desc: list[int]) -> int:
    """nets_desc：由新到舊的每日買賣超。回傳連續同方向天數（帶正負號）。"""
    if not nets_desc or nets_desc[0] == 0:
        return 0
    sign = 1 if nets_desc[0] > 0 else -1
    n = 0
    for v in nets_desc:
        if v * sign > 0:
            n += 1
        else:
            break
    return sign * n


def foreign_score(m: ForeignMetrics, cfg: ScoringConfig) -> float:
    if m.net_amount is not None:
        size = math.tanh(m.net_amount / cfg.amount_scale)
    else:
        size = math.tanh(m.net_lots / cfg.lots_scale)
    persist = (1 if m.streak > 0 else -1 if m.streak < 0 else 0) * min(abs(m.streak), 5) / 5
    ratio = math.tanh(m.net_ratio / cfg.ratio_scale) if m.net_ratio is not None else size
    return round(0.5 * size + 0.3 * persist + 0.2 * ratio, 4)


def compute_foreign_metrics(session: Session, d: date, cfg: ScoringConfig | None = None,
                            lookback: int = 20) -> dict[str, ForeignMetrics]:
    cfg = cfg or settings.scoring
    dates = list(session.scalars(
        select(ForeignFlow.date).where(ForeignFlow.date <= d).distinct()
        .order_by(ForeignFlow.date.desc()).limit(lookback)
    ))
    if not dates or dates[0] != d:
        return {}
    history: dict[str, dict[date, int]] = defaultdict(dict)
    for f in session.scalars(select(ForeignFlow).where(ForeignFlow.date.in_(dates))):
        history[f.code][f.date] = f.net_shares
    stocks = {s.code: s for s in session.scalars(select(Stock))}
    prices = {p.code: p for p in session.scalars(select(DailyPrice).where(DailyPrice.date == d))}

    out: dict[str, ForeignMetrics] = {}
    for code, by_date in history.items():
        if d not in by_date:
            continue
        nets_desc = [by_date.get(x, 0) for x in dates]  # 缺資料的日子視為 0，會中斷連續
        p = prices.get(code)
        st = stocks.get(code)
        m = ForeignMetrics(
            code=code,
            name=st.name if st else code,
            market=st.market if st else "",
            net_shares=by_date[d],
            close=p.close if p else None,
            volume=p.volume if p else None,
            streak=calc_streak(nets_desc),
            net_5d_shares=sum(nets_desc[:5]),
        )
        if m.close:
            m.net_amount = m.net_shares * m.close
        if m.volume:
            m.net_ratio = m.net_shares / m.volume
        m.score = foreign_score(m, cfg)
        out[code] = m

    # 標記候選旗標
    def size_key(m: ForeignMetrics) -> float:
        return m.net_amount if m.net_amount is not None else m.net_shares * 50.0  # 無價時粗估

    ranked = sorted(out.values(), key=size_key)
    top_sell = {m.code for m in ranked[:cfg.top_n_by_amount] if m.net_shares < 0}
    top_buy = {m.code for m in ranked[-cfg.top_n_by_amount:] if m.net_shares > 0}
    for m in out.values():
        if m.streak >= cfg.min_streak:
            m.flags.append(f"連續買超{m.streak}日")
        elif m.streak <= -cfg.min_streak:
            m.flags.append(f"連續賣超{-m.streak}日")
        if m.net_amount is not None and abs(m.net_amount) >= cfg.big_amount:
            m.flags.append("單日大買" if m.net_amount > 0 else "單日大賣")
        if m.code in top_buy:
            m.flags.append("買超金額前段")
        if m.code in top_sell:
            m.flags.append("賣超金額前段")
    return out


def compute_news_metrics(session: Session, d: date) -> dict[str, NewsMetrics]:
    start, end = news_window(d)
    articles = session.scalars(
        select(NewsArticle).where(NewsArticle.published_at.between(start, end), NewsArticle.analyzer.is_not(None))
    )
    raw: dict[str, float] = defaultdict(float)
    out: dict[str, NewsMetrics] = defaultdict(NewsMetrics)
    for a in articles:
        for code, s in a.stock_map().items():
            score = float(s.get("score", 0))
            w = 1.5 if s.get("in_title") else 1.0
            raw[code] += w * score
            nm = out[code]
            nm.count += 1
            if score > 0.1:
                nm.bullish += 1
            elif score < -0.1:
                nm.bearish += 1
            nm.keywords.update(s.get("keywords", []))
            if s.get("reason"):
                nm.reasons.append(s["reason"])
            nm.headlines.append(a.title)
    for code, nm in out.items():
        nm.score = round(math.tanh(raw[code] / 1.5), 4)
    return dict(out)


def _fmt_amount(x: float | None) -> str:
    if x is None:
        return ""
    return f"約 {abs(x) / 1e8:.1f} 億元"


def build_reason(side: str, fm: ForeignMetrics, nm: NewsMetrics) -> str:
    verb = "買超" if side == "buy" else "賣超"
    parts = []
    if abs(fm.streak) >= 2:
        parts.append(f"外資連續{verb} {abs(fm.streak)} 日")
    amt = _fmt_amount(fm.net_amount)
    seg = f"今日{verb} {abs(fm.net_lots):,} 張" + (f"（{amt}）" if amt else "")
    if fm.net_ratio is not None:
        seg += f"，佔成交量 {abs(fm.net_ratio):.0%}"
    parts.append(seg)
    flow = "，".join(parts)

    sign = "+" if side == "buy" else "-"
    kws = [k[1:] for k, _ in nm.keywords.most_common() if k.startswith(sign)][:4]
    tone = "利多" if side == "buy" else "利空"
    n = nm.bullish if side == "buy" else nm.bearish
    news = f"新聞{tone} {n}/{nm.count} 則"
    if kws:
        news += "：" + "、".join(kws)
    if nm.reasons:
        news += f"（{nm.reasons[0]}）"
    return f"{flow}｜{news}"


def build_recommendations(session: Session, d: date, cfg: ScoringConfig | None = None,
                          persist: bool = True) -> dict[str, list[dict]]:
    cfg = cfg or settings.scoring
    fms = compute_foreign_metrics(session, d, cfg)
    nms = compute_news_metrics(session, d)

    buy, sell = [], []
    for code, fm in fms.items():
        nm = nms.get(code)
        if not fm.flags or nm is None:
            continue
        total = round(cfg.weight_foreign * fm.score + cfg.weight_news * nm.score, 4)
        row = {
            "code": code, "name": fm.name, "market": fm.market,
            "total_score": total, "foreign_score": fm.score, "news_score": nm.score,
            "streak": fm.streak, "net_shares": fm.net_shares, "net_lots": fm.net_lots,
            "net_amount": fm.net_amount, "close": fm.close, "news_count": nm.count,
            "flags": fm.flags, "headlines": nm.headlines[:3],
        }
        if fm.score > 0 and nm.score > 0:
            row["keywords"] = [k[1:] for k, _ in nm.keywords.most_common() if k.startswith("+")][:5]
            row["reason"] = build_reason("buy", fm, nm)
            buy.append(row)
        elif fm.score < 0 and nm.score < 0:
            row["keywords"] = [k[1:] for k, _ in nm.keywords.most_common() if k.startswith("-")][:5]
            row["reason"] = build_reason("sell", fm, nm)
            sell.append(row)

    buy.sort(key=lambda r: -r["total_score"])
    sell.sort(key=lambda r: r["total_score"])
    buy, sell = buy[:cfg.list_size], sell[:cfg.list_size]
    for i, r in enumerate(buy, 1):
        r["rank"] = i
    for i, r in enumerate(sell, 1):
        r["rank"] = i

    if persist:
        session.execute(delete(Recommendation).where(Recommendation.date == d))
        for side, rows in (("buy", buy), ("sell", sell)):
            for r in rows:
                session.add(Recommendation(
                    date=d, code=r["code"], side=side, rank=r["rank"], total_score=r["total_score"],
                    foreign_score=r["foreign_score"], news_score=r["news_score"], streak=r["streak"],
                    net_shares=r["net_shares"], net_amount=r["net_amount"], news_count=r["news_count"],
                    keywords=json.dumps(r["keywords"], ensure_ascii=False), reason=r["reason"],
                ))
        session.commit()
    return {"buy": buy, "sell": sell}


def foreign_metrics_as_dicts(ms: dict[str, ForeignMetrics]) -> list[dict]:
    return [asdict(m) | {"net_lots": m.net_lots} for m in ms.values()]
