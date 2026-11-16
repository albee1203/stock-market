from datetime import date

import pytest

from app.demo import seed_demo
from app.portfolio import portfolio
from app.risk import parse_holdings

D = date(2026, 11, 13)


def test_parse_holdings_keywords():
    hs = parse_holdings("2330,980,股數=2000\n2603,180,張=3,日期=2026-10-20;2002,25,,10,shares=5000\n2317,190,8,20")
    assert hs[0] == {"code": "2330", "cost": 980.0, "shares": 2000}
    assert hs[1] == {"code": "2603", "cost": 180.0, "shares": 3000, "date": "2026-10-20"}
    assert hs[2] == {"code": "2002", "cost": 25.0, "shares": 5000, "take_pct": 0.10}
    assert hs[3] == {"code": "2317", "cost": 190.0, "stop_pct": 0.08, "take_pct": 0.20}


def test_portfolio_totals_signals_and_order(session):
    seed_demo(session, D)
    pf = portfolio(session, D, parse_holdings("2330,99999,股數=1000\n2603,100,股數=2000\n8069,240"))
    items = {i["code"]: i for i in pf["items"]}
    assert pf["items"][0]["code"] == "2330" and items["2330"]["kind"] == "stop"   # 跌破停損排最前
    t = pf["totals"]
    assert t["count"] == 3 and t["priced"] == 2
    assert t["cost"] == 99999 * 1000 + 100 * 2000
    assert t["pnl"] == t["value"] - t["cost"]
    long_ = items["2603"]
    assert long_["pnl"] == round(2000 * long_["close"]) - 200000
    assert long_["day_change"] == pytest.approx(long_["close"] / long_["prev_close"] - 1, abs=1e-5)
    texts = [s["text"] for s in long_["signals"]]
    assert "列入賣出／避開名單" in texts and any(t.startswith("外資連賣") for t in texts)
    assert items["8069"].get("pnl") is None and 0 <= items["8069"]["index"] <= 100


def test_empty_portfolio(session):
    seed_demo(session, D)
    assert portfolio(session, D, [])["items"] == []


def test_portfolio_in_message_and_api(session, monkeypatch):
    from fastapi.testclient import TestClient

    from app import notify as nt
    from app.api import app
    monkeypatch.setenv("HOLDINGS", "2603,100,股數=2000")
    _, text = nt.build_message(session, seed_demo(session, D), "")
    assert "💼 我的持股" in text and "長榮 2603" in text and "總損益" in text
    r = TestClient(app).get("/api/portfolio", params={"holdings": "2330,1000,股數=1000"}).json()
    assert r["items"][0]["code"] == "2330" and r["totals"]["count"] == 1
