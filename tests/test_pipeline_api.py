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
