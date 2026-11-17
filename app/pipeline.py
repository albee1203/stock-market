"""每日流程：抓外資 → 抓股價 → 抓新聞 → 情緒分析 → 綜合評分 → 寫入資料庫。"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .analysis.llm import ArticleInput, ClaudeAnalyzer
from .analysis.scoring import build_recommendations
from .analysis.sentiment import ArticleAnalysis, LexiconAnalyzer
from .analysis.stock_matcher import StockMatcher
from .collectors import foreign, news, prices
from .collectors.foreign import FlowRow
from .collectors.news import RawArticle
from .collectors.prices import PriceRow
from .config import settings
from .db import DailyPrice, ForeignFlow, NewsArticle, Stock, upsert_stocks

log = logging.getLogger(__name__)


# ---------- 寫入 ----------

def save_flows(session: Session, d: date, rows: list[FlowRow]) -> int:
    upsert_stocks(session, {r.code: (r.name, r.market) for r in rows})
    existing = {f.code: f for f in session.scalars(select(ForeignFlow).where(ForeignFlow.date == d))}
    for r in rows:
        f = existing.get(r.code)
        if f is None:
            session.add(ForeignFlow(date=d, code=r.code, market=r.market, buy_shares=r.buy_shares,
                                    sell_shares=r.sell_shares, net_shares=r.net_shares))
        else:
            f.buy_shares, f.sell_shares, f.net_shares = r.buy_shares, r.sell_shares, r.net_shares
    session.commit()
    return len(rows)


def save_prices(session: Session, d: date, rows: list[PriceRow]) -> int:
    existing = {p.code: p for p in session.scalars(select(DailyPrice).where(DailyPrice.date == d))}
    for r in rows:
        p = existing.get(r.code)
        if p is None:
            session.add(DailyPrice(date=d, code=r.code, close=r.close, volume=r.volume))
        else:
            p.close, p.volume = r.close, r.volume
    session.commit()
    return len(rows)


def save_articles(session: Session, articles: list[RawArticle]) -> list[NewsArticle]:
    urls = [a.url for a in articles]
    existing = {n.url: n for n in session.scalars(select(NewsArticle).where(NewsArticle.url.in_(urls)))}
    out = []
    for a in articles:
        if a.url in existing:
            out.append(existing[a.url])
            continue
        n = NewsArticle(source=a.source, url=a.url, title=a.title, summary=a.summary,
                        published_at=a.published_at,
                        tagged_codes=json.dumps(a.tagged_codes, ensure_ascii=False))
        session.add(n)
        existing[a.url] = n
        out.append(n)
    session.commit()
    return out


def apply_analysis(n: NewsArticle, r: ArticleAnalysis) -> None:
    n.analyzer = r.analyzer
    n.sentiment = r.sentiment
    n.bullish_keywords = json.dumps(r.bullish_keywords, ensure_ascii=False)
    n.bearish_keywords = json.dumps(r.bearish_keywords, ensure_ascii=False)
    n.stock_sentiments = json.dumps(
        {c: {"score": s.score, "keywords": s.keywords, "reason": s.reason, "in_title": s.in_title}
         for c, s in r.stocks.items()},
        ensure_ascii=False,
    )


def analyze_articles(session: Session, articles: list[NewsArticle], use_llm: bool | None = None,
                     reanalyze: bool = False) -> int:
    matcher = StockMatcher({s.code: s.name for s in session.scalars(select(Stock))})
    todo = [a for a in articles if reanalyze or a.analyzer is None]
    if not todo:
        return 0
    use_llm = bool(settings.anthropic_api_key) if use_llm is None else use_llm
    inputs = [ArticleInput(a.title, a.summary, json.loads(a.tagged_codes or "[]")) for a in todo]
    if use_llm:
        results = ClaudeAnalyzer(matcher).analyze_many(inputs)
    else:
        lex = LexiconAnalyzer(matcher)
        results = [lex.analyze(i.title, i.summary, i.tagged_codes) for i in inputs]
    for a, r in zip(todo, results):
        apply_analysis(a, r)
    session.commit()
    return len(todo)


# ---------- 抓取 ----------

def ensure_flow_history(session: Session, d: date, days: int = 6, max_lookback: int = 15) -> None:
    """往前補足 days 個交易日的外資資料（連買天數需要歷史）。假日抓到空資料會自動略過。"""
    # 上市＋上櫃都有資料才算完整（避免某一市場抓取失敗的日子永遠不會補）
    have = {
        day for day, n in session.execute(
            select(ForeignFlow.date, func.count(func.distinct(ForeignFlow.market)))
            .where(ForeignFlow.date < d).group_by(ForeignFlow.date)
        ) if n >= 2
    }
    got = len([x for x in have if x >= d - timedelta(days=max_lookback)])
    cur = d
    for _ in range(max_lookback):
        if got >= days:
            break
        cur -= timedelta(days=1)
        if cur.weekday() >= 5 or cur in have:
            continue
        rows = foreign.fetch_all(cur)
        if rows:
            save_flows(session, cur, rows)
            save_prices(session, cur, prices.fetch_all(cur))
            got += 1


def run_daily(session: Session, d: date, use_llm: bool | None = None, with_news: bool = True,
              backfill_days: int = 6) -> dict:
    rows = foreign.fetch_all(d)
    if not rows:
        msg = f"{d} 查無外資買賣超資料（假日、或尚未公布：證交所約 15:00 後、櫃買約 15:30 後更新）"
        log.warning(msg)
        return {"date": d.isoformat(), "ok": False, "message": msg}
    save_flows(session, d, rows)
    save_prices(session, d, prices.fetch_all(d))
    ensure_flow_history(session, d, days=backfill_days)

    n_news = n_analyzed = 0
    if with_news:
        saved = save_articles(session, news.fetch_all(d))
        n_news = len(saved)
        n_analyzed = analyze_articles(session, saved, use_llm=use_llm)

    result = build_recommendations(session, d)
    by_market: dict[str, int] = {}
    for r in rows:
        by_market[r.market] = by_market.get(r.market, 0) + 1
    return {
        "date": d.isoformat(), "ok": True, "flows": len(rows), "flows_by_market": by_market,
        "news": n_news, "analyzed": n_analyzed,
        "buy": len(result["buy"]), "sell": len(result["sell"]),
    }


def latest_trading_date(session: Session) -> date | None:
    return session.scalar(select(func.max(ForeignFlow.date)))


def update_recent_news(session: Session, use_llm: bool | None = None) -> dict:
    """盤前更新：抓最新交易日收盤後到現在的新聞（含夜間、今晨），存檔並分析情緒。"""
    from datetime import datetime

    from .config import TZ
    latest = latest_trading_date(session)
    if latest is None:
        return {"ok": False, "message": "資料庫尚無交易日"}
    now = datetime.now(TZ).replace(tzinfo=None)
    start = datetime(latest.year, latest.month, latest.day, 13, 0)
    saved = save_articles(session, news.fetch_between(start, now))
    analyzed = analyze_articles(session, saved, use_llm=use_llm)
    return {"ok": True, "since": start.isoformat(timespec="minutes"), "news": len(saved), "analyzed": analyzed}
