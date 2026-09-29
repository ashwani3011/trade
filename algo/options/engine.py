"""Paper-trading engine for the top-gainers option-buying strategy.

Timeline for one day (5-minute bars; the '10-minute first candle' = 9:15 + 9:20 bars):
  09:25  rank NIFTY 50 by % change vs previous close using the first 10-min candle,
         apply candle filters, pick option contracts, then enter on the 9:25 bar open
         (or on a break of the option's first-candle high, in 'breakout' mode)
  after  stop / target / breakeven trail checked bar by bar (stop wins ties,
         gaps fill at the open), time exit at `exit_time`, hard square-off 15:10 (last Dhan candle)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..costs import CostModel
from ..data.market import DayData
from ..engine import Trade
from ..strategies.base import hhmm
from .source import OptionSeries, OptionSource
from .strategy import TopGainerOptions

FIRST_BARS = (9 * 60 + 15, 9 * 60 + 20)
ENTRY_MINUTE = 9 * 60 + 25
SQUAREOFF = 15 * 60 + 10   # last 5-min candle Dhan provides


@dataclass
class OptionsDay:
    day: date
    data: DayData            # NIFTY 50 constituents + index bars
    index_symbol: str
    options: OptionSource


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        return abs(self.close - self.open) / self.range if self.range > 0 else 0.0


def first_candle(bars: pd.DataFrame) -> Candle | None:
    minute = (bars["time"].dt.hour * 60 + bars["time"].dt.minute).to_numpy()
    sel = bars[np.isin(minute, FIRST_BARS)]
    if len(sel) != 2:
        return None
    return Candle(float(sel["open"].iloc[0]), float(sel["high"].max()), float(sel["low"].min()),
                  float(sel["close"].iloc[-1]), float(sel["volume"].sum()))


@dataclass
class _Pos:
    series: OptionSeries
    qty: int
    entry: float
    stop: float
    target: float | None
    risk_ps: float
    entry_time: str
    reason: str
    moved_to_be: bool = False


@dataclass
class DayResult:
    trades: list[Trade] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    open_positions: list[dict] = field(default_factory=list)
    pending_orders: list[dict] = field(default_factory=list)   # decided, waiting for the next bar to fill


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def rank(ctx: OptionsDay) -> list[tuple[str, float, Candle]]:
    """(symbol, % change at 9:25, first candle) sorted from top gainer to top loser."""
    rows = []
    for sym, bars in ctx.data.bars.items():
        if sym == ctx.index_symbol:
            continue
        hist = ctx.data.daily_hist.get(sym)
        c = first_candle(bars)
        if c is None or hist is None or hist.empty:
            continue
        prev_close = float(hist["close"].iloc[-1])
        rows.append((sym, (c.close / prev_close - 1) * 100, c))
    return sorted(rows, key=lambda r: r[1], reverse=True)


def _candle_ok(p: dict, sym: str, c: Candle, side: int, ctx: OptionsDay, notes: list[str]) -> bool:
    hist = ctx.data.daily_hist[sym]
    prev_close = float(hist["close"].iloc[-1])
    if (c.close - c.open) * side <= 0:
        notes.append(f"{sym}: first candle not {'green' if side > 0 else 'red'} - skipped")
        return False
    if c.body_ratio < p["min_body_ratio"]:
        notes.append(f"{sym}: weak candle (body {c.body_ratio:.0%} of range) - skipped")
        return False
    if p["max_candle_pct"] and c.range / c.close * 100 > p["max_candle_pct"]:
        notes.append(f"{sym}: first candle too big ({c.range / c.close * 100:.1f}%) - skipped")
        return False
    if p["max_gap_pct"] and abs(c.open / prev_close - 1) * 100 > p["max_gap_pct"]:
        notes.append(f"{sym}: gap {abs(c.open / prev_close - 1) * 100:.1f}% too large - skipped")
        return False
    if p["vol_mult"]:
        normal = float(hist["volume"].tail(20).mean()) / 37.5  # ~37.5 ten-minute bars a day
        if c.volume < p["vol_mult"] * normal:
            notes.append(f"{sym}: first-candle volume {c.volume / normal:.1f}x normal - skipped")
            return False
    if p["market_filter"]:
        idx = ctx.data.bars.get(ctx.index_symbol)
        ic = first_candle(idx) if idx is not None else None
        if ic is None or (ic.close - ic.open) * side <= 0:
            notes.append(f"{sym}: NIFTY first candle against the trade - skipped")
            return False
    return True


def simulate_options_day(
    strategy: TopGainerOptions,
    ctx: OptionsDay,
    equity: float,
    costs: CostModel,
    variant: str = "",
    final: bool = True,
) -> DayResult:
    p = strategy.p
    res = DayResult()
    ranked = rank(ctx)
    if not ranked:
        res.notes.append("no ranking possible (missing 9:15/9:20 bars or previous close)")
        return res

    exit_minute = hhmm(p["exit_time"])
    last_entry = hhmm(p["last_entry"])
    gainers = ranked[: p["n_stocks"]]
    losers = list(reversed(ranked))[: p["n_stocks"]]
    res.notes.append("top gainers @9:25: " + ", ".join(f"{s} {chg:+.2f}%" for s, chg, _ in gainers))
    res.notes.append("top losers  @9:25: " + ", ".join(f"{s} {chg:+.2f}%" for s, chg, _ in losers))

    # candidate entries: (series, option first candle, reason)
    plans: list[tuple[OptionSeries, Candle, str]] = []

    def plan(sym: str, side: int, spot: float, why: str) -> None:
        opt_type = "CE" if side > 0 else "PE"
        s = ctx.options.series(sym, ctx.day, opt_type, spot, p["otm_pct"], p["min_expiry_days"])
        if s is None:
            res.notes.append(f"{sym}: no {opt_type} option data - skipped")
            return
        oc = first_candle(s.bars)
        if oc is None or oc.high <= 0:
            res.notes.append(f"{s.label}: option has no first-candle bars - skipped")
            return
        plans.append((s, oc, why))

    for sym, chg, c in gainers:
        if _candle_ok(p, sym, c, +1, ctx, res.notes):
            plan(sym, +1, c.close, f"top gainer {chg:+.2f}%")
    if p["put_leg"] == "independent":
        for sym, chg, c in losers:
            if chg < 0 and _candle_ok(p, sym, c, -1, ctx, res.notes):
                plan(sym, -1, c.close, f"top loser {chg:+.2f}%")

    positions: dict[str, _Pos] = {}
    pending: list[tuple[OptionSeries, Candle, str]] = list(plans)
    hedge_used = p["put_leg"] != "hedge"
    realised = 0.0

    def cash_free() -> float:
        return equity + realised - sum(pp.entry * pp.qty for pp in positions.values())

    def open_pos(s: OptionSeries, oc: Candle, why: str, price: float, minute: int) -> bool:
        entry = costs.slip(price, +1, entering=True)
        big = oc.range / oc.high * 100 > p["big_candle_pct"]
        stop = oc.high - p["stop_frac"] * oc.range if big else oc.low
        risk_ps = entry - stop
        if risk_ps <= 0:
            res.notes.append(f"{s.label}: entry {entry:.2f} already below stop {stop:.2f} - skipped")
            return True  # consumed
        lot_cost = entry * s.lot_size
        if p["sizing"] == "risk":
            lots = min(math.floor(equity * p["risk_pct"] / 100 / (risk_ps * s.lot_size)), math.floor(cash_free() / lot_cost))
        else:
            lots = 1 if lot_cost <= cash_free() else 0
        if lots < 1:
            res.notes.append(
                f"{s.label}: 1 lot = {s.lot_size} x {entry:.2f} = Rs {lot_cost:,.0f}, risk Rs {risk_ps * s.lot_size:,.0f}"
                f" - not affordable within rules on Rs {equity:,.0f} - skipped")
            return True
        tgt = entry + p["target_r"] * risk_ps if p["target_r"] else None
        positions[s.label] = _Pos(s, lots * s.lot_size, entry, stop, tgt, risk_ps, _fmt(minute), why)
        return True

    def close_pos(label: str, price: float, minute: int, why: str) -> None:
        nonlocal realised
        pos = positions.pop(label)
        exit_px = price if why == "target" else costs.slip(price, +1, entering=False)
        exit_px = max(exit_px, 0.05)
        gross = (exit_px - pos.entry) * pos.qty
        cost = costs.round_trip(pos.entry * pos.qty, exit_px * pos.qty)
        net = gross - cost
        realised += net
        res.trades.append(Trade(
            date=ctx.day.isoformat(), variant=variant, symbol=pos.series.label, side=1, qty=pos.qty,
            entry_time=pos.entry_time, entry=round(pos.entry, 2), exit_time=_fmt(minute), exit=round(exit_px, 2),
            exit_reason=why, gross=round(gross, 2), costs=round(cost, 2), net=round(net, 2),
            r_multiple=round(net / (pos.risk_ps * pos.qty), 3), reason=pos.reason))

    # bar-by-bar over the union of option bar times
    series_bars: dict[str, pd.DataFrame] = {}

    def bars_of(s: OptionSeries) -> dict[int, tuple]:
        if s.label not in series_bars:
            b = s.bars
            m = (b["time"].dt.hour * 60 + b["time"].dt.minute).to_numpy()
            series_bars[s.label] = {int(mi): (float(o), float(h), float(l), float(c))
                                    for mi, o, h, l, c in zip(m, b["open"], b["high"], b["low"], b["close"])}
        return series_bars[s.label]

    grid = range(ENTRY_MINUTE, SQUAREOFF + 5, 5)
    hedge_signal_at: int | None = None
    # live (partial day): stop at the latest completed candle - later candles don't exist yet
    data_end = max(int(b["time"].dt.hour.iloc[-1] * 60 + b["time"].dt.minute.iloc[-1])
                   for b in ctx.data.bars.values() if len(b))
    for minute in grid:
        if not final and minute > data_end:
            break
        # 1) entries
        still = []
        for s, oc, why in pending:
            bar = bars_of(s).get(minute)
            if bar is None or minute > last_entry:
                if minute > last_entry:
                    res.notes.append(f"{s.label}: no entry by {p['last_entry']} - skipped")
                    continue
                still.append((s, oc, why))
                continue
            o, h, _, _ = bar
            if p["entry_mode"] == "open":
                open_pos(s, oc, why, o, minute)
            elif h > oc.high:
                open_pos(s, oc, why + ", breakout", max(o, oc.high + 0.05), minute)
            else:
                still.append((s, oc, why))
        pending = still

        # 2) hedge (video rule): a call in loss -> buy a put on the top loser at the next bar
        if hedge_signal_at is not None and minute >= hedge_signal_at:
            hedge_signal_at = None
            for sym, chg, c in losers[:1]:
                if chg < 0 and c.close < c.open:
                    s = ctx.options.series(sym, ctx.day, "PE", c.close, p["otm_pct"], p["min_expiry_days"])
                    oc = first_candle(s.bars) if s is not None else None
                    bar = bars_of(s).get(minute) if s is not None else None
                    if s is None or oc is None or bar is None:
                        res.notes.append(f"{sym}: hedge put data unavailable - skipped")
                    else:
                        open_pos(s, oc, f"hedge: top loser {chg:+.2f}%", bar[0], minute)
                else:
                    res.notes.append("hedge wanted but top loser has no red first candle - skipped")

        # 3) exits
        for label in list(positions):
            pos = positions[label]
            bar = bars_of(pos.series).get(minute)
            if bar is None:
                continue
            o, h, l, c = bar
            if minute >= SQUAREOFF or minute >= exit_minute:
                close_pos(label, o, minute, "time")
                continue
            if l <= pos.stop:
                close_pos(label, o if o < pos.stop else pos.stop, minute, "stop")
                continue
            if pos.target is not None and h >= pos.target:
                close_pos(label, pos.target, minute, "target")
                continue
            if p["trail_be_r"] and not pos.moved_to_be and h >= pos.entry + p["trail_be_r"] * pos.risk_ps:
                pos.stop, pos.moved_to_be = max(pos.stop, pos.entry), True
            if not hedge_used and pos.series.option_type == "CE" and c < pos.entry and minute + 5 <= last_entry:
                hedge_used, hedge_signal_at = True, minute + 5

    for label in list(positions):
        pos = positions[label]
        b = pos.series.bars
        last = float(b["close"].iloc[-1])
        last_min = int(b["time"].dt.hour.iloc[-1] * 60 + b["time"].dt.minute.iloc[-1])
        if final:
            close_pos(label, last, last_min, "eod")
        else:
            res.open_positions.append({
                "variant": variant, "symbol": label, "qty": pos.qty, "entry_time": pos.entry_time,
                "entry": round(pos.entry, 2), "stop": round(pos.stop, 2),
                "target": round(pos.target, 2) if pos.target else None, "last": round(last, 2),
                "unrealised": round((last - pos.entry) * pos.qty, 2),
            })
    for s, oc, why in pending:
        if final:
            res.notes.append(f"{s.label}: entry condition never triggered")
        else:
            trigger = "at market (next candle open)" if p["entry_mode"] == "open" else f"buy above {oc.high:.2f}"
            res.pending_orders.append({"variant": variant, "symbol": s.label, "side": 1, "decided_bar": "09:20",
                                       "order": trigger, "reason": why})
    return res
