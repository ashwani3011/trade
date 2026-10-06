"""Paper-trading engine for the F&O top-gainer call strategy (family tg_fno).

Every decision is taken on a completed 5-minute candle and filled at the next candle's
open, the same convention as the live journal ("ORDER ... at market (next candle open)"):
  bar m completes -> rank F&O stocks by close(m) vs previous close, top `n_stocks` in order;
                     the first that passes the candle filters, has traded at or above the
                     opening-candle high since 09:25 and has a listed OTM call is bought
  bar m+5 opens   -> buy the call at its open
  stop            -> a completed underlying candle with low <= stop exits at the next open
  15:10 candle    -> square off at its close (15:15, the last price Dhan provides)
The 09:15 + 09:20 five-minute candles give exactly the same 09:15-09:25 opening candle as
ten one-minute candles.
"""
from __future__ import annotations

from ..costs import CostModel
from ..indicators import atr
from ..engine import Trade
from ..strategies.base import hhmm
from .engine import SQUAREOFF, Candle, DayResult, OptionsDay, _fmt, first_candle
from .source import OptionSeries
from .strategy import FnoTopGainerCall


def _bar_map(bars) -> dict[int, tuple[float, float, float, float]]:
    m = (bars["time"].dt.hour * 60 + bars["time"].dt.minute).to_numpy()
    return {int(mi): (float(o), float(h), float(l), float(c))
            for mi, o, h, l, c in zip(m, bars["open"], bars["high"], bars["low"], bars["close"])}


def stop_level(p: dict, c: Candle) -> float:
    big = c.range / c.open * 100 > p["big_candle_pct"]
    return c.high - p["stop_frac"] * c.range if big else c.low


