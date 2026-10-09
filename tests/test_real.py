"""Real-order desk (algo/real.py), fully offline: shadow broker and a fake Dhan."""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from algo.costs import CostModel
from algo.clock import IST
from algo.real import RealDesk

DAY = date(2026, 10, 9)
NO_COST = CostModel(brokerage_pct=0, brokerage_cap=0, stt_sell_pct=0, exchange_pct=0, sebi_pct=0,
                    stamp_buy_pct=0, gst_pct=0, slippage_bps=0)


def at(h, m, s=20):
    return datetime.combine(DAY, time(h, m, s), tzinfo=IST)


def candles(rows, start=(9, 15)):
    """rows: (open, high, low, close) for 5-min bars from `start`."""
    t0 = datetime.combine(DAY, time(*start), tzinfo=IST)
    return pd.DataFrame([{"time": t0 + timedelta(minutes=5 * i), "open": o, "high": h, "low": lo, "close": c}
                         for i, (o, h, lo, c) in enumerate(rows)])


def res(pending=(), open_=(), trades=()):
    return SimpleNamespace(pending_orders=list(pending), open_positions=list(open_), trades=list(trades))


def order(sym="AAA", bar="09:35", side=1, stop=98.0, target=104.0):
    return {"symbol": sym, "side": side, "decided_bar": bar, "stop": stop, "target": target}


def trade(sym="AAA", entry="09:40", why="target"):
    return SimpleNamespace(symbol=sym, entry_time=entry, exit_reason=why)


@pytest.fixture
def desk(tmp_path):
    def make(mode="shadow", factory=None, **cfg):
        lines = [f"mode: {mode}", "host: testhost"] + [f"{k}: {v}" for k, v in cfg.items()]
        (tmp_path / "real.yaml").write_text("\n".join(lines) + "\n")
        return RealDesk(tmp_path / "state", tmp_path / "reports", NO_COST, config_path=tmp_path / "real.yaml",
                        kill_file=tmp_path / "KILL", live_broker_factory=factory, hostname="testhost")
    return make


class FakeDhan:
    """Records orders; market orders fill at `px`; stop orders rest until `trigger_stop`."""

    def __init__(self, px=100.0, reject=()):
        self.px, self.reject = px, set(reject)
        self.orders, self.net = {}, {}

    def tick_size(self, symbol):
        return 0.05

    def place(self, symbol, side, qty, kind, tag, trigger=None, price=None):
        oid = str(len(self.orders) + 1)
        o = {"symbol": symbol, "side": side, "qty": qty, "kind": kind, "trigger": trigger, "price": price,
             "tag": tag, "status": "PENDING", "filled": 0, "avg": 0.0}
        self.orders[oid] = o
        if kind in self.reject:
            o["status"] = "REJECTED"
        elif kind == "MARKET":
            self._fill(o, self.px)
        return oid

    def _fill(self, o, px):
        o.update(status="TRADED", filled=o["qty"], avg=px)
        self.net[o["symbol"]] = self.net.get(o["symbol"], 0) + o["side"] * o["qty"]

    def trigger_stop(self, oid, px):
        self._fill(self.orders[oid], px)

    def status(self, oid):
        o = self.orders[oid]
        return {"status": o["status"], "filled": o["filled"], "price": o["avg"], "reason": "test"}

    def cancel(self, oid):
        if self.orders[oid]["status"] == "PENDING":
            self.orders[oid]["status"] = "CANCELLED"

    def find(self, tag):
        return next((k for k, o in self.orders.items() if o["tag"] == tag), None)

    def net_qty(self, symbol):
        return self.net.get(symbol, 0)

    def health(self):
        return "fake OK"


BARS = candles([(100, 100.5, 99.5, 100)] * 6)     # 09:15..09:40, last close 100


def test_shadow_round_trip_follows_paper(desk):
    d = desk("shadow")
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    p = d.positions[0]
    assert p["status"] == "open" and p["qty"] == 100        # Rs 200 / (100 - 98)
    assert p["entry_price"] == 100 and p["sl_trigger"] == 98.0
    d.sync(DAY, at(9, 45), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: BARS)
    assert p["status"] == "open"
    up = candles([(100, 100.5, 99.5, 100)] * 6 + [(100, 104.5, 100, 104)])
    d.sync(DAY, at(9, 50), res(trades=[trade()]), lambda s: up)
    assert p["status"] == "closed" and p["exit_reason"] == "paper target"
    assert p["net"] == pytest.approx(400.0)
    assert "Realised net: **Rs 400.00**" in (d.reports_dir / "real" / f"{DAY}.md").read_text()


