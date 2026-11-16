from datetime import date

import requests

from app import notify as nt
from app.demo import seed_demo

D = date(2026, 11, 13)


class FakeChannel(nt.Channel):
    def __init__(self, name="fake", fail=False):
        super().__init__(name)
        self.sent, self.fail = [], fail

    def configured(self):
        return True

    def send(self, title, text, url):
        if self.fail:
            raise requests.ConnectionError("boom")
        self.sent.append((title, text, url))


def test_message_content(session):
    seed_demo(session, D)
    title, text = nt.build_message(session, D, "https://example.github.io/stock-market/")
    assert "11/13（五）" in title
    assert "建議買入" in text and "建議賣出" in text and "台積電 2330" in text
    assert "回測" in text and "https://example.github.io/stock-market/" in text
    assert "非投資建議" in text
    assert "盤前指數" in text and "昨收" in text and "參考停損" in text  # 示範資料含夜盤


def test_holdings_alert_in_message(session, monkeypatch):
    seed_demo(session, D)
    monkeypatch.setenv("HOLDINGS", "2330,99999")
    _, text = nt.build_message(session, D, "")
    assert "停損停利提醒" in text and "持股 台積電 2330 已跌破停損價" in text


def test_sends_once_per_date_and_channel(session):
    seed_demo(session, D)
    ch = FakeChannel()
    assert nt.notify(session, D, [ch], app_url="")["sent"] == ["fake"]
    assert nt.notify(session, D, [ch], app_url="")["skipped"] == ["fake"]  # 第二次（例如連假）不重送
    assert len(ch.sent) == 1
    assert nt.notify(session, D, [ch], force=True, app_url="")["sent"] == ["fake"]
    assert len(ch.sent) == 2


def test_failure_is_reported_and_retried_next_time(session):
    seed_demo(session, D)
    bad = FakeChannel("bad", fail=True)
    assert nt.notify(session, D, [bad], app_url="")["failed"] == ["bad"]
    bad.fail = False
    assert nt.notify(session, D, [bad], app_url="")["sent"] == ["bad"]  # 失敗不記錄，下次會再試


def test_line_payload(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(nt.requests, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
    monkeypatch.delenv("LINE_TO", raising=False)
    nt.LineChannel().send("t", "內文", "")
    url, kw = calls[0]
    assert url.endswith("/broadcast") and kw["headers"]["Authorization"] == "Bearer tok"
    assert kw["json"]["messages"][0] == {"type": "text", "text": "內文"}
    monkeypatch.setenv("LINE_TO", "U123")
    nt.LineChannel().send("t", "內文", "")
    assert calls[1][0].endswith("/push") and calls[1][1]["json"]["to"] == "U123"


def test_ntfy_payload(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(nt.requests, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    monkeypatch.setenv("NTFY_TOPIC", "twstock-abc")
    nt.NtfyChannel().send("台股觀察清單", "📊 標題\n內文", "https://x/")
    url, kw = calls[0]
    assert url == "https://ntfy.sh" and kw["json"]["topic"] == "twstock-abc"
    assert kw["json"]["title"] == "台股觀察清單" and kw["json"]["message"] == "內文" and kw["json"]["click"] == "https://x/"
