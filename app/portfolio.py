"""個人持股追蹤：損益、今日漲跌、停損停利狀態，以及籌碼／消息訊號。

持股來源：
- 推播／API：環境變數 HOLDINGS 或本機 holdings.csv（格式見 risk.parse_holdings）
- 手機 App：存在裝置本機（localStorage），由前端用同樣的邏輯計算
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis.scoring import compute_foreign_metrics, compute_news_metrics
from .config import settings
from .db import DailyPrice, ForeignFlow, Recommendation
from .risk import check_holdings, index_label, to_index


def _prev_close(session: Session, codes: set[str], d: date) -> dict[str, float]:
    prev_day = session.scalar(select(ForeignFlow.date).where(ForeignFlow.date < d)
                              .order_by(ForeignFlow.date.desc()).limit(1))
    if prev_day is None:
        return {}
    return {p.code: p.close for p in session.scalars(
        select(DailyPrice).where(DailyPrice.date == prev_day, DailyPrice.code.in_(codes))) if p.close}


def signals_for(code: str, fm, nm, rec_side: str | None) -> list[dict]:
    """回傳 [{text, tone}]，tone：up（偏多）/ down（偏空）/ ""。"""
    out = []
    if rec_side == "sell":
        out.append({"text": "列入賣出／避開名單", "tone": "down"})
    elif rec_side == "buy":
        out.append({"text": "列入建議買入名單", "tone": "up"})
    if fm is not None:
        if fm.streak <= -3:
            out.append({"text": f"外資連賣 {-fm.streak} 日", "tone": "down"})
        elif fm.streak >= 3:
            out.append({"text": f"外資連買 {fm.streak} 日", "tone": "up"})
        if "單日大賣" in fm.flags:
            out.append({"text": "外資單日大賣", "tone": "down"})
        elif "單日大買" in fm.flags:
            out.append({"text": "外資單日大買", "tone": "up"})
    if nm is not None:
        if nm.bearish:
            out.append({"text": f"利空新聞 {nm.bearish} 則", "tone": "down"})
        if nm.bullish:
            out.append({"text": f"利多新聞 {nm.bullish} 則", "tone": "up"})
    return out


def portfolio(session: Session, d: date, holdings: list[dict]) -> dict:
    items = check_holdings(session, d, holdings)
    if not items:
        return {"date": d.isoformat(), "items": [], "totals": None}
    codes = {i["code"] for i in items}
    prev = _prev_close(session, codes, d)
    fms = compute_foreign_metrics(session, d)
    nms = compute_news_metrics(session, d)
    rec_side = {r.code: r.side for r in session.scalars(
        select(Recommendation).where(Recommendation.date == d, Recommendation.code.in_(codes)))}
    cfg = settings.scoring

    tot_cost = tot_value = tot_day = 0.0
    priced = 0
    for it in items:
        code = it["code"]
        fm, nm = fms.get(code), nms.get(code)
        total = cfg.weight_foreign * (fm.score if fm else 0.0) + cfg.weight_news * (nm.score if nm else 0.0)
        it["index"] = to_index(total)
        it["label"] = index_label(it["index"])
        pc = prev.get(code)
        it["prev_close"] = pc
        it["day_change"] = round(it["close"] / pc - 1, 5) if pc else None
        it["streak"] = fm.streak if fm else 0
        it["signals"] = signals_for(code, fm, nm, rec_side.get(code))
        sh = it.get("shares")
        if sh:
            it["cost_value"] = round(sh * it["cost"])
            it["market_value"] = round(sh * it["close"])
            it["pnl"] = it["market_value"] - it["cost_value"]
            it["day_pnl"] = round(sh * (it["close"] - pc)) if pc else None
            tot_cost += it["cost_value"]
            tot_value += it["market_value"]
            tot_day += it["day_pnl"] or 0
            priced += 1
    # 需要注意的排前面：停損 > 接近停損 > 賣出名單／偏空訊號 > 其他
    rank = {"stop": 0, "near_stop": 1, "take": 2, "near_take": 3, "ok": 5}

    def key(i):
        bad = any(s["tone"] == "down" for s in i["signals"])
        return (rank.get(i["kind"], 5) - (1 if bad and i["kind"] == "ok" else 0), i["ret"])

    items.sort(key=key)
    totals = None
    if priced:
        totals = {
            "count": len(items), "priced": priced, "cost": round(tot_cost), "value": round(tot_value),
            "pnl": round(tot_value - tot_cost), "pnl_pct": round(tot_value / tot_cost - 1, 5) if tot_cost else None,
            "day_pnl": round(tot_day),
        }
    return {"date": d.isoformat(), "items": items, "totals": totals}
