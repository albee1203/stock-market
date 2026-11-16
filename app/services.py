"""讀取查詢（FastAPI 與 Streamlit 共用）。"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis.scoring import compute_foreign_metrics, foreign_metrics_as_dicts
from .collectors.news import news_window
from .db import DailyPrice, ForeignFlow, NewsArticle, Recommendation, Stock

DISCLAIMER = "本工具僅為資訊整理與研究用途，不構成任何投資建議；投資前請自行判斷並承擔風險。"


def list_dates(session: Session, limit: int = 60) -> list[str]:
    q = select(ForeignFlow.date).distinct().order_by(ForeignFlow.date.desc()).limit(limit)
    return [d.isoformat() for d in session.scalars(q)]


def get_recommendations(session: Session, d: date) -> dict:
    names = {s.code: s.name for s in session.scalars(select(Stock))}
    rows = session.scalars(
        select(Recommendation).where(Recommendation.date == d).order_by(Recommendation.side, Recommendation.rank)
    )
    out: dict = {"date": d.isoformat(), "buy": [], "sell": [], "disclaimer": DISCLAIMER}
    for r in rows:
        out[r.side].append({
            "rank": r.rank, "code": r.code, "name": names.get(r.code, r.code),
            "total_score": r.total_score, "foreign_score": r.foreign_score, "news_score": r.news_score,
            "streak": r.streak, "net_lots": round(r.net_shares / 1000),
            "net_amount_e8": round(r.net_amount / 1e8, 2) if r.net_amount is not None else None,
            "news_count": r.news_count, "keywords": json.loads(r.keywords), "reason": r.reason,
        })
    return out


def get_foreign_ranking(session: Session, d: date, side: str = "buy", limit: int = 30) -> list[dict]:
    rows = foreign_metrics_as_dicts(compute_foreign_metrics(session, d))
    key = (lambda r: r["net_amount"] if r["net_amount"] is not None else r["net_shares"] * 50.0)
    rows.sort(key=key, reverse=(side == "buy"))
    rows = [r for r in rows if (r["net_shares"] > 0) == (side == "buy") and r["net_shares"] != 0]
    return rows[:limit]


def _article_dict(a: NewsArticle, names: dict[str, str]) -> dict:
    stocks = a.stock_map()
    return {
        "id": a.id, "source": a.source, "title": a.title, "url": a.url, "summary": a.summary,
        "published_at": a.published_at.isoformat(timespec="minutes"),
        "analyzer": a.analyzer, "sentiment": a.sentiment,
        "bullish_keywords": json.loads(a.bullish_keywords), "bearish_keywords": json.loads(a.bearish_keywords),
        "stocks": [{"code": c, "name": names.get(c, c), **v} for c, v in stocks.items()],
    }


def get_news(session: Session, d: date, code: str | None = None) -> list[dict]:
    start, end = news_window(d)
    names = {s.code: s.name for s in session.scalars(select(Stock))}
    arts = session.scalars(
        select(NewsArticle).where(NewsArticle.published_at.between(start, end))
        .order_by(NewsArticle.published_at.desc())
    )
    out = [_article_dict(a, names) for a in arts]
    if code:
        out = [a for a in out if any(s["code"] == code for s in a["stocks"])]
    return out


def get_stock_detail(session: Session, code: str, days: int = 30) -> dict | None:
    st = session.get(Stock, code)
    if st is None:
        return None
    flows = list(session.scalars(
        select(ForeignFlow).where(ForeignFlow.code == code).order_by(ForeignFlow.date.desc()).limit(days)
    ))
    prices = {p.date: p for p in session.scalars(
        select(DailyPrice).where(DailyPrice.code == code, DailyPrice.date.in_([f.date for f in flows]))
    )}
    history = []
    for f in reversed(flows):
        p = prices.get(f.date)
        history.append({
            "date": f.date.isoformat(), "net_lots": round(f.net_shares / 1000),
            "close": p.close if p else None,
        })
    return {"code": st.code, "name": st.name, "market": st.market, "history": history}