def test_shadow_stop_fills_from_candles(desk):
    d = desk("shadow")
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    down = candles([(100, 100.5, 99.5, 100)] * 6 + [(99.5, 99.6, 97.5, 97.8)])
    d.sync(DAY, at(9, 45), res(trades=[trade(why="stop")]), lambda s: down)
    p = d.positions[0]
    assert p["exit_reason"] == "stop (at Dhan)" and p["exit_price"] == 98.0 and p["net"] == pytest.approx(-200.0)


def test_exit_when_paper_did_not_take_the_entry(desk):
    d = desk("shadow")
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    d.sync(DAY, at(9, 45), res(), lambda s: BARS)
    assert d.positions[0]["exit_reason"] == "paper did not take the entry"


def test_limits_entries_positions_and_daily_loss(desk):
    d = desk("shadow", max_positions=2, daily_loss_limit=500)
    d.sync(DAY, at(9, 40), res(pending=[order("AAA"), order("BBB"), order("CCC")]), lambda s: BARS)
    st = {p["symbol"]: p["status"] for p in d.positions}
    assert st == {"AAA": "open", "BBB": "open", "CCC": "skipped"}
    assert "max 2 open positions" in d.positions[2]["skip_reason"]



def test_daily_loss_blocks_entries_and_hard_stop_exits_all(desk):
    d = desk("shadow", max_positions=5, daily_loss_limit=500)
    d.sync(DAY, at(9, 40), res(pending=[order("AAA"), order("BBB")]), lambda s: BARS)
    down = candles([(100, 100.5, 99.5, 100)] * 6 + [(99.5, 99.6, 97.5, 97.8)])
    # both stop out: realised -400; a new Rs 200-risk trade could take it past -500
    d.sync(DAY, at(9, 45), res(pending=[order("CCC", "09:40")], trades=[trade("AAA", why="stop"),
                                                                       trade("BBB", why="stop")]),
           lambda s: BARS if s == "CCC" else down)
    assert d.realised() == pytest.approx(-400.0)
    assert d.positions[2]["status"] == "skipped" and "daily loss limit" in d.positions[2]["skip_reason"]

    h = desk("shadow", max_positions=5, daily_loss_limit=300)
    h.state_dir = h.state_dir.parent / "h"
    h.sync(DAY, at(9, 40), res(pending=[order("AAA", stop=99.0), order("BBB", stop=99.0)]), lambda s: BARS)
    sag = candles([(100, 100.5, 99.5, 100)] * 6 + [(100, 100.1, 99.2, 99.2)])   # 2 x 200 shares x -0.8 = -320
    keep = [{"symbol": x, "entry_time": "09:40"} for x in ("AAA", "BBB")]
    h.sync(DAY, at(9, 45), res(open_=keep, pending=[order("CCC", "09:40", stop=98.0)]), lambda s: sag)
    assert all(p["status"] == "closed" and p["exit_reason"] == "daily loss limit" for p in h.positions[:2])
    assert h.halted() and len(h.positions) == 2       # and no new entry


def test_max_entries_per_day(desk):
    d = desk("shadow", max_entries_per_day=1)
    d.sync(DAY, at(9, 40), res(pending=[order("AAA"), order("BBB")]), lambda s: BARS)
    assert [p["status"] for p in d.positions] == ["open", "skipped"]


def test_squareoff_and_kill_exit_everything(desk, tmp_path):
    d = desk("shadow")
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    d.sync(DAY, at(15, 10), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: BARS)
    assert d.positions[0]["exit_reason"] == "squareoff"

    k = desk("shadow")
    k.state_dir = tmp_path / "k"
    k.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    (tmp_path / "KILL").write_text("")
    k.sync(DAY, at(9, 45), res(pending=[order("BBB", "09:40")], open_=[{"symbol": "AAA", "entry_time": "09:40"}]),
           lambda s: BARS)
    assert k.positions[0]["exit_reason"] == "kill switch"
    assert len(k.positions) == 1          # no new entries under the kill switch


def test_other_host_does_nothing(desk):
    d = desk("live", factory=lambda: FakeDhan())
    d.hostname = "github-runner"
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    assert d.positions == []


def test_off_mode_takes_no_entries(desk):
    d = desk("off")
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    assert d.positions == []


def test_live_entry_places_stop_at_broker_and_exits_on_paper_close(desk):
    fake = FakeDhan(px=100.0)
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    kinds = [(o["kind"], o["side"], o["qty"], o["trigger"]) for o in fake.orders.values()]
    assert kinds == [("MARKET", 1, 100, None), ("SL-M", -1, 100, 98.0)]
    fake.px = 104.0
    d.sync(DAY, at(9, 50), res(trades=[trade()]), lambda s: BARS)
    assert fake.orders["2"]["status"] == "CANCELLED"
    assert fake.orders["3"]["kind"] == "MARKET" and fake.orders["3"]["side"] == -1
    assert fake.net["AAA"] == 0 and d.positions[0]["net"] == pytest.approx(400.0)


