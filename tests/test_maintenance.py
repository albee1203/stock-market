from datetime import date, datetime, timedelta

from app.collectors.prices import PriceRow, keep_price
from app.db import DailyPrice, ForeignFlow, NewsArticle
from app.maintenance import compact
from app.pipeline import save_prices


def test_keep_price_codes():
    assert keep_price("2330") and keep_price("0050") and keep_price("00878") and keep_price("00632R")
    assert not keep_price("030001") and not keep_price("01001T") and not keep_price("7000AB")


def test_save_prices_skips_warrants(session):
    save_prices(session, date(2026, 11, 20), [PriceRow("2330", 1000, 1), PriceRow("030001", 1, 1), PriceRow("00878", 20, 1)])
    assert sorted(p.code for p in session.query(DailyPrice)) == ["00878", "2330"]


def test_compact_removes_junk_and_old_rows(session):
    today = date(2026, 11, 26)
    old = today - timedelta(days=600)
    session.add_all([
        DailyPrice(date=today, code="2330", close=1000, volume=1),
        DailyPrice(date=today, code="030001", close=1, volume=1),
        DailyPrice(date=old, code="2330", close=500, volume=1),
        ForeignFlow(date=old, code="2330", market="TWSE", buy_shares=0, sell_shares=0, net_shares=1),
        NewsArticle(source="x", url="https://e/1", title="舊", summary="", published_at=datetime(2026, 1, 1)),
        NewsArticle(source="x", url="https://e/2", title="新", summary="", published_at=datetime(2026, 11, 25)),
    ])
    session.commit()
    removed = compact(session, today, vacuum=False)
    assert removed == {"權證等股價": 1, "過舊股價": 1, "過舊外資": 1, "過舊新聞": 1}
    assert [(p.code, p.date) for p in session.query(DailyPrice)] == [("2330", today)]
    assert [n.title for n in session.query(NewsArticle)] == ["新"]
