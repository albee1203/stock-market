from datetime import date

from fastapi.testclient import TestClient

from app.demo import seed_demo
from app.api import app

D = date(2026, 11, 13)


def test_demo_recommendations(session):
    seed_demo(session, D)
    from app import services
    rec = services.get_recommendations(session, D)
    buy = {r["code"] for r in rec["buy"]}
    sell = {r["code"] for r in rec["sell"]}
    assert {"2330", "2317", "3231"} <= buy
    assert {"2603", "2002"} <= sell
    assert not buy & sell
    assert all(r["foreign_score"] > 0 and r["news_score"] > 0 for r in rec["buy"])
    tsmc = next(r for r in rec["buy"] if r["code"] == "2330")
    assert tsmc["streak"] >= 6 and f"外資連續買超 {tsmc['streak']} 日" in tsmc["reason"]


def test_api(session):
    seed_demo(session, D)
    c = TestClient(app)
    assert c.get("/api/health").json() == {"ok": True}
    assert c.get("/api/dates").json()[0] == "2026-11-13"
    rec = c.get("/api/recommendations").json()
    assert rec["date"] == "2026-11-13" and rec["buy"]
    assert c.get("/api/foreign", params={"side": "sell"}).json()[0]["net_shares"] < 0
    news = c.get("/api/news", params={"code": "2330"}).json()
    assert news and all(any(s["code"] == "2330" for s in a["stocks"]) for a in news)
    assert len(c.get("/api/stocks/2330").json()["history"]) == 30
    bt = c.get("/api/backtest").json()
    assert bt["summary"]["buy"]["5"]["n"] > 0
    assert c.get("/api/stocks/9999").status_code == 404


def test_backfill_refetches_days_missing_a_market(session, monkeypatch):
    from app import pipeline
    from app.collectors.foreign import FlowRow
    d0 = date(2026, 11, 12)
    pipeline.save_flows(session, d0, [FlowRow("6223", "旺矽", "TPEX", 0, 0, 1000)])  # 只有上櫃
    fetched = []
    monkeypatch.setattr(pipeline.foreign, "fetch_all", lambda d: fetched.append(d) or [
        FlowRow("2330", "台積電", "TWSE", 0, 0, 1000), FlowRow("6223", "旺矽", "TPEX", 0, 0, 1000)])
    monkeypatch.setattr(pipeline.prices, "fetch_all", lambda d: [])
    pipeline.ensure_flow_history(session, D, days=1, max_lookback=3)
    assert fetched == [d0]  # 11/12 缺上市 → 重新抓
