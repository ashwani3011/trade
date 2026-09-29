"""Bar-replay paper-trading engine for one strategy variant over one trading day.

Fill model (deliberately conservative):
  * a signal generated on bar i's close is filled at bar i+1's open, with slippage
  * if a bar touches both stop and target, the stop is assumed to fill first
  * a gap through the stop fills at the bar's open, not at the stop price
  * all positions are squared off at the `squareoff` bar's open (MIS)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd

from .costs import CostModel
from .data.market import DayData
from .indicators import atr, ema, vwap
from .strategies.base import Signal, Strategy, SymbolDay, hhmm

BARS_PER_DAY = 75


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0     # of current equity, lost if the stop is hit
    max_leverage: float = 5.0           # MIS intraday leverage cap on total notional
    max_positions: int = 3
    max_trades_per_day: int = 6
    max_trades_per_symbol: int = 2
    daily_loss_limit_pct: float = 3.0   # stop opening trades after this realised loss
    squareoff: str = "15:15"


@dataclass
class Trade:
    date: str
    variant: str
    symbol: str
    side: int
    qty: int
    entry_time: str
    entry: float
    exit_time: str
    exit: float
    exit_reason: str
    gross: float
    costs: float
    net: float
    r_multiple: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Position:
    side: int
    qty: int
    entry: float
    stop: float
    target: float | None
    entry_time: str
    risk_ps: float
    reason: str


def build_symbol_day(symbol: str, bars: pd.DataFrame, daily_hist: pd.DataFrame | None) -> SymbolDay:
    t = bars["time"]
    minute = (t.dt.hour * 60 + t.dt.minute).to_numpy()
    o, h, l, c, v = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    nan = float("nan")
    pdh = pdl = pdc = atr_v = d_ema = avg_vol = nan
    if daily_hist is not None and len(daily_hist) >= 1:
        last = daily_hist.iloc[-1]
        pdh, pdl, pdc = float(last["high"]), float(last["low"]), float(last["close"])
        atr_v = atr(daily_hist, 14) if len(daily_hist) >= 5 else nan
        d_ema = float(ema(daily_hist["close"].to_numpy(dtype=float), 20)[-1])
        avg_vol = float(daily_hist["volume"].tail(20).mean()) / BARS_PER_DAY
    return SymbolDay(
        symbol=symbol, minute=minute, open=o, high=h, low=l, close=c, volume=v,
        vwap=vwap(h, l, c, v), ema_fast=ema(c, 9), ema_slow=ema(c, 21),
        pdh=pdh, pdl=pdl, pdc=pdc, atr=atr_v, daily_ema=d_ema, avg_bar_volume=avg_vol,
    )


def _fmt(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def simulate_day(
    strategy: Strategy,
    day: DayData,
    equity: float,
    risk: RiskConfig,
    costs: CostModel,
    variant: str = "",
    interval: int = 5,
    final: bool = True,
    open_out: list | None = None,
    pending_out: list | None = None,
) -> list[Trade]:
    """Replay one day. With final=False (live, partial day) open positions are
    reported into `open_out` instead of being closed at the last bar."""
    sds = {s: build_symbol_day(s, b, day.daily_hist.get(s)) for s, b in day.bars.items()}
    idx = {s: {int(m): i for i, m in enumerate(sd.minute)} for s, sd in sds.items()}
    grid = sorted({m for d in idx.values() for m in d})
    squareoff = hhmm(risk.squareoff)
    states: dict[str, dict] = {s: {} for s in sds}
    pending: dict[str, Signal] = {}
    pending_at: dict[str, int] = {}
    open_pos: dict[str, _Position] = {}
    per_symbol: dict[str, int] = {}
    trades: list[Trade] = []
    realised = 0.0
    date_str = day.day.isoformat()

    def close(sym: str, price: float, minute: int, why: str) -> None:
        nonlocal realised
        pos = open_pos.pop(sym)
        price = float(price)
        exit_px = costs.slip(price, pos.side, entering=False) if why != "target" else price
        gross = (exit_px - pos.entry) * pos.side * pos.qty
        buy_v, sell_v = (pos.entry * pos.qty, exit_px * pos.qty) if pos.side > 0 else (exit_px * pos.qty, pos.entry * pos.qty)
        cost = costs.round_trip(buy_v, sell_v)
        net = gross - cost
        realised += net
        trades.append(Trade(
            date=date_str, variant=variant, symbol=sym, side=pos.side, qty=pos.qty,
            entry_time=pos.entry_time, entry=round(pos.entry, 2), exit_time=_fmt(minute),
            exit=round(exit_px, 2), exit_reason=why, gross=round(gross, 2), costs=round(cost, 2),
            net=round(net, 2), r_multiple=round(float(net / (pos.risk_ps * pos.qty)), 3), reason=pos.reason,
        ))

    for minute in grid:
        for sym in sorted(sds):
            i = idx[sym].get(minute)
            if i is None:
                continue
            sd = sds[sym]

            # 1) fill yesterday-bar signal at this bar's open
            sig = pending.pop(sym, None)
            if sig is not None and minute < squareoff and len(open_pos) < risk.max_positions:
                entry = costs.slip(float(sd.open[i]), sig.side, entering=True)
                risk_ps = (entry - sig.stop) * sig.side
                target_ok = sig.target is None or (sig.target - entry) * sig.side > 0
                if risk_ps > 0 and target_ok:
                    used = sum(p.entry * p.qty for p in open_pos.values())
                    budget = max(0.0, equity * risk.max_leverage - used)
                    qty = min(
                        math.floor(equity * risk.risk_per_trade_pct / 100 / risk_ps),
                        math.floor(budget / entry),
                    )
                    if qty >= 1:
                        open_pos[sym] = _Position(sig.side, qty, entry, sig.stop, sig.target, _fmt(minute), risk_ps, sig.reason)
                        per_symbol[sym] = per_symbol.get(sym, 0) + 1

            # 2) manage the open position on this bar
            pos = open_pos.get(sym)
            if pos is not None:
                if minute >= squareoff:
                    close(sym, sd.open[i], minute, "squareoff")
                else:
                    s = pos.side
                    stop_hit = (sd.low[i] <= pos.stop) if s > 0 else (sd.high[i] >= pos.stop)
                    tgt_hit = pos.target is not None and ((sd.high[i] >= pos.target) if s > 0 else (sd.low[i] <= pos.target))
                    if stop_hit:
                        gapped = (sd.open[i] - pos.stop) * s < 0
                        close(sym, sd.open[i] if gapped else pos.stop, minute, "stop")
                    elif tgt_hit:
                        close(sym, pos.target, minute, "target")

            # 3) ask the strategy for a new signal on this bar's close
            if sym in open_pos or minute >= squareoff:
                continue
            next_minute = minute + interval
            if not (strategy.first_entry_minute <= next_minute <= strategy.last_entry_minute):
                continue
            if len(trades) + len(open_pos) >= risk.max_trades_per_day:
                continue
            if per_symbol.get(sym, 0) >= risk.max_trades_per_symbol:
                continue
            if realised <= -equity * risk.daily_loss_limit_pct / 100:
                continue
            new = strategy.on_bar(sd, i, states[sym])
            if new is not None:
                pending[sym] = new
                pending_at[sym] = minute

    for sym in list(open_pos):  # data ended before square-off
        sd = sds[sym]
        if final:
            close(sym, sd.close[-1], int(sd.minute[-1]) + interval, "eod")
        elif open_out is not None:
            pos, last = open_pos[sym], float(sd.close[-1])
            open_out.append({
                "variant": variant, "symbol": sym, "side": pos.side, "qty": pos.qty, "entry_time": pos.entry_time,
                "entry": round(pos.entry, 2), "stop": round(pos.stop, 2),
                "target": round(pos.target, 2) if pos.target else None, "last": round(last, 2),
                "unrealised": round((last - pos.entry) * pos.side * pos.qty, 2),
            })
    if not final and pending_out is not None:
        for sym, sig in pending.items():
            pending_out.append({"variant": variant, "symbol": sym, "side": sig.side, "decided_bar": _fmt(pending_at[sym]),
                                "order": "at market (next candle open)", "stop": round(sig.stop, 2),
                                "target": round(sig.target, 2) if sig.target else None, "reason": sig.reason})
    return trades


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    cols = list(Trade.__dataclass_fields__)
    return pd.DataFrame([t.to_dict() for t in trades], columns=cols)
