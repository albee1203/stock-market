"""財經新聞收集：鉅亨網（JSON API）+ 任意 RSS（預設經濟日報、工商時報）。"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from ..config import TZ, settings
from . import http

log = logging.getLogger(__name__)

CNYES_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
_TAG = re.compile(r"<[^>]+>")


@dataclass
class RawArticle:
    source: str
    url: str
    title: str
    summary: str
    published_at: datetime  # Asia/Taipei，naive
    tagged_codes: list[str] = field(default_factory=list)


def clean_text(s: str | None) -> str:
    s = html.unescape(_TAG.sub(" ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _to_local_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ)
    return dt.replace(tzinfo=None)


# ---------- 鉅亨網 ----------

def parse_cnyes(payload: dict[str, Any]) -> list[RawArticle]:
    items = (payload.get("items") or {}).get("data") or []
    out = []
    for it in items:
        codes = [
            str(m.get("code")) for m in (it.get("market") or [])
            if str(m.get("code", "")).isdigit()
        ]
        body = clean_text(it.get("summary") or "") or clean_text(it.get("content") or "")[:400]
        out.append(RawArticle(
            source="鉅亨網",
            url=f"https://news.cnyes.com/news/id/{it['newsId']}",
            title=clean_text(it.get("title")),
            summary=body,
            published_at=_to_local_naive(datetime.fromtimestamp(int(it["publishAt"]), TZ)),
            tagged_codes=codes,
        ))
    return out


def fetch_cnyes(start: datetime, end: datetime, max_pages: int = 5) -> list[RawArticle]:
    out: list[RawArticle] = []
    for page in range(1, max_pages + 1):
        payload = http.get_json(CNYES_URL, {
            "startAt": int(start.replace(tzinfo=TZ).timestamp()),
            "endAt": int(end.replace(tzinfo=TZ).timestamp()),
            "limit": 30, "page": page,
        })
        got = parse_cnyes(payload)
        out.extend(got)
        last_page = (payload.get("items") or {}).get("last_page", page)
        if not got or page >= last_page:
            break
    return out


# ---------- RSS ----------

def parse_rss(xml_text: str, source: str) -> list[RawArticle]:
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        pub = item.findtext("pubDate")
        try:
            published = _to_local_naive(parsedate_to_datetime(pub)) if pub else datetime.now(TZ).replace(tzinfo=None)
        except (TypeError, ValueError):
            published = datetime.now(TZ).replace(tzinfo=None)
        out.append(RawArticle(
            source=source, url=link, title=title,
            summary=clean_text(item.findtext("description"))[:400],
            published_at=published,
        ))
    return out


def fetch_rss(source: str, url: str) -> list[RawArticle]:
    resp = http.get(url)
    return parse_rss(resp.content.decode(resp.encoding or "utf-8", errors="replace"), source)


# ---------- 對外介面 ----------

def news_window(trading_day: datetime | Any) -> tuple[datetime, datetime]:
    """前一日收盤後（14:00）到當日 23:59 的新聞，視為影響當日判斷的消息面。"""
    day = datetime(trading_day.year, trading_day.month, trading_day.day)
    return day - timedelta(hours=10), day + timedelta(hours=23, minutes=59, seconds=59)


def fetch_all(trading_day) -> list[RawArticle]:
    start, end = news_window(trading_day)
    articles: list[RawArticle] = []
    try:
        articles += fetch_cnyes(start, end)
    except Exception as exc:  # noqa: BLE001
        log.error("鉅亨網抓取失敗：%s", exc)
    for source, url in settings.rss_feeds.items():
        if not url:
            continue
        try:
            articles += fetch_rss(source, url)
        except Exception as exc:  # noqa: BLE001
            log.error("%s RSS 抓取失敗：%s", source, exc)
    in_window = [a for a in articles if start <= a.published_at <= end]
    log.info("新聞共 %d 則，時間窗內 %d 則", len(articles), len(in_window))
    return in_window
