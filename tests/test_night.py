from datetime import date

import pytest

from app.collectors.night import parse_csv, select_night
from app.demo import seed_demo
from app.night import apply_pre_index, gap_alerts, night_notes, night_score

HEADER = "交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量\n"


def row(d, month, close, vol, session, contract="TX"):
    return f"{d},{contract},{month},1,1,1,{close},0,0%,{vol},{close},0,0,0,0,0,,{session},0\n"


def test_parse_and_select_next_day_convention():
    # 期交所把夜盤標在「隔一個交易日」：11/13（五）日盤後的夜盤標為 11/16
    csv = HEADER + row("2026/11/13", "202612", 23096, 90000, "一般") + row("2026/11/13", "202701", 23150, 900, "一般") \
        + row("2026/11/16", "202612", 22680, 85000, "盤後") + row("2026/11/16", "202701", 22700, 300, "盤後") \
        + row("2026/11/16", "202612/202701", 50, 10, "盤後") + row("2026/11/13", "202612", 99, 5, "一般", contract="MTX")
    rows = parse_csv(csv)
    assert len(rows) == 6 and rows[0].session == "一般" and rows[0].close == 23096
    q = select_night(rows)
    assert (q.month, q.ref_date, q.night_date) == ("202612", date(2026, 11, 13), date(2026, 11, 16))
    assert q.change_pct == pytest.approx(22680 / 23096 - 1)


def test_select_ignores_day_session_after_the_night():
    # 2026-11-16 下午實測情境：標為 11/16 的夜盤（上週五晚上）已出，11/16 日盤也已收盤
    csv = HEADER + row("2026/11/13", "202611", 43150, 1, "一般") + row("2026/11/16", "202611", 40885, 1, "一般") \
        + row("2026/11/16", "202611", 43706, 1, "盤後")
    q = select_night(parse_csv(csv))
    assert q.ref_date == date(2026, 11, 13) and q.ref_close == 43150 and q.night_date == date(2026, 11, 16)
    assert q.change_pct == pytest.approx(43706 / 43150 - 1)


def test_purge_and_ignore_invalid_rows(session):
    from datetime import datetime

    from app.db import NightSession
    from app.night import night_info, purge_invalid
    session.add(NightSession(ref_date=date(2026, 11, 16), night_date=date(2026, 11, 16), contract="TX", month="202611",
                             close=43706, ref_close=40885, volume=1, fetched_at=datetime(2026, 11, 16, 17, 30)))
    session.commit()
    assert night_info(session, date(2026, 11, 16)) is None  # 錯誤紀錄不會被讀到
    assert purge_invalid(session) == 1


def test_parse_non_csv():
    assert parse_csv("<html>查無資料</html>") == []
    assert select_night([]) is None


def test_score_notes_and_pre_index():
    assert night_score(0) == 0 and night_score(0.03) > 0.99 and night_score(-0.01) == pytest.approx(-0.7616, abs=1e-3)
    assert "夜盤大跌" in night_notes(-0.02)[0] and "夜盤大漲" in night_notes(0.02)[0]
    assert "不大" in night_notes(0.001)[0]
    rows = [{"total_score": 0.9}, {"total_score": -0.9}]
    apply_pre_index(rows, {"score": -1.0})
    assert rows[0]["pre_index"] == 88 and rows[1]["pre_index"] == 0  # 0.9-0.15→88；-0.9-0.15=-1.05→裁切為 0
    apply_pre_index(rows, None)
    assert rows[0]["pre_index"] is None


def test_gap_alerts():
    night = {"change_pct": -0.03}
    items = [
        {"code": "A", "name": "A", "last_close": 100, "stop": 98, "take": 110},   # 估 97 → 跌破
        {"code": "B", "name": "B", "last_close": 100, "stop": 95, "take": 110},   # 估 97 → 沒事
        {"code": "C", "name": "C", "close": 100, "stop": 90, "take": 102},        # 跌 → 不會觸及停利
    ]
    res = gap_alerts(items, night)
    assert [(r["code"], r["kind"], r["est_open"]) for r in res] == [("A", "gap_stop", 97.0)]
    up = gap_alerts(items, {"change_pct": 0.03})
    assert [(r["code"], r["kind"]) for r in up] == [("C", "gap_take")]
    assert gap_alerts(items, None) == []


def test_night_in_recommendations_and_message(session):
    from app import services
    from app import notify as nt
    d = seed_demo(session, date(2026, 11, 13))
    rec = services.get_recommendations(session, d)
    assert rec["night"]["change_pct"] < -0.015
    assert all(r["pre_index"] < r["index"] for r in rec["buy"])
    title, text = nt.build_message(session, d, "")
    assert "收盤＋夜盤" in title and "台指期夜盤" in text and "夜盤大跌" in text and "盤前指數" in text
