"""推播觀察清單：LINE（Messaging API）、Telegram、ntfy（手機推播 App）。

設定任一組環境變數即啟用該管道（可同時多個）：
  LINE_CHANNEL_ACCESS_TOKEN [+ LINE_TO]   LINE 官方帳號；未設 LINE_TO 則「廣播」給所有加好友的人
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   Telegram Bot
  NTFY_TOPIC [+ NTFY_SERVER]              ntfy（https://ntfy.sh），手機裝 ntfy App 訂閱同名主題
  APP_URL                                 訊息中附上的 App 網址

註：LINE Notify 已於 2025-03-31 停止服務，故使用 LINE Messaging API。
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .backtest import compute_backtest, fmt_pct
from .config import TZ
from .db import NotificationLog

log = logging.getLogger(__name__)
TOP_N = 5
WEEKDAYS = "一二三四五六日"


# ---------- 訊息內容 ----------

def _line(r: dict) -> str:
    flow = r["reason"].split("｜")[0].split("，")[:2]  # 只取外資的前兩段，保持簡短
    return f"{r['rank']}. {r['name']} {r['code']}（{r['total_score']:+.2f}）\n   {'，'.join(flow)}"


def build_message(session: Session, d: date, app_url: str = "") -> tuple[str, str]:
    """回傳 (標題, 內文)。"""
    rec = services.get_recommendations(session, d)
    title = f"台股觀察清單｜{d:%m/%d}（{WEEKDAYS[d.weekday()]}）收盤資料"
    parts = [f"📊 {title}"]
    for side, head in (("buy", "📈 建議買入（外資買＋新聞利多）"), ("sell", "📉 建議賣出／避開（外資賣＋新聞利空）")):
        rows = rec[side][:TOP_N]
        parts.append(f"\n{head}")
        parts += [_line(r) for r in rows] or ["（無符合條件標的）"]
        if len(rec[side]) > TOP_N:
            parts.append(f"…另有 {len(rec[side]) - TOP_N} 檔，詳見 App")

    bt = compute_backtest(session)["summary"]
    b5, s5 = bt["buy"]["5"], bt["sell"]["5"]
    if b5["n"] or s5["n"]:
        line = "\n🔁 回測（過去清單 5 日後）"
        if b5["n"]:
            line += f"\n買入：平均 {fmt_pct(b5['avg'])}，勝率 {b5['win_rate']:.0%}（{b5['n']} 筆）"
        if s5["n"]:
            line += f"\n賣出：平均 {fmt_pct(s5['avg'])}，避開成功率 {s5['win_rate']:.0%}（{s5['n']} 筆）"
        parts.append(line)
    if app_url:
        parts.append(f"\n👉 {app_url}")
    parts.append("※ 僅供參考，非投資建議")
    return title, "\n".join(parts)


# ---------- 推播管道 ----------

@dataclass
class Channel:
    name: str

    def configured(self) -> bool:
        raise NotImplementedError

    def send(self, title: str, text: str, url: str) -> None:
        raise NotImplementedError


class LineChannel(Channel):
    def __init__(self):
        super().__init__("line")
        self.token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
        self.to = os.getenv("LINE_TO", "")

    def configured(self) -> bool:
        return bool(self.token)

    def send(self, title: str, text: str, url: str) -> None:
        endpoint = "push" if self.to else "broadcast"
        body: dict = {"messages": [{"type": "text", "text": text[:4900]}]}
        if self.to:
            body["to"] = self.to
        resp = requests.post(f"https://api.line.me/v2/bot/message/{endpoint}", json=body, timeout=20,
                             headers={"Authorization": f"Bearer {self.token}"})
        resp.raise_for_status()


class TelegramChannel(Channel):
    def __init__(self):
        super().__init__("telegram")
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, title: str, text: str, url: str) -> None:
        resp = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage", timeout=20, json={
            "chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True,
        })
        resp.raise_for_status()


class NtfyChannel(Channel):
    def __init__(self):
        super().__init__("ntfy")
        self.topic = os.getenv("NTFY_TOPIC", "")
        self.server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

    def configured(self) -> bool:
        return bool(self.topic)

    def send(self, title: str, text: str, url: str) -> None:
        body = {"topic": self.topic, "title": title, "message": text.split("\n", 1)[-1][:3800], "tags": ["chart_with_upwards_trend"]}
        if url:
            body["click"] = url
        resp = requests.post(self.server, json=body, timeout=20)  # JSON 發佈才支援中文標題
        resp.raise_for_status()


def all_channels() -> list[Channel]:
    return [LineChannel(), TelegramChannel(), NtfyChannel()]


# ---------- 對外介面 ----------

def notify(session: Session, d: date, channels: list[Channel] | None = None, force: bool = False,
           dry_run: bool = False, app_url: str | None = None) -> dict:
    app_url = os.getenv("APP_URL", "") if app_url is None else app_url
    title, text = build_message(session, d, app_url)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if dry_run:
        return {"date": d.isoformat(), "dry_run": True, "title": title, "text": text}

    channels = [c for c in (channels if channels is not None else all_channels()) if c.configured()]
    result: dict = {"date": d.isoformat(), "sent": [], "skipped": [], "failed": []}
    if not channels:
        log.warning("沒有設定任何推播管道（LINE / Telegram / ntfy），略過")
        return result
    for ch in channels:
        # 每份清單（交易日）每個管道只推播一次：連假期間早上的排程不會重複送同一份舊清單
        already = session.scalar(select(NotificationLog).where(
            NotificationLog.date == d, NotificationLog.channel == ch.name))
        if already and not force:
            result["skipped"].append(ch.name)
            continue
        try:
            ch.send(title, text, app_url)
            session.add(NotificationLog(date=d, channel=ch.name, digest=digest,
                                        sent_at=datetime.now(TZ).replace(tzinfo=None)))
            session.commit()
            result["sent"].append(ch.name)
        except requests.RequestException as exc:
            log.error("%s 推播失敗：%s", ch.name, exc)
            result["failed"].append(ch.name)
    return result


def already_notified(session: Session, d: date) -> bool:
    return session.scalar(select(NotificationLog).where(NotificationLog.date == d)) is not None
