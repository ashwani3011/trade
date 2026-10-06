from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from algo.costs import CostModel
from algo.data.base import IST
from algo.data.market import DayData
from algo.options.engine import OptionsDay
from algo.options.fno_engine import simulate_fno_day, stop_level
from algo.options.source import OptionSeries, pick_strike
from algo.options.strategy import FnoTopGainerCall

DAY = date(2026, 10, 6)
NO_COST = CostModel(brokerage_pct=0, brokerage_cap=0, stt_sell_pct=0, exchange_pct=0, sebi_pct=0,
                    stamp_buy_pct=0, gst_pct=0, slippage_bps=0)


def bars(rows):
    """rows: (open, high, low, close) for consecutive 5-min bars from 9:15."""
    start = datetime.combine(DAY, time(9, 15))
    t = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(len(rows))]).tz_localize(IST)
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"time": t, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": 1000.0})


def flat(price, n):
    return [(price, price, price, price)] * n


def hist(close, rng=4.0):
    """20 daily bars with true range `rng` -> daily ATR = rng."""
    d = [DAY - timedelta(days=i) for i in range(20, 0, -1)]
    return pd.DataFrame({"date": d, "open": close, "high": close + rng / 2, "low": close - rng / 2,
                         "close": close, "volume": 1e6})


# opening candle 100 -> 102.0 (bars 9:15, 9:20): range 2.2, body 2.0 (91%)
OPEN_OK = [(100, 101.2, 99.9, 101), (101, 102.1, 100.9, 102)]


class FakeOptions:
    """Option bars = 10 + (underlying - 100) * 2, so the call tracks the stock."""

    def __init__(self, und_rows, missing=()):
        self.und_rows, self.missing, self.calls = und_rows, set(missing), []

    def series(self, und, day, option_type, spot, otm_pct, min_expiry_days, otm_index=None):
        self.calls.append((und, round(spot, 2), otm_index))
        if und in self.missing:
            return None
        rows = [tuple(10 + (v - 100) * 2 for v in r) for r in self.und_rows[und]]
        return OptionSeries(f"{und} OCT 110CE", 110.0, DAY, option_type, 100, bars(rows), "fake")


def run(stocks, final=True, missing=(), equity=20000.0, **params):
    """stocks: symbol -> (bar rows, prev close[, atr])"""
    b = {s: bars(v[0]) for s, v in stocks.items()}
    h = {s: hist(v[1], *v[2:]) for s, v in stocks.items()}
    opts = FakeOptions({s: v[0] for s, v in stocks.items()}, missing)
    ctx = OptionsDay(DAY, DayData(DAY, b, h), "NIFTY 50", opts)
    return simulate_fno_day(FnoTopGainerCall(params), ctx, equity, NO_COST, variant="tg_fno.base", final=final), opts


def test_strike_index_counts_strikes_beyond_spot():
    strikes = np.array([95, 100, 105, 110, 115, 120])
    assert pick_strike(strikes, 104, "CE", 0.0, otm_index=0) == 105
    assert pick_strike(strikes, 104, "CE", 0.0, otm_index=2) == 115      # the source code's index 2 = 3rd OTM strike
    assert pick_strike(strikes, 105, "CE", 0.0, otm_index=0) == 110      # strictly above spot
    assert pick_strike(strikes, 104, "PE", 0.0, otm_index=1) == 95
    assert pick_strike(strikes, 104, "CE", 0.0, otm_index=9) is None


def test_stop_on_underlying_normal_and_big_candle():
    p = FnoTopGainerCall().p
    from algo.options.engine import Candle
    assert stop_level(p, Candle(100, 102, 99.5, 101.8, 0)) == 99.5                  # range 2.5% -> opening low
    assert round(stop_level(p, Candle(100, 104, 99.8, 103.9, 0)), 2) == round(104 - 0.6 * 4.2, 2)   # 4.2% -> 60% rule


def test_touch_of_opening_high_buys_call_next_open_and_stops_on_stock():
    rows = OPEN_OK + [(102, 102.0, 101.5, 101.8),     # 9:25 stays below the opening high 102.1
                      (101.8, 102.3, 101.7, 102.2),   # 9:30 touches -> order, fills 9:35 open
                      (102.2, 103, 102.1, 102.9),     # 9:35 entry at option open = 10 + 2.2*2 = 14.4
                      (102.9, 103, 99.8, 100.0),      # 9:40 low <= opening low 99.9 -> stop decided
                      (100.0, 100.2, 99.5, 99.6)] + flat(99.6, 66)   # 9:45 exit at option open = 10.0
    res, opts = run({"AAA": (rows, 100.0), "BBB": (flat(100.5, 72), 100.0)})
    assert len(res.trades) == 1
    t = res.trades[0]
    assert (t.entry_time, t.entry, t.exit_time, t.exit, t.exit_reason) == ("09:35", 14.4, "09:45", 10.0, "stop")
    assert t.qty == 100 and t.net == -440.0
    assert opts.calls == [("AAA", 102.2, 2)]          # strike chosen from spot at the decision candle


