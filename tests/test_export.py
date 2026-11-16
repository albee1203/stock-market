import json
from datetime import date

from app.demo import seed_demo
from app.export import export_site


def test_export_site(session, tmp_path):
    seed_demo(session, date(2026, 11, 13))
    res = export_site(session, tmp_path / "site", days=20)
    root = tmp_path / "site"
    assert res["days"] == 20
    # App 外殼有被複製
    for f in ("index.html", "app.js", "sw.js", "manifest.webmanifest", "icons/icon-192.png"):
        assert (root / f).exists(), f
    index = json.loads((root / "data/index.json").read_text(encoding="utf-8"))
    assert index["latest"] == "2026-11-13" and len(index["dates"]) == 20
    day = json.loads((root / "data/days/2026-11-13.json").read_text(encoding="utf-8"))
    assert day["buy"] and day["sell"] and day["news"]
    assert day["foreign"]["buy"][0]["net_lots"] > 0 and day["foreign"]["sell"][0]["net_lots"] < 0
    hist = json.loads((root / "data/history/2330.json").read_text(encoding="utf-8"))
    assert len(hist["h"]) == 30 and hist["h"][-1][:2] == ["2026-11-13", 6200] and hist["h"][-1][2] > 0
    assert hist["levels"]["stop"] < hist["h"][-1][2] < hist["levels"]["take"]
    assert 0 <= day["scores"]["2330"] <= 100 and day["buy"][0]["index"] >= 50
    assert "alerts" in day and "open" in day and "risk" in index
    bt = json.loads((root / "data/backtest.json").read_text(encoding="utf-8"))
    assert bt["list_days"] == 25 and bt["details"]
