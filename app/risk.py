"""推薦指數（0~100）與停損／停利。

推薦指數 = round((綜合分數 + 1) × 50)：50 為中性，越高越偏多，越低越偏空。

停損停利（以收盤價判斷）：
  停損幅度 = clamp(2 × 近 20 日日報酬標準差, 4%, 10%)；資料不足時 7%
  停利幅度 = 停損幅度 × 2（風險報酬比 1:2）
清單追蹤：個股進入「建議買入」當天以收盤價為成本建立虛擬部位，之後每天檢查：
  收盤 ≥ 停利價 → 停利；收盤 ≤ 停損價 → 停損；持有滿 20 個交易日 → 到期出場。
"""
from __future__ import annotations

import math
import os
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROOT, RiskConfig, settings
from .db import DailyPrice, ForeignFlow, Recommendation, Stock

# ---------- 推薦指數 ----------

LEVELS = [(80, "強力關注"), (65, "偏多"), (36, "中性"), (21, "偏空"), (0, "強烈避開")]


def to_index(total: float) -> int:
    return max(0, min(100, round((total + 1) * 50)))


def index_label(idx: int) -> str:
    return next(label for th, label in LEVELS if idx >= th)


# ---------- 停損停利價位 ----------

def daily_sigma(closes: list[float | None]) -> float | None:
    """closes 由舊到新；回傳日報酬標準差，資料少於 10 筆報酬時回傳 None。"""
    rets = [b / a - 1 for a, b in zip(closes, closes[1:]) if a and b]
    return statistics.stdev(rets) if len(rets) >= 10 else None


@dataclass
class Levels:
    entry: float
    stop: float
    take: float
    stop_pct: float
    take_pct: float
    sigma: float | None


def calc_levels(entry: float, sigma: float | None, cfg: RiskConfig | None = None,
                stop_pct: float | None = None, take_pct: float | None = None) -> Levels:
    cfg = cfg or settings.risk
    if stop_pct is None:
        stop_pct = cfg.default_sl if sigma is None else min(cfg.sl_max, max(cfg.sl_min, cfg.sl_sigma_mult * sigma))
    if take_pct is None:
        take_pct = stop_pct * cfg.reward_ratio
    return Levels(entry=entry, stop=round(entry * (1 - stop_pct), 2), take=round(entry * (1 + take_pct), 2),
                  stop_pct=round(stop_pct, 4), take_pct=round(take_pct, 4),
                  sigma=round(sigma, 5) if sigma is not None else None)


def _calendar(session: Session, until: date) -> list[date]:
    return sorted(session.scalars(select(ForeignFlow.date).where(ForeignFlow.date <= until).distinct()))


def _closes(session: Session, codes: set[str] | None, since: date, until: date) -> dict[str, dict[date, float]]:
    q = select(DailyPrice).where(DailyPrice.date.between(since, until), DailyPrice.close.is_not(None))
    if codes is not None:
        q = q.where(DailyPrice.code.in_(codes))
    out: dict[str, dict[date, float]] = defaultdict(dict)
    for p in session.scalars(q):
        if p.close and p.close > 0:
            out[p.code][p.date] = p.close
    return out


def levels_for(session: Session, codes: set[str] | None, d: date, cfg: RiskConfig | None = None) -> dict[str, Levels]:
    """以 d 日收盤價為基準，計算各股停損停利價。codes=None 代表全部。"""
    cfg = cfg or settings.risk
    cal = _calendar(session, d)
    window = cal[-(cfg.vol_window + 1):]
    if not window:
        return {}
    closes = _closes(session, codes, window[0], d)
    out = {}
    for code, by_date in closes.items():
        if d not in by_date:
            continue
        out[code] = calc_levels(by_date[d], daily_sigma([by_date.get(x) for x in window]), cfg)
    return out


# ---------- 清單追蹤（虛擬部位） ----------