def test_ranking_is_dynamic_and_one_trade_a_day():
    weak = [(100, 103, 99, 100.5), (100.5, 103, 99, 101.0)] + flat(103.5, 70)    # top gainer, weak body
    late = OPEN_OK + flat(101, 3) + [(101, 102.5, 101, 102.4)] + flat(102.4, 2) + [(102.4, 104, 102.4, 104)] + flat(104, 61)
    other = OPEN_OK + flat(102.5, 70)      # 9:25 high 102.5 touches its opening high 102.1
    res, _ = run({"WEAK": (weak, 100.0), "LATE": (late, 100.0), "OTHER": (other, 100.0)})
    # 9:25: ranking WEAK (+3.5%), OTHER (+2.5%) -> WEAK fails body, OTHER touched its high -> OTHER bought
    assert [t.symbol for t in res.trades] == ["OTHER OCT 110CE"]
    assert res.trades[0].entry_time == "09:30"
    assert res.trades[0].exit_reason == "squareoff" and res.trades[0].exit_time == "15:10"
    assert any("WEAK: weak opening candle" in n for n in res.notes)


def test_gap_counts_as_touch_and_later_names_can_enter_top2():
    gap = OPEN_OK + [(105, 105, 104.9, 105)] * 70       # opens above the opening high: counts as touching
    res, _ = run({"GAP": (gap, 100.0)})
    assert res.trades and res.trades[0].entry_time == "09:30"

    below = OPEN_OK + flat(101.9, 70)          # top gainer but never reaches 102.1
    climber = OPEN_OK + flat(100.5, 10) + [(100.5, 103, 100.5, 103)] + flat(103, 59)   # enters top 2 at 10:15
    third = [(100, 100.6, 99.9, 100.5), (100.5, 101.6, 100.4, 101.5)] + flat(101.5, 70)   # 2nd at first, opening high 101.6 never hit
    res, _ = run({"BELOW": (below, 100.0), "CLIMBER": (climber, 100.0), "THIRD": (third, 100.0)})
    assert [t.symbol for t in res.trades] == ["CLIMBER OCT 110CE"]
    assert res.trades[0].entry_time == "10:20"


def test_filters_atr_not_bullish_and_missing_option():
    big = [(100, 104, 99.8, 103), (103, 106, 102.9, 105.8)] + flat(106.5, 70)   # range 6.2 > 2.5 x ATR 2
    res, _ = run({"BIG": (big, 100.0, 2.0)})
    assert not res.trades and any("ATR" in n for n in res.notes)

    red = [(102, 102.1, 100, 100.2), (100.2, 100.5, 99.9, 100.1)] + flat(103, 70)
    res, _ = run({"RED": (red, 99.0)})
    assert not res.trades and any("not bullish" in n for n in res.notes)

    res, _ = run({"AAA": (OPEN_OK + flat(102.5, 70), 100.0)}, missing={"AAA"})
    assert not res.trades and any("no OTM call" in n for n in res.notes)


def test_unaffordable_lot_is_skipped():
    res, _ = run({"AAA": (OPEN_OK + flat(102.5, 70), 100.0)}, equity=500.0)
    assert not res.trades and any("not affordable" in n for n in res.notes)


def test_live_partial_day_shows_order_then_position():
    rows = OPEN_OK + [(102, 102.5, 101.9, 102.4)]          # only 9:15..9:25 completed
    res, _ = run({"AAA": (rows, 100.0)}, final=False)
    assert not res.trades and res.pending_orders and res.pending_orders[0]["decided_bar"] == "09:25"
    res, _ = run({"AAA": (rows + [(102.4, 102.6, 102.3, 102.5)], 100.0)}, final=False)
    assert res.open_positions and res.open_positions[0]["entry_time"] == "09:30"
    assert res.open_positions[0]["stop"] == 99.9


def test_registry_seeds_base_only_for_tg_fno(tmp_path):
    from algo.state import Store
    reg = Store(tmp_path).load_registry({"tg_fno": {"base": {"n_stocks": 2}}})
    assert sorted(reg.variants) == ["tg_fno.base"]
    assert reg.champion("tg_fno") is None
