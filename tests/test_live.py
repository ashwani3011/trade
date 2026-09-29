"""The real-time loop: decisions use only completed candles, survive restarts,
and agree with the end-of-day record."""
from datetime import datetime, timedelta

import pandas as pd

from algo import clock
from algo.data.synthetic import SyntheticDataSource
from algo.live import LiveTrader, next_poll
from algo.options.source import SyntheticOptionSource
from algo.runner import run_day
from algo.state import Store
from tests.test_pipeline import settings

DAY = datetime(2025, 3, 4, tzinfo=clock.IST)


def at(monkeypatch, hh, mm, ss=20):
    t = DAY.replace(hour=hh, minute=mm, second=ss)
    monkeypatch.setattr(clock, "now_ist", lambda: t)
    return t


def test_next_poll_is_just_after_candle_close():
    assert next_poll(DAY.replace(hour=9, minute=25, second=21)) == DAY.replace(hour=9, minute=30, second=20)
    assert next_poll(DAY.replace(hour=9, minute=13, second=0)) == DAY.replace(hour=9, minute=15, second=20)


def test_live_decisions_match_final_record(tmp_path, monkeypatch):
    s = settings(tmp_path)
    src = SyntheticDataSource()
    osrc = SyntheticOptionSource(src)

    at(monkeypatch, 9, 25)
    trader = LiveTrader(s, src, osrc)
    trader.tick()
    first = list(trader._load_journal())
    # at 9:25:20 only the 9:15 and 9:20 candles exist -> nothing can have a later bar time
    assert all(e.get("bar_time", "00:00") <= "09:25" for e in first)

    seen_at = {}
    t = DAY.replace(hour=9, minute=30, second=20)
    while t.hour < 15 or t.minute <= 35:
        monkeypatch.setattr(clock, "now_ist", lambda t=t: t)
        for e in trader.tick():
            seen_at[e["key"]] = e["decided_at"]
            if e["type"] in ("ENTRY", "EXIT"):
                # a decision about bar HH:MM can only be made after that bar opened
                assert e["bar_time"] <= e["decided_at"][:5]
        t += timedelta(minutes=5)

    # restart mid-day: nothing is journaled twice
    at(monkeypatch, 12, 0)
    assert LiveTrader(s, src, osrc).tick() == []

    journal = trader._load_journal()
    # every fill was preceded by an ORDER decided at the close of the signal candle,
    # i.e. no later than the fill candle's open time
    orders = {(e["variant"], e["symbol"]): e for e in journal if e["type"] == "ORDER"}
    for e in (e for e in journal if e["type"] == "ENTRY" and not e["variant"].startswith("tg_opt")):
        o = orders[(e["variant"], e["symbol"])]
        assert o["decided_at"][:5] <= e["bar_time"] < e["decided_at"][:5]
    keys = [e["key"] for e in journal]
    assert len(keys) == len(set(keys))

    # final end-of-day record agrees with the live entries/exits
    at(monkeypatch, 15, 40)
    run_day(s, DAY.date(), source=src, option_source=osrc)
    store = Store(s.state_dir)
    live_entries = {(e["variant"], e["symbol"], e["bar_time"]) for e in journal if e["type"] == "ENTRY"}
    final = pd.concat([store.trades(v) for v in store.load_registry(s.strategies).variants], ignore_index=True)
    final_entries = {(r.variant, r.symbol, r.entry_time) for r in final.itertuples()}
    assert final_entries == live_entries
    assert (s.reports_dir / "live" / "2025-03-04.md").read_text().count("Decision log") == 1
