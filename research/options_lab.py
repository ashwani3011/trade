"""NIFTY weekly-options intraday strategy lab on real expired-option candles from Dhan.

Research only: no orders. Data: data_cache/nifty_opt/<CE|PE>_<offset>.csv, written by
research/fetch_nifty_options.py. Each row is a 5-min candle of the strike that was ATM+offset
at that moment, with its actual strike, so a fixed contract is rebuilt by selecting rows with
one strike. All strategies are intraday: enter after the open, flat by 15:15.

P&L is per 1 unit of NIFTY. It is scaled to the CURRENT lot size (65) so years with different
lot sizes are comparable. Costs per order: brokerage Rs 20, STT 0.1% of sell premium, NSE
0.03503% of premium, SEBI 0.0001%, stamp 0.003% of buy premium, GST 18% on brokerage +
exchange, and slippage per side (default 0.5% of premium, min Rs 0.05).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DATA = Path("data_cache/nifty_opt")
LOT = 65


def load() -> pd.DataFrame:
    frames = []
    for p in sorted(DATA.glob("*.csv")):
        typ, off = p.stem.split("_")
        df = pd.read_csv(p)
        df["typ"], df["off"] = typ, int(off)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    t = pd.to_datetime(df.timestamp, unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df["day"], df["hm"] = t.dt.date, t.dt.strftime("%H:%M")
    return df


@dataclass
class Fee:
    slip_pct: float = 0.005
    def order(self, premium: float, qty: int, side: str) -> float:
        """Cost in Rs of one order of `qty` units at `premium` (side 'buy'|'sell'), slippage included."""
        val = premium * qty
        brok = 20.0
        exch = val * 0.0003503
        sebi = val * 0.000001
        stt = val * 0.001 if side == "sell" else 0.0
        stamp = val * 0.00003 if side == "buy" else 0.0
        gst = 0.18 * (brok + exch + sebi)
        slip = max(0.05, premium * self.slip_pct) * qty
        return brok + exch + sebi + stt + stamp + gst + slip


class Day:
    """One trading day's option candles: price(typ, strike, hm) -> candle."""
    def __init__(self, g: pd.DataFrame):
        self.g = g
        self.by = {(r.typ, r.strike, r.hm): r for r in g.itertuples()}
        atm = g[(g.off == 0)]
        self.spot = atm.groupby("hm").spot.first()
        self.atm = atm[atm.typ == "CE"].set_index("hm").strike
        self.hms = sorted(g.hm.unique())
        self.strikes = {k: sorted(v) for k, v in g.groupby(["typ", "hm"]).strike.apply(list).items()}

    def bar(self, typ, strike, hm):
        return self.by.get((typ, strike, hm))

    def path(self, typ, strike, start, end):
        return [(hm, self.by[(typ, strike, hm)]) for hm in self.hms if start <= hm <= end and (typ, strike, hm) in self.by]


def _prev(day: Day, hm: str) -> str | None:
    """The candle that closes at `hm` (5-min candles are labelled by their start time)."""
    earlier = [h for h in day.hms if h < hm]
    return earlier[-1] if earlier else None


def _closes(day: Day, typ, strike, start, end):
    """(hm, close) for every candle from `start` to `end`, using closes only (at strike switches
    Dhan's rolling candles mix ticks of two strikes in open/high/low; closes are clean). When the
    contract has moved outside the downloaded strikes (ATM +-6), its price is taken as intrinsic
    value from the spot, a close lower bound for a deep in-the-money weekly option; without this,
    big-move days would silently drop out and flatter option selling."""
    out = []
    for hm in day.hms:
        if not (start <= hm <= end):
            continue
        r = day.bar(typ, strike, hm)
        if r is not None:
            out.append((hm, r.close))
        elif hm in day.spot.index:
            out.append((hm, _estimate(day, typ, strike, hm)))
    return out


def _intrinsic(typ, strike, spot):
    return max(0.0, spot - strike if typ == "CE" else strike - spot)


def _estimate(day: Day, typ, strike, hm):
    """Price of a contract outside the downloaded strikes: intrinsic value plus the time value of
    the nearest downloaded strike on that side (time value shrinks away from ATM, so this is an
    upper bound: a little pessimistic for sold options, a little generous for bought wings)."""
    sp = day.spot[hm]
    ks = day.strikes.get((typ, hm), [])
    if not ks:
        return max(0.05, _intrinsic(typ, strike, sp))
    k0 = ks[-1] if strike > ks[-1] else ks[0]
    tv0 = max(0.0, day.bar(typ, k0, hm).close - _intrinsic(typ, k0, sp))
    return max(0.05, _intrinsic(typ, strike, sp) + tv0)


