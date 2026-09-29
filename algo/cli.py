"""Command line entry point.

  python -m algo.cli run-day [--date YYYY-MM-DD]      paper trade all active variants for a day
  python -m algo.cli catch-up --days N                 paper trade any missed days (oldest first)
  python -m algo.cli improve                           weekly champion/challenger review + search
  python -m algo.cli backtest --family orb [--variant orb.v1] --days 60
  python -m algo.cli leaderboard
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import report
from .backtest import run as backtest
from .config import load, make_source
from .data.market import MarketData
from .data.synthetic import trading_days
from .improve import run_improve
from .runner import run_day
from .state import Store


def _today_ist() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="algo")
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--strategies", default="config/strategies.yaml")
    ap.add_argument("--source", choices=["dhan", "synthetic"], help="override data_source")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run-day")
    p.add_argument("--date", type=date.fromisoformat)
    p = sub.add_parser("catch-up")
    p.add_argument("--days", type=int, default=5)
    sub.add_parser("improve")
    p = sub.add_parser("backtest")
    p.add_argument("--family", required=True)
    p.add_argument("--variant", help="variant id from the registry (default: family champion)")
    p.add_argument("--days", type=int, default=60)
    sub.add_parser("leaderboard")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = load(args.settings, args.strategies)
    if args.source:
        s.data_source = args.source

    if args.cmd == "run-day":
        run_day(s, args.date or _today_ist())
    elif args.cmd == "catch-up":
        today = _today_ist()
        for d in trading_days(today - timedelta(days=args.days), today):
            run_day(s, d)
    elif args.cmd == "improve":
        print(json.dumps(run_improve(s, _today_ist()), indent=2))
    elif args.cmd == "backtest":
        store = Store(s.state_dir)
        reg = store.load_registry(s.strategies)
        v = reg.variants[args.variant] if args.variant else reg.champion(args.family)
        if v is None:
            raise SystemExit(f"no champion for {args.family}")
        today = _today_ist()
        md = MarketData(make_source(s), s.universe, s.cache_dir, s.interval)
        days = md.days(trading_days(today - timedelta(days=int(args.days * 1.6) + 5), today - timedelta(days=1)))[-args.days:]
        trades, _, summary = backtest(v.family, v.params, days, s.capital, s.risk, s.costs, variant=v.id)
        print(json.dumps({"variant": v.id, "params": v.params, **summary}, indent=2))
    elif args.cmd == "leaderboard":
        store = Store(s.state_dir)
        board = report.leaderboard(store, store.load_registry(s.strategies), s.capital)
        report.write_leaderboard(s.reports_dir, board, _today_ist())
        print(board.to_string(index=False) if not board.empty else "no variants yet")


if __name__ == "__main__":
    main()
