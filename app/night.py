"""夜盤（台指期盤後交易時段）納入推薦考量。

清單在 D 日收盤後產生；D 日 15:00 到 D+1 日 05:00 的台指期夜盤反映了收盤後的消息與美股走勢。
隔天早上（盤前）用夜盤漲跌：
  1. 盤前推薦指數 = to_index(綜合分數 + weight × tanh(夜盤漲跌幅 / scale))，最多約 ±7.5 分
     （同方向影響所有個股，不改變清單內排名，但會改變「強力關注／偏多…」的評等）
  2. 夜盤大漲／大跌（±1.5%）時附上進場提醒
  3. 用「昨收 ×（1 + 夜盤漲跌幅 × beta）」估算開盤價，預警可能跳空跌破停損或觸及停利的部位
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .collectors.night import NightQuote, fetch_latest_night
from .config import TZ, NightConfig, settings
from .db import NightSession
from .risk import index_label, to_index

log = logging.getLogger(__name__)


def save_night(session: Session, q: NightQuote) -> NightSession:
    row = session.scalar(select(NightSession).where(NightSession.ref_date == q.ref_date,
                                                    NightSession.contract == q.contract))
    if row is None:
        row = NightSession(ref_date=q.ref_date, contract=q.contract)
        session.add(row)
    row.night_date, row.month, row.close, row.ref_close, row.volume = q.night_date, q.month, q.close, q.ref_close, q.volume
    row.fetched_at = datetime.now(TZ).replace(tzinfo=None)
    session.commit()
    return row


def update_night(session: Session, today: date) -> dict | None:
    cfg = settings.night
    try:
        q = fetch_latest_night(today, cfg.contract)
    except Exception as exc:  # noqa: BLE001 - 夜盤失敗不影響推播
        log.error("抓取夜盤失敗：%s", exc)
        return None
    if q is None:
        log.warning("查無夜盤資料")
        return None
    save_night(session, q)
    log.info("夜盤 %s %s：%.0f（日盤 %s 收 %.0f，%+.2f%%）", q.contract, q.month, q.close, q.ref_date, q.ref_close,
             q.change_pct * 100)
    return night_info(session, q.ref_date)


def night_score(change: float, cfg: NightConfig | None = None) -> float:
    cfg = cfg or settings.night
    return math.tanh(change / cfg.scale)


def night_notes(change: float, cfg: NightConfig | None = None) -> list[str]:
    cfg = cfg or settings.night
    if change <= -cfg.big_move:
        return ["夜盤大跌：買入名單建議等開盤後走勢穩定再分批進場，或降低部位", "持股留意開低跳空跌破停損"]
    if change >= cfg.big_move:
        return ["夜盤大漲：留意開高追價風險，可等拉回再進場", "賣出／避開名單可能先反彈，可趁反彈減碼"]
    if change <= -cfg.big_move / 2:
        return ["夜盤偏弱，進場宜保守"]
    if change >= cfg.big_move / 2:
        return ["夜盤偏強，盤勢有利多方"]
    return ["夜盤波動不大，依原名單操作"]


def night_info(session: Session, list_date: date, cfg: NightConfig | None = None) -> dict | None:
    """清單日 list_date 收盤後的那一場夜盤。"""
    cfg = cfg or settings.night
    row = session.scalar(select(NightSession).where(NightSession.ref_date == list_date,
                                                    NightSession.contract == cfg.contract))
    if row is None:
        return None
    change = row.close / row.ref_close - 1
    return {
        "contract": row.contract, "month": row.month, "night_date": row.night_date.isoformat(),
        "ref_date": row.ref_date.isoformat(), "close": row.close, "ref_close": row.ref_close,
        "change": round(row.close - row.ref_close, 2), "change_pct": round(change, 5),
        "score": round(night_score(change, cfg), 4), "notes": night_notes(change, cfg),
        "fetched_at": row.fetched_at.isoformat(timespec="minutes"),
    }


def apply_pre_index(rows: list[dict], night: dict | None, cfg: NightConfig | None = None) -> None:
    """在清單資料上加上 pre_index / pre_label（盤前推薦指數）。"""
    cfg = cfg or settings.night
    for r in rows:
        if night is None:
            r["pre_index"] = r["pre_label"] = None
            continue
        idx = to_index(r["total_score"] + cfg.weight * night["score"])
        r["pre_index"], r["pre_label"] = idx, index_label(idx)


def gap_alerts(items: list[dict], night: dict | None, cfg: NightConfig | None = None) -> list[dict]:
    """items: 含 code/name/last_close/stop/take 的部位（清單追蹤或持股）。回傳可能跳空觸價的預警。"""
    cfg = cfg or settings.night
    if not night:
        return []
    out = []
    for it in items:
        price = it.get("last_close") or it.get("close")
        if not price:
            continue
        est = round(price * (1 + night["change_pct"] * cfg.beta), 2)
        if est <= it["stop"] < price:
            out.append({**it, "est_open": est, "kind": "gap_stop", "text": "預估開盤可能跌破停損"})
        elif price < it["take"] <= est:
            out.append({**it, "est_open": est, "kind": "gap_take", "text": "預估開盤可能觸及停利"})
    return out
