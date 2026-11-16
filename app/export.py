"""把資料庫匯出成靜態 JSON，給手機 App（PWA，web/）讀取。

輸出結構（--out 目錄，預設 site/）：
  data/index.json            {updated_at, latest, dates[], disclaimer}
  data/days/<date>.json      當日觀察清單、外資排行、新聞
  data/stocks.json           [[code, name, market], ...]（搜尋用）
  data/history/<code>.json   個股近 60 日外資買賣超與收盤價
  data/backtest.json         回測統計與最近 300 筆明細
並把 web/ 的 App 外殼複製進來，整個目錄就是可部署的網站。
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .config import ROOT, TZ
from .db import DailyPrice, ForeignFlow, Stock

WEB_DIR = ROOT / "web"

FOREIGN_FIELDS = ("code", "name", "market", "net_lots", "net_amount", "net_ratio", "streak", "close", "score", "flags")


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _round(v, n=4):
    return round(v, n) if isinstance(v, float) else v


def export_day(session: Session, d) -> dict:
    rec = services.get_recommendations(session, d)
    foreign = {
        side: [{k: _round(r[k]) for k in FOREIGN_FIELDS} for r in services.get_foreign_ranking(session, d, side, 30)]
        for side in ("buy", "sell")
    }
    news = services.get_news(session, d)
    for a in news:
        a["summary"] = a["summary"][:200]
        a.pop("id", None)
    return {"date": d.isoformat(), "buy": rec["buy"], "sell": rec["sell"], "foreign": foreign, "news": news}


def export_history(session: Session, out: Path, days: int = 60) -> int:
    dates = list(session.scalars(
        select(ForeignFlow.date).distinct().order_by(ForeignFlow.date.desc()).limit(days)
    ))
    if not dates:
        return 0
    lo = min(dates)
    flows: dict[str, list] = defaultdict(list)
    for f in session.scalars(select(ForeignFlow).where(ForeignFlow.date >= lo).order_by(ForeignFlow.date)):
        flows[f.code].append(f)
    closes = {(p.code, p.date): p.close for p in session.scalars(select(DailyPrice).where(DailyPrice.date >= lo))}
    for code, rows in flows.items():
        _dump(out / f"{code}.json", [
            [f.date.isoformat(), round(f.net_shares / 1000), closes.get((code, f.date))] for f in rows
        ])
    return len(flows)


def export_site(session: Session, out: Path, days: int = 20) -> dict:
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    if WEB_DIR.exists():
        shutil.copytree(WEB_DIR, out)
    data = out / "data"
    dates = [datetime.fromisoformat(x).date() for x in services.list_dates(session, days)]
    for d in dates:
        _dump(data / "days" / f"{d.isoformat()}.json", export_day(session, d))
    _dump(data / "stocks.json", [[s.code, s.name, s.market] for s in session.scalars(select(Stock).order_by(Stock.code))])
    n_hist = export_history(session, data / "history")
    _dump(data / "backtest.json", services.get_backtest(session))
    index = {
        "updated_at": datetime.now(TZ).isoformat(timespec="minutes"),
        "latest": dates[0].isoformat() if dates else None,
        "dates": [d.isoformat() for d in dates],
        "disclaimer": services.DISCLAIMER,
    }
    _dump(data / "index.json", index)
    return {"out": str(out), "days": len(dates), "stocks_with_history": n_hist}