def _price(day: Day, typ, strike, hm):
    c = _closes(day, typ, strike, hm, hm)
    return c[0][1] if c else None


def short_legs(day: Day, legs: list[tuple[str, float]], entry="09:20", exit_="15:15", sl=0.3, fee=Fee(),
               hedges: list[tuple[str, float]] = ()):
    """Sell `legs` (typ, strike) at the price at `entry` (close of the candle ending then). Each leg
    is stopped at the first 5-min close >= premium*(1+sl); the rest are bought back at `exit_`.
    Hedges are bought at entry and sold at exit_. Returns Rs P&L for one current-size lot, or None."""
    e_hm, x_hm = _prev(day, entry), _prev(day, exit_)
    if e_hm is None or x_hm is None:
        return None
    pnl, cost = 0.0, 0.0
    for typ, k in legs:
        prem = _price(day, typ, k, e_hm)
        if prem is None:
            return None
        out_px = None
        for hm, c in _closes(day, typ, k, e_hm, x_hm):
            if hm == e_hm:
                continue
            if sl and c >= prem * (1 + sl):
                out_px = c
                break
            if hm == x_hm:
                out_px = c
        if out_px is None:
            return None
        pnl += (prem - out_px) * LOT
        cost += fee.order(prem, LOT, "sell") + fee.order(out_px, LOT, "buy")
    for typ, k in hedges:
        b, e = _price(day, typ, k, e_hm), _price(day, typ, k, x_hm)
        if b is None or e is None:
            return None
        pnl += (e - b) * LOT
        cost += fee.order(b, LOT, "buy") + fee.order(e, LOT, "sell")
    return pnl - cost


def orb_buy(day: Day, orb_end="09:30", last_entry="13:00", exit_="15:15", sl=0.3, tgt=0.6, fee=Fee()):
    """Buy the ATM CE when a 5-min spot close breaks above the 09:15-09:30 high (ATM PE below the
    low), at that close's option price. Stop -sl / target +tgt on 5-min closes; flat at exit_.
    One trade a day."""
    s = day.spot
    rng = s[(s.index >= "09:15") & (s.index < orb_end)]
    if len(rng) < 2:
        return None
    hi, lo = rng.max(), rng.min()
    x_hm = _prev(day, exit_)
    for hm in [h for h in day.hms if orb_end <= h <= last_entry]:
        c = s.get(hm)
        if c is None:
            continue
        typ = "CE" if c > hi else "PE" if c < lo else None
        if not typ:
            continue
        k = day.atm.get(hm)
        b = day.bar(typ, k, hm)
        if b is None:
            return None
        prem = b.close
        out = None
        for h2, px in _closes(day, typ, k, hm, x_hm):
            if h2 == hm:
                continue
            if px <= prem * (1 - sl) or px >= prem * (1 + tgt) or h2 == x_hm:
                out = px
                break
        if out is None:
            return None
        return (out - prem) * LOT - fee.order(prem, LOT, "buy") - fee.order(out, LOT, "sell")
    return 0.0


def run(df: pd.DataFrame, strat, **kw) -> pd.Series:
    res = {}
    for d, g in df.groupby("day"):
        day = Day(g)
        if "09:15" not in day.atm.index:
            continue
        res[d] = strat(day, **kw)
    return pd.Series(res, dtype=float).dropna()


def straddle(day: Day, entry="09:20", width=0, wings=None, **kw):
    k = day.atm.get(_prev(day, entry))
    if k is None:
        return None
    legs = [("CE", k + 50 * width), ("PE", k - 50 * width)]
    hedges = [("CE", k + 50 * wings), ("PE", k - 50 * wings)] if wings else ()
    return short_legs(day, legs, entry=entry, hedges=hedges, **kw)


def summary(s: pd.Series, capital: float) -> dict:
    m = s.groupby(pd.to_datetime(pd.Series(s.index)).dt.to_period("M").values).sum()
    eq = capital + s.cumsum()
    dd = (eq - eq.cummax()).min()
    return {"days": len(s), "total": round(s.sum()), "per_month_avg": round(m.mean()), "per_month_median": round(m.median()),
            "months_pos_pct": round(100 * (m > 0).mean()), "worst_month": round(m.min()), "worst_day": round(s.min()),
            "max_dd_rs": round(dd), "win_day_pct": round(100 * (s > 0).mean())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.parse_args()
    df = load()
    print("days", df.day.nunique(), df.day.min(), "->", df.day.max())
