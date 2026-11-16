"""資料庫模型（SQLAlchemy 2.0）。改 DATABASE_URL 即可從 SQLite 切到 PostgreSQL。"""
from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stocks"
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    market: Mapped[str] = mapped_column(String(8))  # TWSE / TPEX


class ForeignFlow(Base):
    """外資及陸資（不含外資自營商）每日買賣超，單位：股。"""
    __tablename__ = "foreign_flows"
    __table_args__ = (UniqueConstraint("date", "code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    market: Mapped[str] = mapped_column(String(8))
    buy_shares: Mapped[int] = mapped_column(Integer)
    sell_shares: Mapped[int] = mapped_column(Integer)
    net_shares: Mapped[int] = mapped_column(Integer)


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("date", "code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 成交股數


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    # 分析結果
    analyzer: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    bullish_keywords: Mapped[str] = mapped_column(Text, default="[]")
    bearish_keywords: Mapped[str] = mapped_column(Text, default="[]")
    # {"2330": {"score": 0.6, "keywords": [...], "reason": "..."}}
    stock_sentiments: Mapped[str] = mapped_column(Text, default="{}")
    # 來源本身標註的相關個股（例如鉅亨網 API 的 market 欄位）
    tagged_codes: Mapped[str] = mapped_column(Text, default="[]")

    def stock_map(self) -> dict[str, dict[str, Any]]:
        return json.loads(self.stock_sentiments or "{}")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("date", "code", "side"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(10), ForeignKey("stocks.code"))
    side: Mapped[str] = mapped_column(String(4))  # buy / sell
    rank: Mapped[int] = mapped_column(Integer)
    total_score: Mapped[float] = mapped_column(Float)
    foreign_score: Mapped[float] = mapped_column(Float)
    news_score: Mapped[float] = mapped_column(Float)
    streak: Mapped[int] = mapped_column(Integer)
    net_shares: Mapped[int] = mapped_column(Integer)
    net_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_count: Mapped[int] = mapped_column(Integer)
    keywords: Mapped[str] = mapped_column(Text, default="[]")
    reason: Mapped[str] = mapped_column(Text)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str | None = None):
    """建立 engine 與資料表；測試時可傳入 sqlite:///:memory: 之類的 URL。"""
    global _engine, _SessionLocal
    url = url or settings.database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


def upsert_stocks(session: Session, stocks: dict[str, tuple[str, str]]) -> None:
    """stocks: code -> (name, market)"""
    existing = {s.code: s for s in session.scalars(select(Stock).where(Stock.code.in_(list(stocks))))}
    for code, (name, market) in stocks.items():
        if code in existing:
            existing[code].name, existing[code].market = name, market
        else:
            session.add(Stock(code=code, name=name, market=market))
