"""推播觀察清單：LINE（Messaging API）、Telegram、ntfy（手機推播 App）。

設定任一組環境變數即啟用該管道（可同時多個）：
  LINE_CHANNEL_ACCESS_TOKEN [+ LINE_TO]   LINE 官方帳號；未設 LINE_TO 則「廣播」給所有加好友的人
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   Telegram Bot
  NTFY_TOPIC [+ NTFY_SERVER]              ntfy（https://ntfy.sh），手機裝 ntfy App 訂閱同名主題
  APP_URL                                 訊息中附上的 App 網址
  HOLDINGS                                我的持股（選用），每行「代號,成本[,停損%,停利%][,股數=1000][,日期=2026-11-01]」

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
from .night import gap_alerts, night_info
from .portfolio import portfolio
from .risk import check_holdings, fmt_price, load_holdings
from .db import NotificationLog

log = logging.getLogger(__name__)
TOP_N = 5
WEEKDAYS = "一二三四五六日"


# ---------- 訊息內容 ----------

def _line(r: dict, side: str) -> str:
    flow = r["reason"].split("｜")[0].split("，")[:2]  # 只取外資的前兩段，保持簡短
    if r.get("pre_index") is not None:
        idx = f"盤前指數 {r['pre_index']}（{r['pre_label']}，昨收 {r['index']}）"
    else:
        idx = f"推薦指數 {r['index']}（{r['label']}）"
    out = f"{r['rank']}. {r['name']} {r['code']}｜{idx}\n   {'，'.join(flow)}"
    lv = r.get("levels")
    if side == "buy" and lv:
        out += (f"\n   參考停損 {fmt_price(lv['stop'])}（-{lv['stop_pct']:.0%}）"
                f"／停利 {fmt_price(lv['take'])}（+{lv['take_pct']:.0%}）")
    return out


ALERT_ICON = {"stop": "🛑", "near_stop": "⚠️", "take": "🎯", "near_take": "📈", "expired": "⏱"}


def _alert_lines(session: Session, d: date) -> list[str]:
    lines = []
    for a in services.get_alerts(session, d)["alerts"]:  # noqa: B007
        price = a["exit_price"] or a["last_close"]
        lines.append(f"{ALERT_ICON.get(a['kind'], '•')} {a['name']} {a['code']} {a['text']}："
                     f"收盤 {fmt_price(price)}，{a['open_date'][5:]} 入清單起 {fmt_pct(a['ret'])}"
                     f"（停損 {fmt_price(a['stop'])}／停利 {fmt_price(a['take'])}）")
    al = services.get_alerts(session, d)
    holdings = check_holdings(session, d, load_holdings())
    night = night_info(session, d)
    for g in al["gap_alerts"] + [dict(h, prefix="持股 ") for h in gap_alerts(
            [h for h in holdings if h["kind"] not in ("stop", "take")], night)]:
        lines.append(f"🌙 {g.get('prefix', '')}{g['name']} {g['code']} {g['text']}：昨收 {fmt_price(g.get('last_close') or g['close'])}"
                     f" → 預估 {fmt_price(g['est_open'])}（停損 {fmt_price(g['stop'])}／停利 {fmt_price(g['take'])}）")
    return lines  # 持股本身的停損停利狀態列在「💼 我的持股」段落


def _money(x: float | None) -> str:
    return "—" if x is None else f"{x:+,.0f} 元"


def _portfolio_lines(session: Session, d: date) -> list[str]:
    pf = portfolio(session, d, load_holdings())
    if not pf["items"]:
        return []
    lines = ["\n💼 我的持股"]
    t = pf["totals"]
    if t:
        lines.append(f"總損益 {_money(t['pnl'])}（{fmt_pct(t['pnl_pct'])}）｜今日 {_money(t['day_pnl'])}"
                     f"｜市值 {t['value']:,.0f} 元")
        if t["priced"] < t["count"]:
            lines.append(f"（總計只含有填股數的 {t['priced']}/{t['count']} 檔）")
    for it in pf["items"]:
        icon = ALERT_ICON.get(it["kind"], "⚠️" if any(x["tone"] == "down" for x in it["signals"]) else "•")
        line = f"{icon} {it['name']} {it['code']}｜{fmt_pct(it['ret'])}"
        if it.get("pnl") is not None:
            line += f"（{_money(it['pnl'])}）"
        if it.get("day_change") is not None:
            line += f"，今日 {fmt_pct(it['day_change'])}"
        line += f"\n   收盤 {fmt_price(it['close'])}｜指數 {it['index']}｜"
        line += it["text"] if it["kind"] != "ok" else f"停損 {fmt_price(it['stop'])}／停利 {fmt_price(it['take'])}"
        if it["signals"]:
            line += "\n   " + "、".join(x["text"] for x in it["signals"])
        lines.append(line)
    return lines


def build_message(session: Session, d: date, app_url: str = "") -> tuple[str, str]:
    """回傳 (標題, 內文)。"""
    rec = services.get_recommendations(session, d)
    title = f"台股觀察清單｜{d:%m/%d}（{WEEKDAYS[d.weekday()]}）收盤＋夜盤" if rec.get("night") else \
        f"台股觀察清單｜{d:%m/%d}（{WEEKDAYS[d.weekday()]}）收盤資料"
    parts = [f"📊 {title}"]
    night = rec.get("night")
    if night:
        arrow = "▲" if night["change"] > 0 else "▼" if night["change"] < 0 else "－"
        parts.append(f"\n🌙 台指期夜盤 {fmt_price(night['close'])}  {arrow}{abs(night['change']):,.0f}"
                     f"（{night['change_pct'] * 100:+.2f}%）")
        parts += [f"・{n}" for n in night["notes"]]
    alerts = _alert_lines(session, d)
    if alerts:
        parts.append("\n🔔 停損停利提醒")
        parts += alerts
    parts += _portfolio_lines(session, d)
    for side, head in (("buy", "📈 建議買入（外資買＋新聞利多）"), ("sell", "📉 建議賣出／避開（外資賣＋新聞利空）")):
        rows = rec[side][:TOP_N]
        parts.append(f"\n{head}")
        parts += [_line(r, side) for r in rows] or ["（無符合條件標的）"]
        if len(rec[side]) > TOP_N:
            parts.append(f"…另有 {len(rec[side]) - TOP_N} 檔，詳見 App")

    bt_all = compute_backtest(session)
    bt = bt_all["summary"]
    b5, s5 = bt["buy"]["5"], bt["sell"]["5"]
    if b5["n"] or s5["n"]:
        line = "\n🔁 回測（過去清單 5 日後）"
        if b5["n"]:
            line += f"\n買入：平均 {fmt_pct(b5['avg'])}，勝率 {b5['win_rate']:.0%}（{b5['n']} 筆）"
        if s5["n"]:
            line += f"\n賣出：平均 {fmt_pct(s5['avg'])}，避開成功率 {s5['win_rate']:.0%}（{s5['n']} 筆）"
        st = bt_all["strategy"]
        if st["closed"]:
            line += (f"\n照停損停利出場：平均 {fmt_pct(st['avg'])}，勝率 {st['win_rate']:.0%}"
                     f"（停利 {st['take']}／停損 {st['stop']}／到期 {st['expired']}）")
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
