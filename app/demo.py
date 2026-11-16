"""離線示範資料：不需網路即可看到完整畫面（含回測）。所有數字皆為虛構，僅供測試介面與評分邏輯。"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .analysis.scoring import build_recommendations
from .collectors.foreign import FlowRow
from .collectors.news import RawArticle
from .collectors.prices import PriceRow
from .db import DailyPrice, ForeignFlow, NewsArticle, Recommendation
from .pipeline import analyze_articles, save_articles, save_flows, save_prices

# code, name, market, close, volume(股), 最近 6 日外資買賣超(張，由舊到新)
STOCKS = [
    ("2330", "台積電", "TWSE", 1050.0, 28_000_000, [1200, 3400, 2800, 5100, 4300, 6200]),
    ("2454", "聯發科", "TWSE", 1320.0, 6_500_000, [-300, 400, 900, 1500, 1100, 2100]),
    ("2317", "鴻海", "TWSE", 205.0, 52_000_000, [3000, -1500, 2200, 4100, 6800, 9200]),
    ("3231", "緯創", "TWSE", 118.0, 61_000_000, [800, 1200, 2600, 3900, 5200, 7800]),
    ("2603", "長榮", "TWSE", 210.0, 18_000_000, [-500, -1200, -2600, -3100, -4200, -5800]),
    ("2002", "中鋼", "TWSE", 22.5, 40_000_000, [-2000, -3500, -4100, -6200, -8800, -12000]),
    ("1301", "台塑", "TWSE", 38.0, 15_000_000, [500, -800, -1500, -2400, -3000, -4100]),
    ("3008", "大立光", "TWSE", 2400.0, 500_000, [50, 30, -20, -60, -110, -180]),
    ("8069", "元太", "TPEX", 260.0, 9_000_000, [400, 600, 900, 1300, 1700, 2500]),
    ("6488", "環球晶", "TPEX", 420.0, 3_000_000, [-200, -300, -500, -700, -900, -1300]),
    ("2412", "中華電", "TWSE", 128.0, 9_000_000, [300, -200, 100, -150, 80, 120]),
]

NEWS = [
    ("台積電(2330)10月營收創新高 外資調升目標價至1400元", "台積電公布10月營收創新高，年增逾4成，AI訂單滿載，多家外資調升目標價，看好明年獲利成長。"),
    ("鴻海AI伺服器出貨優於預期 法說會報喜", "鴻海(2317)表示AI伺服器需求強勁，第四季出貨優於預期，展望樂觀。"),
    ("緯創接單暢旺 獲美系大單 擴產因應", "緯創(3231)受惠AI伺服器大單，產能滿載，宣布擴產。"),
    ("聯發科天璣新晶片打入供應鏈 營收年增", "聯發科(2454)新款晶片打入國際大廠供應鏈，10月營收年增兩成。"),
    ("元太電子紙標籤需求回溫 營收成長", "元太(8069)電子紙標籤需求回溫，營收成長，法人看好。"),
    ("運價崩跌 長榮獲利衰退 外資下修目標價", "貨櫃運價持續崩跌，長榮(2603)第三季獲利衰退，外資下修目標價，展望保守。"),
    ("中鋼鋼價跌價 需求疲弱 第四季恐虧損", "中鋼(2002)宣布調降盤價，受中國供過於求影響，需求疲弱，第四季恐轉盈為虧。"),
    ("台塑化工報價疲弱 獲利衰退低於預期", "台塑(1301)第三季獲利低於預期，石化產品跌價，法人保守看待。"),
    ("環球晶客戶庫存調整 營收年減", "環球晶(6488)表示客戶庫存調整延續，10月營收年減，訂單減少。"),
    ("大立光新品遞延 法人保守看待", "大立光(3008)新品出貨遞延，法人保守看待短期營運。"),
    ("中華電穩健 配息創新高", "中華電(2412)營運穩健，配息創新高。"),
]


DEMO_DAYS = 30


def last_weekdays(end: date, n: int) -> list[date]:
    out, cur = [], end
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur -= timedelta(days=1)
    return list(reversed(out))


def _demo_series(rng: random.Random, lots: list[int], close: float, n: int) -> tuple[list[int], list[float]]:
    """產生 n 日虛構外資買賣超（最後 6 日用 STOCKS 中設定的值）與收盤價。"""
    trend = 1 if sum(lots) > 0 else -1
    head = [int(abs(lots[0]) * rng.uniform(0.3, 1.2)) * (trend if rng.random() < 0.6 else -trend)
            for _ in range(n - len(lots))]
    flows = head + lots
    prices, p = [], close
    for f in flows:  # 外資方向帶一點漂移，讓示範回測有東西可看
        p *= 1 + (0.004 if f > 0 else -0.004) + rng.gauss(0, 0.012)
        prices.append(round(p, 2))
    return flows, prices


def seed_demo(session: Session, end: date) -> date:
    days = last_weekdays(end, DEMO_DAYS)
    for model in (ForeignFlow, DailyPrice, Recommendation):
        session.execute(delete(model).where(model.date.in_(days)))
    session.execute(delete(NewsArticle).where(NewsArticle.source == "示範資料"))
    session.commit()

    rng = random.Random(42)
    series = {code: _demo_series(rng, lots, close, len(days)) for code, _, _, close, _, lots in STOCKS}
    for i, d in enumerate(days):
        save_flows(session, d, [FlowRow(code, name, mkt, 0, 0, series[code][0][i] * 1000)
                                for code, name, mkt, *_ in STOCKS])
        save_prices(session, d, [PriceRow(code, series[code][1][i], vol) for code, _, _, _, vol, _ in STOCKS])

    # 每天都放同一組示範新聞（網址不同），從第 6 天起每天產生清單
    for i, d in enumerate(days):
        base = datetime(d.year, d.month, d.day, 8, 0)
        arts = [RawArticle("示範資料", f"https://example.com/demo/{d:%Y%m%d}/{k}", t, s,
                           base + timedelta(minutes=17 * k)) for k, (t, s) in enumerate(NEWS)]
        saved = save_articles(session, arts)
        analyze_articles(session, saved, use_llm=False, reanalyze=True)
        if i >= 5:
            build_recommendations(session, d)
    # 示範夜盤：最後一天收盤後台指期夜盤下跌約 1.8%
    from .collectors.night import NightQuote
    from .night import save_night
    last = days[-1]
    save_night(session, NightQuote(contract="TX", month="202612", night_date=last + timedelta(days=1), ref_date=last,
                                   close=22_680.0, ref_close=23_096.0, volume=85_000))
    return last
