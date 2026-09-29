from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from algo.costs import CostModel
from algo.data.base import IST
from algo.data.market import DayData
from algo.data.synthetic import SyntheticDataSource, trading_days
from algo.engine import RiskConfig, build_symbol_day, simulate_day
from algo.strategies import REGISTRY, Signal, Strategy, build, clamp_params, mutate

DAY = date(2025, 3, 3)


def make_bars(closes, day=DAY, spread=0.5, volume=1000.0, opens=None):
    closes = np.asarray(closes, dtype=float)
    opens = np.asarray(opens if opens is not None else np.concatenate([[closes[0]], closes[:-1]]), dtype=float)
    start = datetime.combine(day, time(9, 15))
    t = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(len(closes))]).tz_localize(IST)
    return pd.DataFrame({
        "time": t, "open": opens, "high": np.maximum(opens, closes) + spread,
        "low": np.minimum(opens, closes) - spread, "close": closes, "volume": volume,
    })


def daily_hist(n=30, close=100.0, high=101.0, low=99.0):
    days = trading_days(DAY - timedelta(days=60), DAY - timedelta(days=1))[-n:]
    return pd.DataFrame({"date": days, "open": close, "high": high, "low": low, "close": close, "volume": 75000.0})


class OneShot(Strategy):
    """Test strategy: fire one fixed signal at a given bar."""
    family = "test"
    defaults = {"at": 3, "side": 1, "stop": 95.0, "target": 110.0, "first_entry": "09:15", "last_entry": "15:00"}

    def on_bar(self, sd, i, st):
        if i == self.p["at"]:
            return Signal(self.p["side"], self.p["stop"], self.p["target"], "test")
        return None


NO_COST = CostModel(brokerage_pct=0, brokerage_cap=0, stt_sell_pct=0, exchange_pct=0, sebi_pct=0,
                    stamp_buy_pct=0, gst_pct=0, slippage_bps=0)


def run(strategy, bars, risk=None, costs=NO_COST, equity=20000.0):
    day = DayData(DAY, {"AAA": bars}, {"AAA": daily_hist()})
    return simulate_day(strategy, day, equity, risk or RiskConfig(), costs, variant="t")


def test_fill_at_next_open_and_target():
    closes = [100] * 5 + [104, 108, 112, 112] + [112] * 60
    trades = run(OneShot(), make_bars(closes, spread=0.0))
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_time == "09:35" and t.entry == 100.0   # bar 4 open
    assert t.exit_reason == "target" and t.exit == 110.0
    # 1% risk of 20000 = 200 / 5 per share = 40 shares, capped by 5x leverage (1000 shares) -> 40
    assert t.qty == 40 and t.net == pytest.approx(400.0)


def test_stop_wins_when_both_hit_same_bar():
    closes = [100] * 5 + [100] + [100] * 60
    bars = make_bars(closes, spread=0.0)
    bars.loc[5, ["high", "low"]] = [111.0, 94.0]
    trades = run(OneShot(), bars)
    assert trades[0].exit_reason == "stop" and trades[0].exit == 95.0


def test_gap_through_stop_fills_at_open():
    closes = [100] * 5 + [90] * 60
    opens = [100] * 5 + [90] * 60
    trades = run(OneShot(), make_bars(closes, spread=0.0, opens=opens))
    assert trades[0].exit_reason == "stop" and trades[0].exit == 90.0


def test_squareoff():
    trades = run(OneShot({"target": 200.0}), make_bars([100] * 75, spread=0.0))
    assert trades[0].exit_reason == "squareoff" and trades[0].exit_time == "15:10"


def test_daily_loss_limit_blocks_new_trades():
    class Repeat(OneShot):
        def on_bar(self, sd, i, st):
            return Signal(1, sd.close[i] - 1, None, "t") if i % 4 == 0 else None

    # price collapses so every trade stops out
    closes = list(np.linspace(100, 60, 75))
    risk = RiskConfig(daily_loss_limit_pct=1.5, max_trades_per_symbol=99, max_trades_per_day=99)
    trades = run(Repeat(), make_bars(closes, spread=0.0), risk=risk)
    assert 1 <= len(trades) <= 3
    assert sum(t.net for t in trades) > -20000 * 0.03


def test_costs_round_trip_reasonable():
    c = CostModel()
    cost = c.round_trip(50_000, 50_000)
    # Rs 100k turnover intraday: brokerage 2x20 capped at 15 each -> ~30 + STT 12.5 + small fees
    assert 40 < cost < 60


@pytest.mark.parametrize("family", sorted(REGISTRY))
def test_no_lookahead(family):
    """Signals up to bar k must not change when future bars are removed."""
    src = SyntheticDataSource()
    days = trading_days(date(2025, 1, 1), date(2025, 3, 3))
    daily = src.daily("XYZ", days[0], days[-2])
    for d in days[-8:]:
        full = src.intraday("XYZ", d, d)
        hist = daily[daily["date"] < d]
        strat = build(family, {**REGISTRY[family].defaults})
        sd_full = build_symbol_day("XYZ", full, hist)
        st_full, sig_full = {}, []
        for i in range(len(sd_full)):
            s = strat.on_bar(sd_full, i, st_full)
            sig_full.append(None if s is None else (s.side, round(s.stop, 6)))
        for k in (10, 30, 50):
            sd_cut = build_symbol_day("XYZ", full.iloc[: k + 1].reset_index(drop=True), hist)
            st_cut, sig_cut = {}, []
            for i in range(k + 1):
                s = strat.on_bar(sd_cut, i, st_cut)
                sig_cut.append(None if s is None else (s.side, round(s.stop, 6)))
            assert sig_cut == sig_full[: k + 1]


@pytest.mark.parametrize("family", sorted(REGISTRY))
def test_mutation_stays_in_space(family):
    import random

    cls = REGISTRY[family]
    rng = random.Random(1)
    p = dict(cls.defaults)
    for _ in range(50):
        p = mutate(cls.space, p, rng)
        cls(p)  # must construct
        assert clamp_params(cls.space, p) == {k: p[k] for k in cls.space if k in p}