STATUS_TEXT = {"open": "持有中", "take": "觸及停利", "stop": "觸及停損", "expired": "到期出場"}


@dataclass
class Position:
    code: str
    name: str
    open_date: str
    entry: float
    stop: float
    take: float
    stop_pct: float
    take_pct: float
    status: str = "open"
    close_date: str | None = None
    exit_price: float | None = None
    last_date: str | None = None
    last_close: float | None = None
    ret: float | None = None       # 已出場＝實現報酬；持有中＝目前報酬
    days_held: int = 0
    near: str | None = None        # 持有中且接近停損／停利："stop" / "take"


def track_positions(session: Session, until: date, cfg: RiskConfig | None = None) -> list[Position]:
    cfg = cfg or settings.risk
    cal = _calendar(session, until)
    if not cal:
        return []
    recs = list(session.scalars(select(Recommendation).where(Recommendation.side == "buy", Recommendation.date <= until)))
    if not recs:
        return []
    rec_dates: dict[str, set[date]] = defaultdict(set)
    for r in recs:
        rec_dates[r.code].add(r.date)
    first = min(r.date for r in recs)
    pos_idx = {d: i for i, d in enumerate(cal)}
    start_i = max(0, pos_idx.get(first, 0) - cfg.vol_window - 1)
    closes = _closes(session, set(rec_dates), cal[start_i], until)
    names = {s.code: s.name for s in session.scalars(select(Stock).where(Stock.code.in_(list(rec_dates))))}

    out: list[Position] = []
    for code, dates in rec_dates.items():
        px = closes.get(code, {})
        cur: Position | None = None
        for i in range(pos_idx.get(min(dates), 0), len(cal)):
            t = cal[i]
            c = px.get(t)
            if cur is not None and c is not None:
                cur.days_held += 1
                cur.last_date, cur.last_close = t.isoformat(), c
                cur.ret = round(c / cur.entry - 1, 5)
                status = "take" if c >= cur.take else "stop" if c <= cur.stop else \
                    "expired" if cur.days_held >= cfg.hold_days else None
                if status:
                    cur.status, cur.close_date, cur.exit_price = status, t.isoformat(), c
                    out.append(cur)
                    cur = None
            if cur is None and t in dates and c is not None:
                window = [px.get(x) for x in cal[max(0, i - cfg.vol_window):i + 1]]
                lv = calc_levels(c, daily_sigma(window), cfg)
                cur = Position(code=code, name=names.get(code, code), open_date=t.isoformat(), entry=c,
                               stop=lv.stop, take=lv.take, stop_pct=lv.stop_pct, take_pct=lv.take_pct,
                               last_date=t.isoformat(), last_close=c, ret=0.0)
        if cur is not None:
            if cur.last_close and cur.last_close <= cur.stop * (1 + cfg.near_pct):
                cur.near = "stop"
            elif cur.last_close and cur.last_close >= cur.take * (1 - cfg.near_pct):
                cur.near = "take"
            out.append(cur)
    return out


def strategy_summary(positions: list[Position]) -> dict:
    closed = [p for p in positions if p.status != "open"]
    rets = [p.ret for p in closed if p.ret is not None]
    return {
        "closed": len(closed),
        "open": sum(1 for p in positions if p.status == "open"),
        "take": sum(1 for p in closed if p.status == "take"),
        "stop": sum(1 for p in closed if p.status == "stop"),
        "expired": sum(1 for p in closed if p.status == "expired"),
        "avg": round(statistics.fmean(rets), 5) if rets else None,
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4) if rets else None,
        "avg_days": round(statistics.fmean(p.days_held for p in closed), 1) if closed else None,
    }


