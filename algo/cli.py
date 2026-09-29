"""Command line entry point.

  python -m algo.cli auto                 what the scheduler runs: live snapshot during market
                                          hours, final record + report after 15:35 IST
  python -m algo.cli live                 replay today so far -> reports/live/<date>.md
  python -m algo.cli run-day [--date D]   record one completed day for all active variants
  python -m algo.cli catch-up --days N    record any missed completed days
  python -m algo.cli improve              weekly champion/challenger review + search
  python -m algo.cli backtest --family F [--variant V] --days N
  python -m algo.cli leaderboard
  python -m algo.cli check                verify Dhan credentials and data access
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta

from . import report
from .backtest import run as backtest
from .clock import IST, is_final, market_phase, today_ist
from .config import load, make_option_source, make_source
from .data.synthetic import trading_days
from .families import DataHub, kind
from .improve import run_improve
from .runner import run_day, run_live
from .state import Store

log = logging.getLogger("algo")


def catch_up(s, days: int) -> None:
    today = today_ist()
    for d in trading_days(today - timedelta(days=days), today):
        if is_final(d):
            run_day(s, d)


def check(s) -> None:
    from .data.dhan_auth import get_access_token, token_expiry

    src = make_source(s)
    if s.data_source == "dhan":
        token = get_access_token(src.client_id, s.cache_dir)
        exp = token_expiry(token)
        print(f"token OK, expires {datetime.fromtimestamp(exp, IST):%Y-%m-%d %H:%M} IST" if exp else "token OK")
    d = today_ist() - timedelta(days=7)
    sym = s.universe[0]
    bars = src.intraday(sym, d, today_ist(), s.interval)
    print(f"{sym}: {len(bars)} bars, last {bars['time'].iloc[-1] if len(bars) else '-'}")
    idx = src.intraday(s.options.index_symbol, today_ist() - timedelta(days=3), today_ist(), s.interval)
    print(f"{s.options.index_symbol}: {len(idx)} bars")
    if s.data_source == "dhan":
        c = src.option_contracts(sym)
        print(f"{sym} listed options: {len(c)} contracts, expiries {sorted(set(c['expiry']))[:3]}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="algo")
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--strategies", default="config/strategies.yaml")
    ap.add_argument("--source", choices=["dhan", "synthetic"], help="override data_source")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auto")
    sub.add_parser("live")
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
    sub.add_parser("check")
    sub.add_parser("trade-live", help="run the real-time paper trader in the foreground until the close")
    sub.add_parser("supervise", help="start the real-time trader in the background if it isn't running")
    sub.add_parser("stop-live")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = load(args.settings, args.strategies)
    if args.source:
        s.data_source = args.source

    if args.cmd == "auto":
        phase = market_phase()
        log.info("market phase: %s", phase)
        if phase == "open":
            run_live(s, today_ist())
        else:  # after close, pre-open or weekend: record every completed day not yet recorded
            catch_up(s, 5)
    elif args.cmd == "live":
        run_live(s, today_ist())
    elif args.cmd == "run-day":
        run_day(s, args.date or today_ist())
    elif args.cmd == "catch-up":
        catch_up(s, args.days)
    elif args.cmd == "improve":
        print(json.dumps(run_improve(s, today_ist()), indent=2))
    elif args.cmd == "backtest":
        store = Store(s.state_dir)
        reg = store.load_registry(s.strategies)
        v = reg.variants[args.variant] if args.variant else reg.champion(args.family)
        if v is None:
            raise SystemExit(f"no champion for {args.family}")
        today = today_ist()
        cal = trading_days(today - timedelta(days=int(args.days * 1.6) + 5), today - timedelta(days=1))
        src = make_source(s)
        hub = DataHub(s, src, make_option_source(s, src))
        hub.load(cal, {kind(v.family)})
        ctxs = [c for c in (hub.context(v.family, d) for d in cal) if c is not None][-args.days:]
        _, _, summary = backtest(v.family, v.params, ctxs, s.capital, s, variant=v.id)
        print(json.dumps({"variant": v.id, "params": v.params, **summary}, indent=2))
    elif args.cmd == "leaderboard":
        store = Store(s.state_dir)
        board = report.leaderboard(store, store.load_registry(s.strategies), s.capital)
        report.write_leaderboard(s.reports_dir, board, today_ist())
        print(board.to_string(index=False) if not board.empty else "no variants yet")
    elif args.cmd == "check":
        check(s)
    elif args.cmd == "trade-live":
        from .live import LiveTrader, _pid_file

        _pid_file(s).parent.mkdir(parents=True, exist_ok=True)
        _pid_file(s).write_text(str(os.getpid()))
        LiveTrader(s).run()
    elif args.cmd == "supervise":
        from .live import ensure_running

        phase = market_phase()
        if phase in ("pre", "open"):
            fwd = ["--settings", args.settings, "--strategies", args.strategies]
            if args.source:
                fwd += ["--source", args.source]
            pid, started = ensure_running(s, fwd)
            print(f"live trader {'started' if started else 'already running'} (pid {pid}), market phase: {phase}")
        else:
            print(f"market phase: {phase} - recording any completed days")
            catch_up(s, 5)
    elif args.cmd == "stop-live":
        from .live import stop

        print("stopped" if stop(s) else "not running")


if __name__ == "__main__":
    main()
