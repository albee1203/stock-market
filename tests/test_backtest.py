from datetime import date, timedelta

import pytest

from app.backtest import compute_backtest
from app.db import DailyPrice, ForeignFlow, Recommendation, Stock

DAYS = [date(2026, 10, 1) + timedelta(days=i) for i in range(30)]


def _seed(session, prices: dict[str, list[float]], recs: list[tuple[int, str, str]]):
    for code in prices:
        session.add(Stock(code=code, name=code, market="TWSE"))
    for i, d in enumerate(DAYS):
        for code, ps in prices.items():
            session.add(ForeignFlow(date=d, code=code, market="TWSE", buy_shares=0, sell_shares=0, net_shares=1))
            if ps[i] is not None:
                session.add(DailyPrice(date=d, code=code, close=ps[i], volume=1))
    for i, code, side in recs:
        session.add(Recommendation(date=DAYS[i], code=code, side=side, rank=1, total_score=0.5, foreign_score=0.5,
                                   news_score=0.5, streak=1, net_shares=1, net_amount=None, news_count=1,
                                   keywords="[]", reason=""))
    session.commit()


def test_returns_excess_and_win_rate(session):
    up = [100 * 1.01 ** i for i in range(30)]   # 每天 +1%
    flat = [100.0] * 30
    down = [100 * 0.99 ** i for i in range(30)]
    _seed(session, {"A": up, "B": flat, "C": down}, [(0, "A", "buy"), (0, "C", "sell"), (20, "A", "buy")])
    bt = compute_backtest(session)
    a0 = next(r for r in bt["details"] if r["code"] == "A" and r["date"] == "2026-10-01")
    assert a0["r1"] == pytest.approx(0.01, abs=1e-4)
    assert a0["r5"] == pytest.approx(1.01 ** 5 - 1, abs=1e-4)
    assert a0["r20"] == pytest.approx(1.01 ** 20 - 1, abs=1e-4)
    # 基準＝A、B、C 等權平均
    bench5 = ((1.01 ** 5 - 1) + 0 + (0.99 ** 5 - 1)) / 3
    assert a0["x5"] == pytest.approx(a0["r5"] - bench5, abs=1e-4)
    # 第 20 天的清單：20 日後超出資料範圍 → None，不計入統計
    a20 = next(r for r in bt["details"] if r["date"] == "2026-10-21")
    assert a20["r5"] is not None and a20["r20"] is None
    s = bt["summary"]
    assert s["buy"]["5"]["n"] == 2 and s["buy"]["20"]["n"] == 1 and s["buy"]["5"]["win_rate"] == 1.0
    assert s["sell"]["5"]["win_rate"] == 1.0  # 賣出清單下跌＝避開成功


def test_missing_price_is_skipped(session):
    ps = [100.0] * 30
    ps[5] = None
    _seed(session, {"A": ps}, [(0, "A", "buy")])
    row = compute_backtest(session)["details"][0]
    assert row["r1"] == 0.0 and row["r5"] is None


def test_empty(session):
    bt = compute_backtest(session)
    assert bt["list_days"] == 0 and bt["summary"]["buy"]["5"]["n"] == 0
