from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from algo.config import option_costs
from algo.costs import CostModel
from algo.data.base import IST
from algo.data.market import DayData
from algo.data.synthetic import SyntheticDataSource
from algo.options.engine import OptionsDay, rank, simulate_options_day
from algo.options.source import OptionSeries, SyntheticOptionSource, monthly_expiry, pick_strike, target_expiry_month
from algo.options.strategy import TopGainerOptions

DAY = date(2026, 4, 22)
NO_COST = CostModel(brokerage_pct=0, brokerage_cap=0, stt_sell_pct=0, exchange_pct=0, sebi_pct=0,
                    stamp_buy_pct=0, gst_pct=0, slippage_bps=0)


def bars(rows):
    """rows: list of (open, high, low, close) for consecutive 5-min bars from 9:15."""
    start = datetime.combine(DAY, time(9, 15))
    t = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(len(rows))]).tz_localize(IST)
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"time": t, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": 1000.0})


def flat(price, n=75):
    return [(price, price, price, price)] * n


def hist(close):
    return pd.DataFrame({"date": [DAY - timedelta(days=1)], "open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6})


class FakeOptions:
    def __init__(self, table):
        self.table = table  # (symbol, type) -> list of bar rows
        self.calls = []

    def series(self, und, day, option_type, spot, otm_pct, min_expiry_days):
        self.calls.append((und, option_type, round(spot, 2)))
        rows = self.table.get((und, option_type))
        if rows is None:
            return None
        return OptionSeries(f"{und} {option_type}", 100.0, DAY, option_type, 1000, bars(rows), "fake")


def ctx(stocks, options, index_rows=None):
    b = {s: bars(r) for s, (r, _) in stocks.items()}
    h = {s: hist(pc) for s, (_, pc) in stocks.items()}
    b["NIFTY 50"] = bars(index_rows or [(100, 101, 99.9, 101), (101, 102, 100.9, 102)] + flat(102, 73))
    return OptionsDay(DAY, DayData(DAY, b, h), "NIFTY 50", options)


def strong_green(p0, p1):  # two 5-min bars making a strong green 10-min candle
    return [(p0, p0 + (p1 - p0) * 0.55, p0 - 0.05, p0 + (p1 - p0) * 0.5), (p0 + (p1 - p0) * 0.5, p1 + 0.05, p0 + (p1 - p0) * 0.45, p1)]


def test_expiry_calendar():
    assert monthly_expiry(2026, 4) == date(2026, 4, 28)          # last Tuesday
    assert monthly_expiry(2025, 3) == date(2025, 3, 27)          # last Thursday (old rule)
    assert target_expiry_month(date(2026, 4, 22), 0)[:2] == (2026, 4)
    assert target_expiry_month(date(2026, 4, 25), 5)[:2] == (2026, 5)   # 3 days left -> roll
    assert target_expiry_month(date(2026, 4, 29), 0)[:2] == (2026, 5)   # after expiry
    assert pick_strike(np.array([480, 490, 500, 510]), 478.95, "CE", 4.0) == 500
    assert pick_strike(np.array([740, 750, 760, 780]), 786, "PE", 4.0) == 750


def test_ranking_uses_first_10min_candle_vs_prev_close():
    stocks = {"AAA": (strong_green(100, 103) + flat(103, 73), 100.0),
              "BBB": (strong_green(100, 101) + flat(101, 73), 100.0),
              "CCC": ([(100, 100, 98, 98)] * 75, 100.0)}
    r = rank(ctx(stocks, FakeOptions({})))
    assert [x[0] for x in r] == ["AAA", "BBB", "CCC"]
    assert round(r[0][1], 2) == 3.0


def test_video_rules_call_entry_stop_and_target():
    # option first candle 9:15-9:25: high 3.3, low 2.9 -> entry at 9:25 open 3.28, stop 2.9 (risk 0.38)
    opt = [(3.0, 3.1, 2.9, 3.05), (3.05, 3.3, 3.0, 3.25), (3.28, 3.5, 3.2, 3.45), (3.45, 4.0, 3.4, 3.9),
           (3.9, 4.5, 3.9, 4.4)] + flat(4.4, 70)
    stocks = {"WIPRO": (strong_green(470, 479) + flat(479, 73), 465.0), "B": (flat(100), 100.0)}
    fake = FakeOptions({("WIPRO", "CE"): opt})
    res = simulate_options_day(TopGainerOptions({"put_leg": "none"}), ctx(stocks, fake), 20000, NO_COST, "v")
    assert fake.calls[0] == ("WIPRO", "CE", 479.0)
    [t] = res.trades
    assert (t.entry_time, t.entry) == ("09:25", 3.28)
    assert t.exit_reason == "target" and abs(t.exit - (3.28 + 3 * 0.38)) < 1e-6
    assert t.qty == 1000


def test_big_option_candle_uses_60pct_stop_and_time_exit():
    opt = [(2.0, 2.5, 2.0, 2.4), (2.4, 3.0, 2.3, 2.9)] + flat(2.95, 73)  # range 1.0 = 33% of high
    stocks = {"X": (strong_green(100, 101.5) + flat(101.5, 73), 100.0)}
    res = simulate_options_day(TopGainerOptions({"put_leg": "none"}), ctx(stocks, FakeOptions({("X", "CE"): opt})),
                               20000, NO_COST, "v")
    [t] = res.trades
    assert t.exit_reason == "time" and t.exit_time == "12:00"
    assert t.net == 0 and abs(t.r_multiple) < 1e-9  # flat price -> zero P&L
    # stop sits at 60% of the big candle (3.0 - 0.6 * 1.0), so a dip to 2.45 must not stop out
    opt2 = opt[:3] + [(2.95, 2.95, 2.45, 2.9)] + flat(2.9, 71)
    res2 = simulate_options_day(TopGainerOptions({"put_leg": "none"}), ctx(stocks, FakeOptions({("X", "CE"): opt2})),
                                20000, NO_COST, "v")
    assert res2.trades[0].exit_reason == "time"
    assert "too big" not in " ".join(res.notes)


def test_too_big_stock_candle_and_red_candle_skipped():
    stocks = {"BIG": (strong_green(100, 104) + flat(104, 73), 100.0),       # 4% candle > 2% limit
              "RED": ([(102, 102.2, 101, 101.1), (101.1, 101.3, 100.9, 101.0)] + flat(101, 73), 99.0)}
    fake = FakeOptions({})
    res = simulate_options_day(TopGainerOptions({"put_leg": "none"}), ctx(stocks, fake), 20000, NO_COST, "v")
    assert not res.trades and not fake.calls
    text = " ".join(res.notes)
    assert "BIG: first candle too big" in text and "RED: first candle not green" in text


def test_one_lot_not_affordable_is_reported():
    opt = [(30, 31, 29, 30), (30, 31, 29, 30.5)] + flat(31, 73)   # 1 lot = 1000 x 31 = 31k > 20k
    stocks = {"X": (strong_green(100, 101.5) + flat(101.5, 73), 100.0)}
    res = simulate_options_day(TopGainerOptions({"put_leg": "none"}), ctx(stocks, FakeOptions({("X", "CE"): opt})),
                               20000, NO_COST, "v")
    assert not res.trades and "not affordable" in " ".join(res.notes)


def test_hedge_put_on_top_loser_when_call_goes_red():
    call = [(3.0, 3.1, 2.9, 3.05), (3.05, 3.3, 3.0, 3.25), (3.28, 3.3, 3.0, 3.1)] + flat(3.05, 72)
    put = [(2.0, 2.2, 1.9, 2.1), (2.1, 2.3, 2.0, 2.2), (2.2, 2.3, 2.1, 2.25), (2.3, 2.4, 2.2, 2.35)] + flat(2.35, 71)
    stocks = {"UP": (strong_green(100, 101.5) + flat(101.5, 73), 100.0),
              "DOWN": ([(100, 100.1, 99, 99.2), (99.2, 99.3, 98.4, 98.5)] + flat(98.5, 73), 100.0)}
    fake = FakeOptions({("UP", "CE"): call, ("DOWN", "PE"): put})
    res = simulate_options_day(TopGainerOptions({"n_stocks": 1}), ctx(stocks, fake), 20000, NO_COST, "v")
    labels = {t.symbol: t for t in res.trades}
    assert "DOWN PE" in labels and labels["DOWN PE"].entry_time == "09:30" and labels["DOWN PE"].entry == 2.3
    assert labels["DOWN PE"].reason.startswith("hedge")


def test_live_mode_reports_open_positions():
    opt = [(3.0, 3.1, 2.9, 3.05), (3.05, 3.3, 3.0, 3.25), (3.28, 3.4, 3.2, 3.35)]
    stocks = {"X": (strong_green(100, 101.5) + flat(101.5, 1), 100.0)}
    res = simulate_options_day(TopGainerOptions({"put_leg": "none"}), ctx(stocks, FakeOptions({("X", "CE"): opt})),
                               20000, NO_COST, "v", final=False)
    assert not res.trades and res.open_positions[0]["entry"] == 3.28 and res.open_positions[0]["last"] == 3.35


def test_option_costs_are_material():
    c = option_costs()
    # 1 lot of 3000 at Rs 3 -> Rs 9000 in, Rs 9000 out: ~Rs 40 brokerage + 9 STT + fees
    cost = c.round_trip(9000, 9000)
    assert 50 < cost < 65


def test_synthetic_end_to_end_trades_something():
    eq = SyntheticDataSource()
    src = SyntheticOptionSource(eq)
    syms = ["S1", "S2", "S3", "S4", "S5", "S6"]
    trades = 0
    for d in [date(2025, 3, 3) + timedelta(days=i) for i in range(10) if (date(2025, 3, 3) + timedelta(days=i)).weekday() < 5]:
        b = {s: eq.intraday(s, d, d) for s in syms + ["NIFTY 50"]}
        h = {s: eq.daily(s, d - timedelta(days=40), d - timedelta(days=1)) for s in syms + ["NIFTY 50"]}
        c = OptionsDay(d, DayData(d, b, h), "NIFTY 50", src)
        res = simulate_options_day(TopGainerOptions({"max_candle_pct": 0, "min_body_ratio": 0.3}), c, 20000, option_costs(), "v")
        trades += len(res.trades)
    assert trades > 0
