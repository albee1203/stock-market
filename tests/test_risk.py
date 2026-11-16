from datetime import date, timedelta

import pytest

from app.config import RiskConfig
from app.db import DailyPrice, ForeignFlow, Recommendation, Stock
from app.risk import (alerts_for, calc_levels, check_holdings, daily_sigma, index_label, parse_holdings,
                      strategy_summary, to_index, track_positions)

CFG = RiskConfig()
DAYS = [date(2026, 9, 1) + timedelta(days=i) for i in range(60)]


def test_index_scale_and_labels():
    assert to_index(1.0) == 100 and to_index(-1.0) == 0 and to_index(0) == 50
    assert to_index(1.7) == 100  # 裁切
    assert index_label(95) == "強力關注" and index_label(70) == "偏多" and index_label(50) == "中性"
    assert index_label(30) == "偏空" and index_label(5) == "強烈避開"


def test_levels_clamped_by_volatility():
    assert daily_sigma([100.0] * 5) is None  # 資料不足
    lv = calc_levels(100, None, CFG)
    assert (lv.stop, lv.take) == (93.0, 114.0)  # 預設 7% / 14%
    assert calc_levels(100, 0.005, CFG).stop_pct == 0.04  # 低波動 → 下限 4%
    assert calc_levels(100, 0.2, CFG).stop_pct == 0.10    # 高波動 → 上限 10%
    lv = calc_levels(100, 0.03, CFG)
    assert lv.stop_pct == pytest.approx(0.06) and lv.take_pct == pytest.approx(0.12)
    assert calc_levels(100, 0.03, CFG, stop_pct=0.05, take_pct=0.2).take == 120.0


def test_parse_holdings():
    hs = parse_holdings("2330,980\n# 註解\n2317, 190, 8, 20\n壞掉的行\n2002，25")
    assert hs == [{"code": "2330", "cost": 980.0},
                  {"code": "2317", "cost": 190.0, "stop_pct": 0.08, "take_pct": 0.20},
                  {"code": "2002", "cost": 25.0}]


def _seed(session, paths: dict[str, list[float]], buy_days: dict[str, list[int]]):
    for code in paths:
        session.add(Stock(code=code, name=f"股{code}", market="TWSE"))
    for i, d in enumerate(DAYS):
        for code, ps in paths.items():
            session.add(ForeignFlow(date=d, code=code, market="TWSE", buy_shares=0, sell_shares=0, net_shares=1))
            session.add(DailyPrice(date=d, code=code, close=ps[i], volume=1))
    for code, idxs in buy_days.items():
        for i in idxs:
            session.add(Recommendation(date=DAYS[i], code=code, side="buy", rank=1, total_score=0.8,
                                       foreign_score=0.8, news_score=0.8, streak=3, net_shares=1, net_amount=None,
                                       news_count=1, keywords="[]", reason=""))
    session.commit()


def test_track_take_stop_expire_and_reopen(session):
    flat = [100.0] * 25
    up = flat + [100 + 2 * k for k in range(1, 36)]           # 第 25 天後穩定上漲 → 停利（預設 14%）
    down = flat + [100 - 2 * k for k in range(1, 36)]         # 下跌 → 停損（7%）
    side = [100.0] * 60                                       # 不動 → 20 日到期
    _seed(session, {"A": up, "B": down, "C": side}, {"A": [24, 25, 26, 40], "B": [24], "C": [24]})
    ps = track_positions(session, DAYS[-1], CFG)
    a = [p for p in ps if p.code == "A"]
    assert a[0].status == "take" and a[0].open_date == DAYS[24].isoformat()  # 連續上榜只算一次部位
    assert a[0].exit_price >= a[0].take
    assert a[1].open_date == DAYS[40].isoformat()                            # 出場後再上榜 → 新部位
    b = next(p for p in ps if p.code == "B")
    assert b.status == "stop" and b.ret < 0
    c = next(p for p in ps if p.code == "C")
    assert c.status == "expired" and c.days_held == 20
    s = strategy_summary(ps)
    assert s["take"] >= 1 and s["stop"] == 1 and s["expired"] == 1
    # 觸發當天會出現在提醒
    alerts = alerts_for(ps, date.fromisoformat(b.close_date))
    assert any(x["code"] == "B" and x["kind"] == "stop" for x in alerts)


def test_near_stop_alert(session):
    # 平盤 → 波動 0 → 停損取下限 4%（96）；97.5 在停損價 2% 以內 → 接近停損
    ps_ = [100.0] * 30 + [99.0, 98.0, 97.5] + [97.5] * 27
    _seed(session, {"A": ps_}, {"A": [29]})
    d = DAYS[32]
    pos = track_positions(session, d, CFG)
    assert pos[0].status == "open" and pos[0].near == "stop"
    assert alerts_for(pos, d)[0]["kind"] == "near_stop"


def test_check_holdings(session):
    _seed(session, {"A": [100.0] * 60, "B": [100.0] * 60}, {})
    res = {h["code"]: h for h in check_holdings(session, DAYS[-1], [
        {"code": "A", "cost": 110.0}, {"code": "B", "cost": 85.0}, {"code": "Z", "cost": 1.0}])}
    assert res["A"]["kind"] == "stop" and res["A"]["ret"] == pytest.approx(-0.0909, abs=1e-3)
    assert res["B"]["kind"] == "take"
    assert "Z" not in res
