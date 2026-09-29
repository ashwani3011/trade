"""Daily job: paper trade every active variant on one day of real market data."""
from __future__ import annotations

import logging
from datetime import date

from . import report
from .config import Settings, make_source
from .data.market import MarketData
from .engine import Trade, simulate_day
from .state import Store
from .strategies import build

log = logging.getLogger(__name__)


def run_day(s: Settings, day: date, source=None) -> dict[str, list[Trade]] | None:
    md = MarketData(source or make_source(s), s.universe, s.cache_dir, s.interval)
    loaded = md.days([day])
    if not loaded:
        log.info("%s: no market data (holiday, weekend or data not yet available)", day)
        return None
    data = loaded[0]
    store = Store(s.state_dir)
    reg = store.load_registry(s.strategies)
    results: dict[str, list[Trade]] = {}
    for v in reg.active():
        if store.has_day(v.id, day):
            log.info("%s already recorded for %s - skipping", v.id, day)
            continue
        equity = store.equity(v.id, s.capital)
        trades = simulate_day(build(v.family, v.params), data, equity, s.risk, s.costs, variant=v.id, interval=s.interval)
        store.record_day(v.id, day, equity, trades)
        results[v.id] = trades
        log.info("%-18s trades=%d pnl=%.2f", v.id, len(trades), sum(t.net for t in trades))
    board = report.leaderboard(store, reg, s.capital)
    path = report.write_daily(s.reports_dir, day, results, board)
    log.info("report written to %s", path)
    return results
