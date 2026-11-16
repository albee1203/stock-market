"""共用 HTTP 工具：統一 User-Agent、逾時、重試與節流（證交所會封鎖過於頻繁的請求）。"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from ..config import settings

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/xml, */*",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

_last_call: dict[str, float] = {}


def _throttle(host: str) -> None:
    wait = settings.request_interval - (time.monotonic() - _last_call.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def get(url: str, params: dict[str, Any] | None = None, retries: int = 3) -> requests.Response:
    host = requests.utils.urlparse(url).netloc
    last_exc: Exception | None = None
    for attempt in range(retries):
        _throttle(host)
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=settings.request_timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # 網路錯誤或 5xx：退避後重試
            last_exc = exc
            log.warning("GET %s 失敗（第 %d 次）：%s", url, attempt + 1, exc)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"多次請求失敗：{url}") from last_exc


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    return get(url, params).json()


def to_int(value: Any) -> int:
    """'1,234' / '-5,678' / '--' -> int"""
    s = str(value).replace(",", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return 0


def to_float(value: Any) -> float | None:
    s = str(value).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def find_col(fields: list[str], *keywords: str, exclude: tuple[str, ...] = ()) -> int | None:
    """回傳第一個同時包含所有 keywords、且不含 exclude 字串的欄位索引。"""
    for i, f in enumerate(fields):
        f = str(f)
        if all(k in f for k in keywords) and not any(x in f for x in exclude):
            return i
    return None