def alerts_for(positions: list[Position], d: date) -> list[dict]:
    """d 日新觸發的停損／停利／到期，以及接近停損／停利的持有中部位。"""
    ds = d.isoformat()
    out = []
    for p in positions:
        if p.close_date == ds:
            out.append({**asdict(p), "kind": p.status, "text": STATUS_TEXT[p.status]})
        elif p.status == "open" and p.near and p.last_date == ds:
            out.append({**asdict(p), "kind": f"near_{p.near}", "text": "接近停損" if p.near == "stop" else "接近停利"})
    order = {"stop": 0, "near_stop": 1, "take": 2, "near_take": 3, "expired": 4}
    return sorted(out, key=lambda a: order.get(a["kind"], 9))


# ---------- 我的持股（推播用；App 內的持股存在手機本機） ----------

_KEYS = {"股數": "shares", "shares": "shares", "張": "lots", "lots": "lots", "停損": "stop_pct", "sl": "stop_pct",
         "停利": "take_pct", "tp": "take_pct", "日期": "date", "date": "date"}


def parse_holdings(text: str) -> list[dict]:
    """每行一檔：代號,成本[,停損%,停利%][,股數=1000][,日期=2026-11-01]；# 之後為註解，多筆可用分號隔開。

    例：「2330,980」「2317,190,8,20」「2454,1200,股數=2000」「2603,180,張=3,日期=2026-10-20」
    """
    out = []
    for line in text.replace("；", "\n").replace(";", "\n").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in line.replace("，", ",").split(",")]
        try:
            h: dict = {"code": parts[0], "cost": float(parts[1])}
            positional = []
            for p in parts[2:]:
                if not p:
                    positional.append(None)
                    continue
                if "=" in p:
                    k, v = (x.strip() for x in p.split("=", 1))
                    key = _KEYS.get(k.lower(), _KEYS.get(k))
                    if key == "shares":
                        h["shares"] = int(float(v))
                    elif key == "lots":
                        h["shares"] = int(float(v) * 1000)
                    elif key in ("stop_pct", "take_pct"):
                        h[key] = float(v) / 100
                    elif key == "date":
                        h["date"] = v
                else:
                    positional.append(p)
            if len(positional) > 0 and positional[0]:
                h.setdefault("stop_pct", float(positional[0]) / 100)
            if len(positional) > 1 and positional[1]:
                h.setdefault("take_pct", float(positional[1]) / 100)
            out.append(h)
        except (IndexError, ValueError):
            continue
    return out


def load_holdings() -> list[dict]:
    text = os.getenv("HOLDINGS", "")
    f = ROOT / "holdings.csv"
    if not text and f.exists():
        text = f.read_text(encoding="utf-8")
    return parse_holdings(text)


def check_holdings(session: Session, d: date, holdings: list[dict], cfg: RiskConfig | None = None) -> list[dict]:
    cfg = cfg or settings.risk
    if not holdings:
        return []
    lv = levels_for(session, {h["code"] for h in holdings}, d, cfg)
    names = {s.code: s.name for s in session.scalars(select(Stock))}
    out = []
    for h in holdings:
        cur = lv.get(h["code"])
        if cur is None:
            continue
        levels = calc_levels(h["cost"], cur.sigma, cfg, h.get("stop_pct"), h.get("take_pct"))
        price = cur.entry
        if price <= levels.stop:
            kind, text = "stop", "已跌破停損價"
        elif price >= levels.take:
            kind, text = "take", "已達停利價"
        elif price <= levels.stop * (1 + cfg.near_pct):
            kind, text = "near_stop", "接近停損價"
        elif price >= levels.take * (1 - cfg.near_pct):
            kind, text = "near_take", "接近停利價"
        else:
            kind, text = "ok", "正常"
        out.append({"code": h["code"], "name": names.get(h["code"], h["code"]), "cost": h["cost"], "close": price,
                    "shares": h.get("shares"), "date": h.get("date"),
                    "ret": round(price / h["cost"] - 1, 5), "stop": levels.stop, "take": levels.take,
                    "kind": kind, "text": text})
    return out


def fmt_price(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".") if not math.isnan(x) else "—"
