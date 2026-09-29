"""Daily jobs.

run_day   - after 15:35 IST: replay the complete day for every active variant and
            record it in the ledgers (idempotent; a day is recorded once).
run_live  - during market hours: replay the day so far with the same engine and
            write reports/live/<date>.md with closed trades and open positions.
            Nothing is recorded; because the replay never looks ahead, the live
            view always matches what the final run will record for the same bars.
"""
from __future__ import annotations

import logging
from datetime import date

from . import report
from .clock import is_final, now_ist
from .config import Settings, make_option_source, make_source
from .families import DataHub, DayResult, kind, simulate
from .state import Store

log = logging.getLogger(__name__)


def _hub(s: Settings, source=None, option_source=None) -> DataHub:
    src = source or make_source(s)
    return DataHub(s, src, option_source or make_option_source(s, src))


def run_day(s: Settings, day: date, source=None, option_source=None) -> dict[str, DayResult] | None:
    if not is_final(day) and source is None:
        raise RuntimeError(f"{day} is still trading (final after 15:35 IST) - use `live` instead")
    store = Store(s.state_dir)
    reg = store.load_registry(s.strategies)
    todo = [v for v in reg.active() if not store.has_day(v.id, day)]
    if not todo:
        log.info("%s already recorded for all active variants", day)
        return {}
    hub = _hub(s, source, option_source)
    if not hub.load([day], {kind(v.family) for v in todo}):
        log.info("%s: no market data (holiday, weekend or data not yet available)", day)
        return None
    results: dict[str, DayResult] = {}
    for v in todo:
        ctx = hub.context(v.family, day)
        if ctx is None:
            log.warning("%s: no data for %s", v.id, day)
            continue
        equity = store.equity(v.id, s.capital)
        res = simulate(v.family, v.params, ctx, equity, s, variant=v.id)
        store.record_day(v.id, day, equity, res.trades)
        results[v.id] = res
        log.info("%-18s trades=%d pnl=%.2f", v.id, len(res.trades), sum(t.net for t in res.trades))
    board = report.leaderboard(store, reg, s.capital)
    path = report.write_daily(s.reports_dir, day, results, board)
    log.info("report written to %s", path)
    return results


def run_live(s: Settings, day: date, source=None, option_source=None) -> dict[str, DayResult] | None:
    store = Store(s.state_dir)
    reg = store.load_registry(s.strategies)
    active = reg.active()
    hub = _hub(s, source, option_source)
    if not hub.load([day], {kind(v.family) for v in active}, min_bars=1):
        log.info("%s: no bars yet", day)
        return None
    results: dict[str, DayResult] = {}
    for v in active:
        ctx = hub.context(v.family, day)
        if ctx is None:
            continue
        results[v.id] = simulate(v.family, v.params, ctx, store.equity(v.id, s.capital), s, variant=v.id,
                                 final=is_final(day))
    path = report.write_live(s.reports_dir, day, now_ist(), results)
    log.info("live report written to %s", path)
    return results