def test_live_stop_filled_at_broker(desk):
    fake = FakeDhan()
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    fake.trigger_stop("2", 98.0)
    d.sync(DAY, at(9, 45), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: BARS)
    p = d.positions[0]
    assert p["exit_reason"] == "stop (at Dhan)" and p["net"] == pytest.approx(-200.0)
    assert len(fake.orders) == 2          # no extra exit order


def test_live_falls_back_to_stop_limit_then_exits(desk):
    fake = FakeDhan(reject={"SL-M"})
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    sl = fake.orders["3"]
    assert sl["kind"] == "SL" and sl["trigger"] == 98.0 and sl["price"] == pytest.approx(97.5)

    fake2 = FakeDhan(reject={"SL-M", "SL"})
    d2 = desk("live", factory=lambda: fake2)
    d2.state_dir = d2.state_dir.parent / "s2"
    d2.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    assert d2.positions[0]["status"] == "closed" and d2.positions[0]["exit_reason"] == "no stop possible"
    assert fake2.net["AAA"] == 0


def test_live_exit_never_reverses_a_flat_position(desk):
    fake = FakeDhan()
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    fake.net["AAA"] = 0                    # e.g. the owner closed it in the Dhan app
    n = len(fake.orders)
    d.sync(DAY, at(9, 50), res(trades=[trade()]), lambda s: BARS)
    # first "flat" report: not trusted yet - the stop stays, nothing is sent
    assert len(fake.orders) == n and d.positions[0]["status"] == "open"
    assert fake.orders["2"]["status"] == "PENDING"
    d.sync(DAY, at(9, 55), res(trades=[trade()]), lambda s: BARS)
    assert len(fake.orders) == n           # no market order sent
    assert d.positions[0]["status"] == "closed"
    assert fake.orders["2"]["status"] == "CANCELLED"      # and the leftover stop is gone


def test_positions_glitch_never_drops_the_stop(desk):
    fake = FakeDhan()
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    real_net = fake.net["AAA"]
    fake.net["AAA"] = 0                    # one bad positions reply
    d.sync(DAY, at(9, 45), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: BARS)
    fake.net["AAA"] = real_net             # next reply is fine again
    d.sync(DAY, at(9, 50), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: BARS)
    p = d.positions[0]
    assert p["status"] == "open" and fake.orders[p["sl_id"]]["status"] == "PENDING" and "flat_seen" not in p


def test_short_stop_limit_buys_above_trigger(desk):
    fake = FakeDhan(px=100.0, reject={"SL-M"})
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order(side=-1, stop=102.0, target=96.0)]), lambda s: BARS)
    entry, _, sl = fake.orders["1"], fake.orders["2"], fake.orders["3"]
    assert entry["side"] == -1 and sl["side"] == 1 and sl["price"] == pytest.approx(102.5)


def test_restart_keeps_positions_and_never_reenters(desk):
    fake = FakeDhan()
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    again = desk("live", factory=lambda: fake)       # new process, same files
    again.sync(DAY, at(9, 40, 50), res(pending=[order()]), lambda s: BARS)
    assert len(fake.orders) == 2 and again.positions[0]["status"] == "open"


def test_shadow_restart_restores_open_position(desk):
    d = desk("shadow")
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    again = desk("shadow")
    down = candles([(100, 100.5, 99.5, 100)] * 6 + [(99.5, 99.6, 97.5, 97.8)])
    again.sync(DAY, at(9, 45), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: down)
    assert again.positions[0]["exit_reason"] == "stop (at Dhan)"


def test_failed_exit_puts_the_stop_back(desk):
    fake = FakeDhan()
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    fake.reject.add("MARKET")             # e.g. Dhan refuses the exit
    d.sync(DAY, at(9, 50), res(trades=[trade()]), lambda s: BARS)
    p = d.positions[0]
    assert p["status"] == "open" and p["exit_pending"] == "paper target"
    assert fake.orders[p["sl_id"]]["status"] == "PENDING"      # protected again
    fake.reject.discard("MARKET")
    d.sync(DAY, at(9, 55), res(trades=[trade()]), lambda s: BARS)
    assert p["status"] == "closed" and fake.net["AAA"] == 0


def test_stop_cancelled_outside_is_placed_again(desk):
    fake = FakeDhan()
    d = desk("live", factory=lambda: fake)
    d.sync(DAY, at(9, 40), res(pending=[order()]), lambda s: BARS)
    fake.cancel("2")                      # e.g. cancelled by hand in the Dhan app
    d.sync(DAY, at(9, 45), res(open_=[{"symbol": "AAA", "entry_time": "09:40"}]), lambda s: BARS)
    p = d.positions[0]
    assert p["sl_id"] == "3" and fake.orders["3"]["kind"] == "SL-M"
