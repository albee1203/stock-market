"""外資及陸資買賣超資料：上市（TWSE）與上櫃（TPEx）。

解析時一律用「欄位名稱」找欄位，而非寫死索引，降低官方調整欄位順序時壞掉的機率。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..config import settings
from . import http

log = logging.getLogger(__name__)

TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/TWT38U"  # 外資及陸資買賣超彙總表
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"  # 三大法人買賣明細（含外資欄位）
TPEX_LEGACY_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"

_STOCK_CODE = re.compile(r"^\d{4}$")


@dataclass
class FlowRow:
    code: str
    name: str
    market: str
    buy_shares: int
    sell_shares: int
    net_shares: int


def is_common_stock(code: str) -> bool:
    if not _STOCK_CODE.match(code):
        return False  # 權證、特別股、6 碼 ETF 等
    if code.startswith("00") and not settings.include_etf:
        return False  # 4 碼 ETF（0050、0056...）
    return True


def _parse_table(fields: list[str], rows: list[list[Any]], market: str) -> list[FlowRow]:
    fields = [str(f).strip() for f in fields]
    i_code = http.find_col(fields, "代號")
    i_name = http.find_col(fields, "名稱")
    # 優先取「不含外資自營商」的外陸資欄位
    i_buy = http.find_col(fields, "外", "買進", exclude=("自營商買進",))
    i_sell = http.find_col(fields, "外", "賣出", exclude=("自營商賣出",))
    i_net = http.find_col(fields, "外", "買賣超", exclude=("自營商買賣超",))
    if None in (i_code, i_name, i_net):
        # 舊版 / 無欄位名稱時的預設欄位位置：代號, 名稱, 買進, 賣出, 買賣超
        i_code, i_name, i_buy, i_sell, i_net = 0, 1, 2, 3, 4
        log.warning("%s 欄位名稱無法辨識，改用預設欄位位置：%s", market, fields)
    out: list[FlowRow] = []
    for r in rows:
        code = str(r[i_code]).strip()
        if not is_common_stock(code):
            continue
        out.append(FlowRow(
            code=code,
            name=str(r[i_name]).strip(),
            market=market,
            buy_shares=http.to_int(r[i_buy]) if i_buy is not None else 0,
            sell_shares=http.to_int(r[i_sell]) if i_sell is not None else 0,
            net_shares=http.to_int(r[i_net]),
        ))
    return out


def parse_twse(payload: dict[str, Any]) -> list[FlowRow]:
    if payload.get("stat") != "OK" or not payload.get("data"):
        return []  # 假日或尚未公布：stat 會是「很抱歉，沒有符合條件的資料!」
    return _parse_table(payload["fields"], payload["data"], "TWSE")


def parse_tpex(payload: dict[str, Any]) -> list[FlowRow]:
    # 新版：{"tables": [{"fields": [...], "data": [...]}]}
    for table in payload.get("tables") or []:
        if table.get("data"):
            return _parse_table(table.get("fields") or [], table["data"], "TPEX")
    # 舊版：{"aaData": [[...]]}
    if payload.get("aaData"):
        return _parse_table([], payload["aaData"], "TPEX")
    return []


def fetch_twse(d: date) -> list[FlowRow]:
    payload = http.get_json(TWSE_URL, {"date": d.strftime("%Y%m%d"), "response": "json"})
    return parse_twse(payload)


def fetch_tpex(d: date) -> list[FlowRow]:
    try:
        payload = http.get_json(TPEX_URL, {
            "type": "Daily", "sect": "EW", "date": d.strftime("%Y/%m/%d"), "response": "json",
        })
        rows = parse_tpex(payload)
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 - 新版失敗就試舊版
        log.warning("TPEx 新版 API 失敗，改試舊版：%s", exc)
    roc = f"{d.year - 1911}/{d:%m/%d}"
    payload = http.get_json(TPEX_LEGACY_URL, {"l": "zh-tw", "se": "EW", "t": "D", "d": roc})
    return parse_tpex(payload)


def fetch_all(d: date) -> list[FlowRow]:
    rows: list[FlowRow] = []
    for name, fn in (("TWSE", fetch_twse), ("TPEX", fetch_tpex)):
        try:
            got = fn(d)
            log.info("%s %s 外資買賣超：%d 檔", d, name, len(got))
            rows.extend(got)
        except Exception as exc:  # noqa: BLE001
            log.error("抓取 %s %s 外資資料失敗：%s", d, name, exc)
    return rows
