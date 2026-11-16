"""讀取查詢（FastAPI 與 Streamlit 共用）。"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from dataclasses import asdict

from .analysis.scoring import compute_foreign_metrics, compute_news_metrics, foreign_metrics_as_dicts
from .config import settings
from .night import apply_pre_index, gap_alerts, night_info
from .risk import alerts_for, index_label, levels_for, to_index, track_positions
from .collectors.news import news_window
from .db import DailyPrice, ForeignFlow, NewsArticle, Recommendation, Stock

DISCLAIMER = "本工具僅為資訊整理與研究用途，不構成任何投資建議；投資前請自行判斷並承擔風險。"


def list_dates(session: Session, limit: int = 60) -> list[str]:
    q = select(ForeignFlow.date).distinct().order_by(ForeignFlow.date.desc()).limit(limit)
    return [d.isoformat() for d in session.scalars(q)]


def get_recommendations(session: Session, d: date) -> dict:
    names = {s.code: s.name for s in session.scalars(select(Stock))}
    rows = session.scalars(
        select(Recommendation).where(Recommendation.date == d).order_by(Recommendation.side, Recommendation.rank)
    )
    out: dict = {"date": d.isoformat(), "buy": [], "sell": [], "disclaimer": DISCLAIMER}
    rows = list(rows)
    levels = levels_for(session, {r.code for r in rows}, d) if rows else {}
    night = night_info(session, d)
    out["night"] = night
    for r in rows:
        idx = to_index(r.total_score)
        lv = levels.get(r.code)
        out[r.side].append({
            "index": idx, "label": index_label(idx),
            "levels": asdict(lv) if lv else None,
            "rank": r.rank, "code": r.code, "name": names.get(r.code, r.code),
            "total_score": r.total_score, "foreign_score": r.foreign_score, "news_score": r.news_score,
            "streak": r.streak, "net_lots": round(r.net_shares / 1000),
            "net_amount_e8": round(r.net_amount / 1e8, 2) if r.net_amount is not None else None,
            "news_count": r.news_count, "keywords": json.loads(r.keywords), "reason": r.reason,
        })
    apply_pre_index(out["buy"] + out["sell"], night)
    return out


def get_foreign_ranking(session: Session, d: date, side: str = "buy", limit: int = 30) -> list[dict]:
    rows = foreign_metrics_as_dicts(compute_foreign_metrics(session, d))
    key = (lambda r: r["net_amount"] if r["net_amount"] is not None else r["net_shares"] * 50.0)
    rows.sort(key=key, reverse=(side == "buy"))
    rows = [r for r in rows if (r["net_shares"] > 0) == (side == "buy") and r["net_shares"] != 0]
    return rows[:limit]


def _article_dict(a: NewsArticle, names: dict[str, str]) -> dict:
    stocks = a.stock_map()
    return {
        "id": a.id, "source": a.source, "title": a.title, "url": a.url, "summary": a.summary,
        "published_at": a.published_at.isoformat(timespec="minutes"),
        "analyzer": a.analyzer, "sentiment": a.sentiment,
        "bullish_keywords": json.loads(a.bullish_keywords), "bearish_keywords": json.loads(a.bearish_keywords),
        "stocks": [{"code": c, "name": names.get(c, c), **v} for c, v in stocks.items()],
    }


def get_news(session: Session, d: date, code: str | None = None) -> list[dict]:
    start, end = news_window(d)
    names = {s.code: s.name for s in session.scalars(select(Stock))}
    arts = session.scalars(
        select(NewsArticle).where(NewsArticle.published_at.between(start, end))
        .order_by(NewsArticle.published_at.desc())
    )
    out = [_article_dict(a, names) for a in arts]
    if code:
        out = [a for a in out if any(s["code"] == code for s in a["stocks"])]
    return out


def get_stock_detail(session: Session, code: str, days: int = 30) -> dict | None:
    st = session.get(Stock, code)
    if st is None:
        return None
    flows = list(session.scalars(
        select(ForeignFlow).where(ForeignFlow.code == code).order_by(ForeignFlow.date.desc()).limit(days)
    ))
    prices = {p.date: p for p in session.scalars(
        select(DailyPrice).where(DailyPrice.code == code, DailyPrice.date.in_([f.date for f in flows]))
    )}
    history = []
    for f in reversed(flows):
        p = prices.get(f.date)
        history.append({
            "date": f.date.isoformat(), "net_lots": round(f.net_shares / 1000),
            "close": p.close if p else None,
        })
    return {"code": st.code, "name": st.name, "market": st.market, "history": history}


def get_backtest(session: Session, limit_details: int = 300) -> dict:
    from .backtest import compute_backtest
    bt = compute_backtest(session)
    bt["details"] = bt["details"][:limit_details]
    return bt


def get_scores(session: Session, d: date) -> dict[str, int]:
    """所有有外資資料的個股之推薦指數（無新聞者新聞分以 0 計）。"""
    cfg = settings.scoring
    news = compute_news_metrics(session, d)
    return {
        code: to_index(cfg.weight_foreign * fm.score + cfg.weight_news * (news[code].score if code in news else 0.0))
        for code, fm in compute_foreign_metrics(session, d, cfg).items()
    }


def get_alerts(session: Session, d: date) -> dict:
    """清單追蹤的停損停利提醒（d 日新觸發 + 接近中）與目前持有中的虛擬部位。"""
    positions = track_positions(session, d)
    open_pos = [asdict(p) for p in positions if p.status == "open"]
    open_pos.sort(key=lambda p: p["ret"] or 0, reverse=True)
    night = night_info(session, d)
    triggered = {p.code for p in positions if p.close_date == d.isoformat()}
    gaps = gap_alerts([p for p in open_pos if p["code"] not in triggered], night)
    return {"alerts": alerts_for(positions, d), "open": open_pos, "gap_alerts": gaps}


def get_portfolio(session: Session, d: date, holdings_text: str | None = None) -> dict:
    from .portfolio import portfolio
    from .risk import load_holdings, parse_holdings
    holdings = parse_holdings(holdings_text) if holdings_text is not None else load_holdings()
    return portfolio(session, d, holdings)
