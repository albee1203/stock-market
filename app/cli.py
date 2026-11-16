"""命令列入口。

  python -m app.cli run [--date 2026-11-13] [--llm|--no-llm] [--no-news]
  python -m app.cli backfill --days 10
  python -m app.cli rescore [--date ...]       # 只重新計算評分（調參數後用）
  python -m app.cli demo                       # 載入離線示範資料
  python -m app.cli show [--date ...]          # 在終端機印出清單
  python -m app.cli export [--out site]        # 匯出手機 App 用的靜態網站
  python -m app.cli night                      # 抓台指期夜盤（盤前執行），納入最新清單的盤前指數
  python -m app.cli notify [--dry-run]         # 推播最新一份清單（LINE / Telegram / ntfy）
  python -m app.cli backtest                   # 印出回測結果
  python -m app.cli replay --days 60           # 用歷史資料重新產生過去的清單（回測樣本）
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
            print(f"  {r['rank']:>2}. {r['code']} {r['name']:<6} 推薦指數 {r['index']:>3}（{r['label']}）"
                  f"  外資 {r['foreign_score']:+.2f} / 新聞 {r['news_score']:+.2f}")
            print(f"      {r['reason']}")
            lv = r.get("levels")
            if side == "buy" and lv:
                print(f"      參考停損 {lv['stop']:,.2f}（-{lv['stop_pct']:.0%}）／停利 {lv['take']:,.2f}（+{lv['take_pct']:.0%}）")
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

    sub.add_parser("night", help="抓台指期夜盤（盤前執行）")

    n = sub.add_parser("notify", help="推播最新一份清單")
    n.add_argument("--date", type=_date, default=None, help="預設＝資料庫中最新交易日")
    n.add_argument("--force", action="store_true", help="已推播過也再送一次")
    n.add_argument("--dry-run", action="store_true", help="只印出訊息，不發送")
    n.add_argument("--max-age-days", type=int, default=5, help="清單超過幾天就不推播（避免資料中斷時推舊清單）")

    sub.add_parser("backtest", help="印出回測結果")

    rp = sub.add_parser("replay", help="用歷史資料重新產生過去的清單")
    rp.add_argument("--days", type=int, default=60)
    rp.add_argument("--date", type=_date, default=None, help="重播到哪一天（預設今天）")
    rp.add_argument("--llm", action="store_true", help="用 Claude 分析歷史新聞（請求數多，注意費用）")

    e = sub.add_parser("export", help="匯出手機 App（PWA）用的靜態網站")
    e.add_argument("--out", default="site")
    e.add_argument("--days", type=int, default=20, help="保留最近幾個交易日")

    d = sub.add_parser("demo", help="載入離線示範資料")
    d.add_argument("--date", type=_date, default=None)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # 收集警告／錯誤，寫進 data/last_run.json 方便排查（GitHub Actions 會一起存到 data 分支）
    problems: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            problems.append(f"{record.levelname} {record.name}: {record.getMessage()}"[:500])

    collector = _Collector(level=logging.WARNING)
    logging.getLogger().addHandler(collector)
    summary: dict = {"command": args.cmd, "started_at": datetime.now(TZ).isoformat(timespec="seconds")}
    init_db()
    s = get_session()
    today = datetime.now(TZ).date()
    try:
        if args.cmd == "run":
            d = args.date or today
            res = run_daily(s, d, use_llm=args.use_llm, with_news=not args.no_news)
            summary["result"] = res
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
        elif args.cmd == "night":
            from .night import update_night
            info = update_night(s, today)
            summary["result"] = info
            print(json.dumps(info, ensure_ascii=False) if info else "查無夜盤資料")
        elif args.cmd == "notify":
            from .notify import notify
            d = args.date or latest_trading_date(s)
            if d is None:
                print("資料庫尚無資料，不推播")
            elif (today - d).days > args.max_age_days and not args.force:
                print(f"最新清單是 {d}，已超過 {args.max_age_days} 天，不推播（資料可能中斷，請檢查每日排程）")
            else:
                res = notify(s, d, force=args.force, dry_run=args.dry_run)
                summary["result"] = {k: v for k, v in res.items() if k != "text"}
                print(res["text"] if args.dry_run else json.dumps(res, ensure_ascii=False))
        elif args.cmd == "backtest":
            from .backtest import compute_backtest, fmt_pct
            bt = compute_backtest(s)
            print(f"清單天數 {bt['list_days']}，期間 {bt['period']}\n{bt['method']}")
            for side, label in (("buy", "建議買入"), ("sell", "建議賣出")):
                print(f"\n{label}")
                for h in bt["horizons"]:
                    m = bt["summary"][side][str(h)]
                    if m["n"]:
                        print(f"  {h:>2} 日後：平均 {fmt_pct(m['avg'])}｜中位數 {fmt_pct(m['median'])}｜"
                              f"{'勝率' if side == 'buy' else '避開成功率'} {m['win_rate']:.0%}｜"
                              f"超額 {fmt_pct(m['avg_excess'])}｜{m['n']} 筆")
                    else:
                        print(f"  {h:>2} 日後：尚無足夠資料")
            st = bt["strategy"]
            print(f"\n照停損停利出場（買入清單，最長持有 20 日）")
            if st["closed"]:
                print(f"  已出場 {st['closed']} 筆（停利 {st['take']}／停損 {st['stop']}／到期 {st['expired']}），"
                      f"平均 {fmt_pct(st['avg'])}｜勝率 {st['win_rate']:.0%}｜平均持有 {st['avg_days']} 日｜持有中 {st['open']} 筆")
            else:
                print(f"  尚無已出場部位（持有中 {st['open']} 筆）")
        elif args.cmd == "replay":
            from .replay import replay
            res = replay(s, args.date or today, args.days, use_llm=args.llm)
            print(f"重播完成：{sum(1 for r in res if r['ok'])} 個交易日產生清單")
        elif args.cmd == "export":
            from .export import export_site
            print(json.dumps(export_site(s, args.out, args.days), ensure_ascii=False))
        elif args.cmd == "demo":
            d = __import__("app.demo", fromlist=["seed_demo"]).seed_demo(s, args.date or today)
            _print_lists(services.get_recommendations(s, d))
    finally:
        s.close()
        if args.cmd in ("run", "backfill", "replay", "night", "notify"):
            _write_summary(args.cmd, summary, problems)


def _write_summary(cmd: str, summary: dict, problems: list[str]) -> None:
    from pathlib import Path

    path = Path("data/last_run.json")
    try:
        allrun = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        allrun = {}
    summary["finished_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    summary["problems"] = problems[-50:]
    allrun[cmd] = summary
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(allrun, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    main()
