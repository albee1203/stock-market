"""期交所期貨行情（含「盤後交易時段」＝夜盤），用來取得台指期夜盤漲跌。

資料來源：期交所「期貨每日交易行情下載」CSV（Big5 編碼）
  POST https://www.taifex.com.tw/cht/3/futDataDown
欄位（依名稱解析）：交易日期, 契約, 到期月份(週別), 開盤價, 最高價, 最低價, 收盤價, 漲跌價, 漲跌%, 成交量, 結算價, ..., 交易時段
「交易時段」為「一般」或「盤後」。
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from . import http

log = logging.getLogger(__name__)

URL = "https://www.taifex.com.tw/cht/3/futDataDown"


@dataclass
class FutRow:
    date: date
    contract: str
    month: str
    close: float | None
    volume: int
    session: str  # 一般 / 盤後


@dataclass
class NightQuote:
    contract: str
    month: str
    night_date: date        # 期交所標示的交易日期
    ref_date: date          # 比較基準：夜盤開始前最後一個日盤
    close: float
    ref_close: float
    volume: int

    @property
    def change_pct(self) -> float:
        return self.close / self.ref_close - 1


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(text: str) -> list[FutRow]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = None
    out: list[FutRow] = []
    for row in reader:
        row = [c.strip() for c in row]
        if header is None:
            if "交易日期" in row and "收盤價" in row:
                header = row
                col = {name: i for i, name in enumerate(header)}
                i_month = next((i for n, i in col.items() if n.startswith("到期月份")), None)
            continue
        if len(row) < len(header) - 1:
            continue
        d = _parse_date(row[col["交易日期"]])
        if d is None:
            continue
        out.append(FutRow(
            date=d,
            contract=row[col["契約"]],
            month=row[i_month].replace(" ", "") if i_month is not None else "",
            close=http.to_float(row[col["收盤價"]]),
            volume=http.to_int(row[col["成交量"]]),
            session=row[col["交易時段"]] if "交易時段" in col else "一般",
        ))
    if header is None:
        log.warning("期交所回傳內容不是預期的 CSV（可能是查無資料或網站改版）")
    return out


def select_night(rows: list[FutRow], contract: str = "TX") -> NightQuote | None:
    """取最近一次夜盤（成交量最大的到期月份），與「夜盤開始前最後一個日盤」的同月份收盤比較。

    不論期交所把夜盤標在開始日或隔日，「日期 ≤ 夜盤日期的最近一個日盤」都會是夜盤開始前的那一盤
    （早上 9 點前執行時，當天日盤尚未產生）。
    """
    rows = [r for r in rows if r.contract == contract and r.close and "/" not in r.month]  # 排除價差組合
    night = [r for r in rows if r.session == "盤後"]
    if not night:
        return None
    nd = max(r.date for r in night)
    best = max((r for r in night if r.date == nd), key=lambda r: r.volume)
    day = [r for r in rows if r.session == "一般" and r.month == best.month and r.date <= nd]
    if not day:
        return None
    ref = max(day, key=lambda r: r.date)
    return NightQuote(contract=contract, month=best.month, night_date=nd, ref_date=ref.date,
                      close=best.close, ref_close=ref.close, volume=best.volume)


def fetch(start: date, end: date, contract: str = "TX") -> list[FutRow]:
    http._throttle("www.taifex.com.tw")
    resp = requests.post(URL, headers=http.HEADERS, timeout=30, data={
        "down_type": "1", "commodity_id": contract, "commodity_id2": "",
        "queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d"),
    })
    resp.raise_for_status()
    return parse_csv(resp.content.decode("cp950", errors="replace"))


def fetch_latest_night(today: date, contract: str = "TX") -> NightQuote | None:
    rows = fetch(today - timedelta(days=7), today, contract)
    return select_night(rows, contract)
