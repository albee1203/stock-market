"""每日收盤價與成交股數，用來把外資買賣超「股數」換算成「金額」與「佔成交量比例」。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import http

log = logging.getLogger(__name__)

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"  # 每日收盤行情（含所有上市股票）
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


def keep_price(code: str) -> bool:
    """只保存普通股（4 碼）與 ETF（00 開頭，例如 0050、00878、00632R）；權證、牛熊證等衍生商品不存。

    2026-11 實測：股價表每天約 15,000 筆，其中約 13,000 筆是權證，讓資料庫超過 GitHub 單檔 100 MB 上限。
    """
    return len(code) == 4 and code.isdigit() or code.startswith("00")


@dataclass
class PriceRow:
    code: str
    close: float | None
    volume: int | None


def _parse(fields: list[str], rows: list[list[Any]]) -> list[PriceRow]:
    fields = [str(f) for f in fields]
    i_code = http.find_col(fields, "代號")
    i_close = http.find_col(fields, "收盤")
    i_vol = http.find_col(fields, "成交股數")
    if i_code is None or i_close is None:
        log.warning("股價欄位無法辨識：%s", fields)
        return []
    return [
        PriceRow(
            code=str(r[i_code]).strip(),
            close=http.to_float(r[i_close]),
            volume=http.to_int(r[i_vol]) if i_vol is not None else None,
        )
        for r in rows
    ]


def _is_quote_table(fields: list) -> bool:
    fs = [str(f) for f in fields or []]
    return http.find_col(fs, "代號") is not None and http.find_col(fs, "收盤") is not None


def parse_twse(payload: dict[str, Any]) -> list[PriceRow]:
    if payload.get("stat") not in (None, "OK", "ok"):
        return []
    if payload.get("data") and _is_quote_table(payload.get("fields")):
        return _parse(payload["fields"], payload["data"])
    # MI_INDEX 回傳多張表（大盤指數、漲跌統計…），要挑有「證券代號＋收盤價」的那張
    for table in payload.get("tables") or []:
        if table.get("data") and _is_quote_table(table.get("fields")):
            return _parse(table["fields"], table["data"])
    return []


def parse_tpex(payload: dict[str, Any]) -> list[PriceRow]:
    for table in payload.get("tables") or []:
        if table.get("data") and _is_quote_table(table.get("fields")):
            return _parse(table["fields"], table["data"])
    return []


def fetch_all(d: date) -> list[PriceRow]:
    out: list[PriceRow] = []
    jobs = (
        ("TWSE", TWSE_URL, {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"}, parse_twse),
        ("TPEX", TPEX_URL, {"date": d.strftime("%Y/%m/%d"), "response": "json"}, parse_tpex),
    )
    for name, url, params, parser in jobs:
        try:
            got = parser(http.get_json(url, params))
            log.info("%s %s 股價：%d 檔", d, name, len(got))
            out.extend(got)
        except Exception as exc:  # noqa: BLE001 - 股價缺失時評分會退回用張數
            log.error("抓取 %s %s 股價失敗：%s", d, name, exc)
    return out
