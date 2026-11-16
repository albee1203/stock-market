"""重播：用過去 N 個交易日的資料重新產生每日清單，讓回測立刻有樣本。

- 外資、股價：證交所 / 櫃買可查歷史日期。
- 新聞：鉅亨網 API 可依時間區間查歷史新聞；RSS 只有最近幾天，歷史日期會自然缺少。
- 預設只用詞典分析：用 Claude 重播 60 天約需數百次請求，費用不低，需明確加 --llm。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Recommendation
from .pipeline import run_daily

log = logging.getLogger(__name__)


def replay(session: Session, end: date, days: int, use_llm: bool = False, skip_existing: bool = True) -> list[dict]:
    done = set(session.scalars(select(Recommendation.date).distinct())) if skip_existing else set()
    targets, cur = [], end
    while len(targets) < days and cur > end - timedelta(days=days * 2 + 20):
        if cur.weekday() < 5:
            targets.append(cur)
        cur -= timedelta(days=1)
    results = []
    for d in sorted(targets):  # 由舊到新，連買天數的歷史會逐步累積
        if d in done:
            continue
        res = run_daily(session, d, use_llm=use_llm, backfill_days=6)
        log.info("重播 %s：%s", d, res)
        results.append(res)
    return results
