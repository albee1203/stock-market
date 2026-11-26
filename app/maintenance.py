"""資料庫維護：刪除用不到的資料、清掉太舊的紀錄並壓實，讓資料庫維持在 GitHub 單檔上限（100 MB）以下。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .collectors.prices import keep_price
from .db import DailyPrice, ForeignFlow, NewsArticle

log = logging.getLogger(__name__)

KEEP_MARKET_DAYS = 550   # 股價、外資：約 1.5 年（回測與連買天數夠用）
KEEP_NEWS_DAYS = 180     # 新聞：半年


def compact(session: Session, today: date | None = None, vacuum: bool = True) -> dict:
    today = today or date.today()
    removed: dict[str, int] = {}

    codes = set(session.scalars(select(DailyPrice.code).distinct()))
    junk = [c for c in codes if not keep_price(c)]
    n = 0
    for i in range(0, len(junk), 500):
        n += session.execute(delete(DailyPrice).where(DailyPrice.code.in_(junk[i:i + 500]))).rowcount or 0
    removed["權證等股價"] = n

    cut = today - timedelta(days=KEEP_MARKET_DAYS)
    removed["過舊股價"] = session.execute(delete(DailyPrice).where(DailyPrice.date < cut)).rowcount or 0
    removed["過舊外資"] = session.execute(delete(ForeignFlow).where(ForeignFlow.date < cut)).rowcount or 0
    from datetime import datetime
    ncut = datetime.combine(today - timedelta(days=KEEP_NEWS_DAYS), datetime.min.time())
    removed["過舊新聞"] = session.execute(delete(NewsArticle).where(NewsArticle.published_at < ncut)).rowcount or 0
    session.commit()

    bind = session.get_bind()
    if vacuum and bind.dialect.name == "sqlite":
        with bind.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT").execute(text("VACUUM"))
    log.info("資料庫整理：%s", removed)
    return removed
