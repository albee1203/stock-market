"""命令列入口。

  python -m app.cli run [--date 2026-11-13] [--llm|--no-llm] [--no-news]
  python -m app.cli backfill --days 10
  python -m app.cli rescore [--date ...]       # 只重新計算評分（調參數後用）
  python -m app.cli demo                       # 載入離線示範資料
  python -m app.cli show [--date ...]          # 在終端機印出清單
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime

from .analysis.scoring import build_recommendations
from .config import TZ
from .db import get_session, init_db
from .pipeline import ensure_flow_history, latest_trading_date, run_daily
from . import services


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _print_lists(rec: dict) -> None:
    print(f"\n=== {rec['date']} 建議觀察清單 ===")
    for side, title in (("buy", "📈 建議買入（外資買 + 新聞利多）"), ("sell", "📉 建議賣出 / 避開（外資賣 + 新聞利空）")):
        print(f"\n{title}")
        if not rec[side]:
            print("  （無符合條件標的）")
        for r in rec[side]:
            print(f"  {r['rank']:>2}. {r['code']} {r['name']:<6} 綜合 {r['total_score']:+.2f}"
                  f"（外資 {r['foreign_score']:+.2f} / 新聞 {r['news_score']:+.2f}）")
            print(f"      {r['reason']}")
    print(f"\n※ {rec['disclaimer']}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="app.cli", description="台股外資＋新聞輔助分析")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="執行每日流程")
    r.add_argument("--date", type=_date, default=None)
    g = r.add_mutually_exclusive_group()
    g.add_argument("--llm", dest="use_llm", action="store_true", default=None, help="強制使用 Claude 分析")
    g.add_argument("--no-llm", dest="use_llm", action="store_false", help="只用詞典分析")
    r.add_argument("--no-news", action="store_true")

    b = sub.add_parser("backfill", help="回補歷史外資資料")
    b.add_argument("--date", type=_date, default=None)
    b.add_argument("--days", type=int, default=10)

    for name in ("rescore", "show"):
        x = sub.add_parser(name)
        x.add_argument("--date", type=_date, default=None)

    d = sub.add_parser("demo", help="載入離線示範資料")
    d.add_argument("--date", type=_date, default=None)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    s = get_session()
    today = datetime.now(TZ).date()
    try:
        if args.cmd == "run":
            d = args.date or today
            res = run_daily(s, d, use_llm=args.use_llm, with_news=not args.no_news)
            print(json.dumps(res, ensure_ascii=False))
            if res["ok"]:
                _print_lists(services.get_recommendations(s, d))
        elif args.cmd == "backfill":
            ensure_flow_history(s, (args.date or today), days=args.days, max_lookback=args.days * 2 + 5)
            print("完成，目前資料日期：", services.list_dates(s, args.days + 1))
        elif args.cmd == "rescore":
            d = args.date or latest_trading_date(s)
            build_recommendations(s, d)
            _print_lists(services.get_recommendations(s, d))
        elif args.cmd == "show":
            d = args.date or latest_trading_date(s)
            if d is None:
                print("資料庫尚無資料")
                return
            _print_lists(services.get_recommendations(s, d))
        elif args.cmd == "demo":
            d = __import__("app.demo", fromlist=["seed_demo"]).seed_demo(s, args.date or today)
            _print_lists(services.get_recommendations(s, d))
    finally:
        s.close()


if __name__ == "__main__":
    main()
