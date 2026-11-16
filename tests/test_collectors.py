"""解析器測試（離線）。fixture 依官方 JSON 結構撰寫；若官方改版導致實際抓取失敗，請先更新這裡。"""
from datetime import datetime

from app.collectors import foreign, news, prices

TWSE_T38U = {
    "stat": "OK", "date": "20261113", "title": "115年11月13日 外資及陸資買賣超彙總表",
    "fields": ["", "證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
               "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數",
               "外資自營商買賣超股數", "買進股數", "賣出股數", "買賣超股數"],
    "data": [
        [" ", "2330", "台積電", "20,000,000", "13,800,000", "6,200,000", "0", "0", "0", "20,000,000", "13,800,000", "6,200,000"],
        [" ", "0050", "元大台灣50", "1,000", "0", "1,000", "0", "0", "0", "1,000", "0", "1,000"],
        [" ", "2002", "中鋼", "1,000,000", "13,000,000", "-12,000,000", "0", "0", "0", "1,000,000", "13,000,000", "-12,000,000"],
        [" ", "03001P", "某權證", "1", "0", "1", "0", "0", "0", "1", "0", "1"],
    ],
}

TPEX_NEW = {"tables": [{
    "fields": ["代號", "名稱", "外資及陸資(不含外資自營商)-買進股數", "外資及陸資(不含外資自營商)-賣出股數",
               "外資及陸資(不含外資自營商)-買賣超股數", "外資自營商-買進股數", "外資自營商-賣出股數",
               "外資自營商-買賣超股數", "投信-買進股數"],
    "data": [["8069", "元太", "3,000,000", "500,000", "2,500,000", "0", "0", "0", "0"]],
}]}

TPEX_LEGACY = {"aaData": [["6488", "環球晶", "100,000", "1,400,000", "-1,300,000", "0", "0", "0"]]}


def test_parse_twse_uses_field_names_and_filters():
    rows = foreign.parse_twse(TWSE_T38U)
    assert [r.code for r in rows] == ["2330", "2002"]  # ETF、權證被濾掉
    tsmc = rows[0]
    assert (tsmc.name, tsmc.buy_shares, tsmc.sell_shares, tsmc.net_shares) == ("台積電", 20_000_000, 13_800_000, 6_200_000)
    assert rows[1].net_shares == -12_000_000


def test_parse_twse_holiday():
    assert foreign.parse_twse({"stat": "很抱歉，沒有符合條件的資料!"}) == []


def test_parse_tpex_new_and_legacy():
    r = foreign.parse_tpex(TPEX_NEW)[0]
    assert (r.code, r.market, r.net_shares, r.buy_shares) == ("8069", "TPEX", 2_500_000, 3_000_000)
    r = foreign.parse_tpex(TPEX_LEGACY)[0]
    assert (r.code, r.net_shares) == ("6488", -1_300_000)


def test_parse_prices():
    payload = {"stat": "OK", "fields": ["證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
               "data": [["2330", "台積電", "28,000,000", "1", "1", "1", "1", "1,050.00"], ["9999", "X", "0", "0", "--", "--", "--", "--"]]}
    rows = prices.parse_twse(payload)
    assert rows[0].close == 1050.0 and rows[0].volume == 28_000_000
    assert rows[1].close is None


def test_parse_cnyes():
    payload = {"items": {"last_page": 1, "data": [{
        "newsId": 123, "title": "<b>台積電</b>營收創新高", "summary": "AI 需求強勁",
        "publishAt": int(datetime(2026, 11, 13, 1, 0).timestamp()),
        "market": [{"code": "2330", "name": "台積電"}, {"code": "TSM", "name": "ADR"}],
    }]}}
    a = news.parse_cnyes(payload)[0]
    assert a.title == "台積電 營收創新高"
    assert a.tagged_codes == ["2330"]
    assert a.url.endswith("/123")


def test_parse_rss():
    xml = """<?xml version="1.0" encoding="UTF-8"?><rss><channel>
      <item><title><![CDATA[鴻海法說會報喜]]></title><link>https://money.udn.com/a/1</link>
      <description><![CDATA[<p>展望樂觀</p>]]></description><pubDate>Fri, 13 Nov 2026 08:30:00 +0800</pubDate></item>
    </channel></rss>"""
    a = news.parse_rss(xml, "經濟日報")[0]
    assert a.title == "鴻海法說會報喜" and a.summary == "展望樂觀"
    assert a.published_at == datetime(2026, 11, 13, 8, 30)


def test_parse_twse_tables_format():
    payload = {"stat": "OK", "tables": [{"title": "外資及陸資買賣超彙總表", "fields": TWSE_T38U["fields"], "data": TWSE_T38U["data"]}]}
    assert [r.code for r in foreign.parse_twse(payload)] == ["2330", "2002"]
