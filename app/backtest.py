"""回測：每份清單出現後 N 個交易日的實際報酬。

定義（請注意限制）：
- 報酬 = 第 D+N 個交易日收盤價 / 清單日 D 收盤價 − 1（收盤對收盤）。
  清單在收盤後才產生，實際上最快隔天才能進場，因此實際績效會與此不同。
- 未還原除權息：除息日附近的報酬會被低估。
- 基準 = 同期間所有有收盤價個股的等權平均報酬；超額報酬 = 個股報酬 − 基準。
- 買入清單的「勝率」= 報酬 > 0 的比例；賣出清單的「勝率」= 報酬 < 0 的比例（避開成功）。
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import DailyPrice, ForeignFlow, Recommendation, Stock

HORIZONS = (1, 5, 20)


def _summary(values: list[float], excess: list[float], side: str) -> dict:
    if not values:
        return {"n": 0, "avg": None, "median": None, "win_rate": None, "avg_excess": None}
    wins = sum(1 for v in values if (v > 0 if side == "buy" else v < 0))
    return {
        "n": len(values),
        "avg": round(statistics.fmean(values), 5),
        "median": round(statistics.median(values), 5),
        "win_rate": round(wins / len(values), 4),
        "avg_excess": round(statistics.fmean(excess), 5) if excess else None,
    }


def compute_backtest(session: Session, horizons: tuple[int, ...] = HORIZONS, since: date | None = None) -> dict:
    # 交易日曆：以外資資料的日期為準（比股價資料更完整）
    calendar = sorted(set(session.scalars(select(ForeignFlow.date).distinct())))
    pos = {d: i for i, d in enumerate(calendar)}
    q = select(Recommendation)
    if since:
        q = q.where(Recommendation.date >= since)
    recs = [r for r in session.scalars(q) if r.date in pos]

    # 只載入需要的日期的股價
    need: set[date] = set()
    for r in recs:
        i = pos[r.date]
        need.add(r.date)
        need.update(calendar[i + h] for h in horizons if i + h < len(calendar))
    closes: dict[date, dict[str, float]] = defaultdict(dict)
    if need:
        for p in session.scalars(select(DailyPrice).where(DailyPrice.date.in_(need), DailyPrice.close.is_not(None))):
            if p.close > 0:
                closes[p.date][p.code] = p.close

    bench_cache: dict[tuple[date, date], float | None] = {}

    def benchmark(d0: date, d1: date) -> float | None:
        if (d0, d1) not in bench_cache:
            a, b = closes.get(d0, {}), closes.get(d1, {})
            rets = [b[c] / a[c] - 1 for c in a.keys() & b.keys()]
            bench_cache[(d0, d1)] = statistics.fmean(rets) if rets else None
        return bench_cache[(d0, d1)]

    names = {s.code: s.name for s in session.scalars(select(Stock))}
    details = []
    buckets: dict[tuple[str, int], tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for r in sorted(recs, key=lambda x: (x.date, x.side, x.rank), reverse=True):
        base = closes.get(r.date, {}).get(r.code)
        row = {"date": r.date.isoformat(), "code": r.code, "name": names.get(r.code, r.code), "side": r.side,
               "rank": r.rank, "total_score": r.total_score, "entry": base}
        for h in horizons:
            i = pos[r.date] + h
            ret = exc = None
            if base and i < len(calendar):
                d1 = calendar[i]
                after = closes.get(d1, {}).get(r.code)
                if after:
                    ret = after / base - 1
                    bm = benchmark(r.date, d1)
                    exc = ret - bm if bm is not None else None
                    vals, excs = buckets[(r.side, h)]
                    vals.append(ret)
                    if exc is not None:
                        excs.append(exc)
            row[f"r{h}"] = round(ret, 5) if ret is not None else None
            row[f"x{h}"] = round(exc, 5) if exc is not None else None
        details.append(row)

    summary = {
        side: {str(h): _summary(*buckets[(side, h)], side) for h in horizons} for side in ("buy", "sell")
    }
    rec_dates = sorted({r.date for r in recs})
    return {
        "horizons": list(horizons),
        "period": [rec_dates[0].isoformat(), rec_dates[-1].isoformat()] if rec_dates else None,
        "list_days": len(rec_dates),
        "summary": summary,
        "details": details,
        "method": "收盤對收盤報酬（清單日收盤 → 第 N 個交易日收盤），未還原除權息；基準為全市場等權平均。",
    }


def fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"