def simulate_fno_day(
    strategy: FnoTopGainerCall,
    ctx: OptionsDay,
    equity: float,
    costs: CostModel,
    variant: str = "",
    final: bool = True,
) -> DayResult:
    p = strategy.p
    res = DayResult()
    noted: set[str] = set()

    def note(msg: str) -> None:
        if msg not in noted:
            noted.add(msg)
            res.notes.append(msg)

    # per stock: opening candle, previous close, bars by minute
    stocks: dict[str, tuple[Candle, float, dict]] = {}
    for sym, bars in ctx.data.bars.items():
        hist = ctx.data.daily_hist.get(sym)
        if sym == ctx.index_symbol or hist is None or hist.empty:
            continue
        c = first_candle(bars)
        if c is not None:
            stocks[sym] = (c, float(hist["close"].iloc[-1]), _bar_map(bars))
    if not stocks:
        res.notes.append("no ranking possible (missing 9:15/9:20 bars or previous close)")
        return res

    first_scan = hhmm(p["first_scan"])
    data_end = max(max(b) for _, _, b in stocks.values() if b)
    no_option: set[str] = set()
    pos: dict | None = None          # open position
    order: dict | None = None        # decided entry, fills at the next open
    exit_reason: str | None = None   # decided exit, fills at the next open
    trades_taken = 0

    def qualifies(sym: str, minute: int) -> bool:
        c, _, b = stocks[sym]
        if c.close <= c.open:
            note(f"{sym}: opening candle not bullish - skipped")
            return False
        if c.body_ratio < p["min_body_ratio"]:
            note(f"{sym}: weak opening candle (body {c.body_ratio:.0%} of range) - skipped")
            return False
        a = atr(ctx.data.daily_hist[sym], int(p["atr_days"]))
        if not a > 0:
            note(f"{sym}: no daily ATR - skipped")
            return False
        if c.range > p["max_range_atr"] * a:
            note(f"{sym}: opening range {c.range:.2f} > {p['max_range_atr']} x ATR {a:.2f} - skipped")
            return False
        if max(v[1] for k, v in b.items() if first_scan <= k <= minute) < c.high:
            return False   # not yet at the opening-candle high; re-checked on later candles
        return True

    def option_for(sym: str, spot: float) -> OptionSeries | None:
        if sym in no_option:
            return None
        s = ctx.options.series(sym, ctx.day, "CE", spot, 0.0, 0, otm_index=int(p["otm_index"]))
        if s is None or s.bars.empty:
            no_option.add(sym)
            note(f"{sym}: no OTM call data - skipped")
            return None
        return s

    def close(price: float, minute: int, why: str) -> None:
        nonlocal pos
        exit_px = max(costs.slip(price, +1, entering=False), 0.05)
        gross = (exit_px - pos["entry"]) * pos["qty"]
        cost = costs.round_trip(pos["entry"] * pos["qty"], exit_px * pos["qty"])
        net = gross - cost
        res.trades.append(Trade(
            date=ctx.day.isoformat(), variant=variant, symbol=pos["series"].label, side=1, qty=pos["qty"],
            entry_time=pos["entry_time"], entry=round(pos["entry"], 2), exit_time=_fmt(minute), exit=round(exit_px, 2),
            exit_reason=why, gross=round(gross, 2), costs=round(cost, 2), net=round(net, 2),
            r_multiple=round(net / (pos["entry"] * pos["qty"]), 3),   # R = premium paid (the most a call can lose)
            reason=pos["reason"]))
        pos = None

    for minute in range(first_scan, SQUAREOFF + 5, 5):
        if not final and minute > data_end:
            break
        # 1) fills at this candle's open
        if order is not None:
            s, o = order["series"], _bar_map(order["series"].bars).get(minute)
            if o is not None:
                entry = costs.slip(o[0], +1, entering=True)
                qty = s.lot_size
                if entry * qty > equity:
                    note(f"{s.label}: 1 lot = {qty} x {entry:.2f} = Rs {entry * qty:,.0f} - not affordable on "
                         f"Rs {equity:,.0f} - skipped")
                else:
                    pos = {"series": s, "bars": _bar_map(s.bars), "und": order["und"], "stop": order["stop"],
                           "qty": qty, "entry": entry, "entry_time": _fmt(minute), "reason": order["reason"]}
                    trades_taken += 1
                order = None
            elif minute >= SQUAREOFF:
                note(f"{s.label}: no option candle to fill the order - skipped")
                order = None
        if pos is not None and exit_reason is not None:
            o = pos["bars"].get(minute)
            if o is not None:
                close(o[0], minute, exit_reason)
                exit_reason = None
        # 2) square-off on the last candle's close
        if minute >= SQUAREOFF:
            if pos is not None and minute in pos["bars"]:
                close(pos["bars"][minute][3], minute, "squareoff")
            break
        # 3) decisions on the completed candle `minute`
        if pos is not None:
            u = stocks[pos["und"]][2].get(minute)
            if u is not None and u[2] <= pos["stop"]:
                exit_reason = "stop"
            continue
        if order is not None or trades_taken >= p["max_trades"] or minute + 5 >= SQUAREOFF:
            continue
        ranked = sorted(((sym, b[minute][3] / pc * 100 - 100) for sym, (_, pc, b) in stocks.items() if minute in b),
                        key=lambda r: r[1], reverse=True)[: int(p["n_stocks"])]
        if minute == first_scan:
            note("top gainers @" + _fmt(minute + 5) + ": " + ", ".join(f"{s} {chg:+.2f}%" for s, chg in ranked))
        for sym, chg in ranked:
            if chg <= 0 or not qualifies(sym, minute):
                continue
            spot = stocks[sym][2][minute][3]
            s = option_for(sym, spot)
            if s is None:
                continue
            c = stocks[sym][0]
            order = {"series": s, "und": sym, "stop": stop_level(p, c), "decided": minute,
                     "reason": f"top F&O gainer {chg:+.2f}% @{_fmt(minute + 5)}, above opening high {c.high:.2f}"}
            break

    if pos is not None:
        last_min = max(pos["bars"])
        last = pos["bars"][last_min][3]
        if final:
            close(last, last_min, "eod")
        else:
            res.open_positions.append({
                "variant": variant, "symbol": pos["series"].label, "qty": pos["qty"], "entry_time": pos["entry_time"],
                "entry": round(pos["entry"], 2), "stop": round(pos["stop"], 2), "target": None,
                "last": round(last, 2), "unrealised": round((last - pos["entry"]) * pos["qty"], 2),
            })
    if order is not None and not final:
        res.pending_orders.append({"variant": variant, "symbol": order["series"].label, "side": 1,
                                   "decided_bar": _fmt(order["decided"]), "order": "at market (next candle open)",
                                   "reason": order["reason"]})
    if final and trades_taken == 0:
        note("no qualifying top gainer today")
    return res


__all__ = ["simulate_fno_day", "stop_level"]
