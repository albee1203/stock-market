"""FastAPI 後端。啟動：uvicorn app.api:app --reload"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import services
from .config import TZ
from .db import get_session
from .pipeline import latest_trading_date, run_daily

app = FastAPI(title="台股外資＋新聞輔助分析 API", version="0.1.0", description=services.DISCLAIMER)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def db() -> Iterator[Session]:
    s = get_session()
    try:
        yield s
    finally:
        s.close()


def _resolve_date(s: Session, d: date | None) -> date:
    d = d or latest_trading_date(s)
    if d is None:
        raise HTTPException(404, "資料庫尚無資料，請先執行 python -m app.cli run 或 python -m app.cli demo")
    return d


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/dates")
def dates(s: Session = Depends(db)) -> list[str]:
    return services.list_dates(s)


@app.get("/api/recommendations")
def recommendations(d: date | None = Query(None, alias="date"), s: Session = Depends(db)) -> dict:
    return services.get_recommendations(s, _resolve_date(s, d))


@app.get("/api/foreign")
def foreign_ranking(d: date | None = Query(None, alias="date"), side: str = Query("buy", pattern="^(buy|sell)$"),
                    limit: int = Query(30, le=200), s: Session = Depends(db)) -> list[dict]:
    return services.get_foreign_ranking(s, _resolve_date(s, d), side, limit)


@app.get("/api/news")
def news(d: date | None = Query(None, alias="date"), code: str | None = None, s: Session = Depends(db)) -> list[dict]:
    return services.get_news(s, _resolve_date(s, d), code)


@app.get("/api/stocks/{code}")
def stock(code: str, days: int = Query(30, le=250), s: Session = Depends(db)) -> dict:
    detail = services.get_stock_detail(s, code, days)
    if detail is None:
        raise HTTPException(404, f"找不到股票 {code}")
    return detail


@app.get("/api/alerts")
def alerts(d: date | None = Query(None, alias="date"), s: Session = Depends(db)) -> dict:
    """停損停利提醒：清單追蹤的新觸發／接近，以及持有中的虛擬部位。"""
    return services.get_alerts(s, _resolve_date(s, d))


@app.get("/api/portfolio")
def portfolio(d: date | None = Query(None, alias="date"), holdings: str | None = Query(
        None, description="持股，每行「代號,成本[,停損%,停利%][,股數=1000]」；未提供則讀 HOLDINGS 環境變數或 holdings.csv"),
        s: Session = Depends(db)) -> dict:
    """個人持股追蹤：損益、今日漲跌、停損停利狀態與籌碼／消息訊號。"""
    return services.get_portfolio(s, _resolve_date(s, d), holdings)


@app.get("/api/backtest")
def backtest(s: Session = Depends(db)) -> dict:
    """清單出現後 1 / 5 / 20 個交易日的報酬統計與明細。"""
    return services.get_backtest(s)


def _run_job(d: date, use_llm: bool | None) -> None:
    s = get_session()
    try:
        run_daily(s, d, use_llm=use_llm)
    finally:
        s.close()


@app.post("/api/run")
def trigger_run(background: BackgroundTasks, d: date | None = Query(None, alias="date"),
                use_llm: bool | None = None) -> dict:
    """在背景執行每日流程（抓資料需要數十秒，故不阻塞請求）。"""
    from datetime import datetime

    d = d or datetime.now(TZ).date()
    background.add_task(_run_job, d, use_llm)
    return {"queued": True, "date": d.isoformat()}
