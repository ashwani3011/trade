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

    def bar(self, typ, strike, hm):
        return self.by.get((typ, strike, hm))

    def path(self, typ, strike, start, end):
        return [(hm, self.by[(typ, strike, hm)]) for hm in self.hms if start <= hm <= end and (typ, strike, hm) in self.by]


def short_legs(day: Day, legs: list[tuple[str, float]], entry="09:20", exit_="15:15", sl=0.3, fee=Fee(),
               hedges: list[tuple[str, float]] = ()):
    """Sell `legs` (typ, strike) at the entry bar's open, stop each leg out at +sl of its premium
    (filled at the stop, or at the bar open if it gapped beyond), buy back the rest at exit_.
    Hedges are bought at entry and sold at exit_. Returns Rs P&L for one current-size lot, or None."""
    pnl, cost = 0.0, 0.0
    for typ, k in legs:
        b = day.bar(typ, k, entry)
        if b is None:
            return None
        prem = b.open
        stop = prem * (1 + sl) if sl else None
        out_px = None
        for hm, r in day.path(typ, k, entry, exit_):
            if hm == exit_:
                out_px = r.open
                break
            if stop and r.high >= stop:
                out_px = max(stop, r.open) if hm != entry else stop
                break
        if out_px is None:
            last = day.path(typ, k, entry, "15:30")
            if not last:
                return None
            out_px = last[-1][1].close
        pnl += (prem - out_px) * LOT
        cost += fee.order(prem, LOT, "sell") + fee.order(out_px, LOT, "buy")
    for typ, k in hedges:
        b, e = day.bar(typ, k, entry), day.bar(typ, k, exit_)
        if b is None or e is None:
            return None
        pnl += (e.open - b.open) * LOT
        cost += fee.order(b.open, LOT, "buy") + fee.order(e.open, LOT, "sell")
    return pnl - cost


def orb_buy(day: Day, orb_end="09:30", last_entry="13:00", exit_="15:15", sl=0.3, tgt=0.6, fee=Fee()):
    """Buy the ATM CE on a 5-min spot close above the 09:15-09:30 high (PE below the low),
    filled at the next bar's open. Stop -sl, target +tgt of premium, flat at exit_. One trade a day."""
    s = day.spot
    rng = s[(s.index >= "09:15") & (s.index < orb_end)]
    if len(rng) < 2:
        return None
    hi, lo = rng.max(), rng.min()
    hms = [h for h in day.hms if orb_end <= h <= last_entry]
    for i, hm in enumerate(hms[:-1]):
        c = s.get(hm)
        if c is None:
            continue
        typ = "CE" if c > hi else "PE" if c < lo else None
        if not typ:
            continue
        nxt = hms[i + 1]
        k = day.atm.get(hm)
        b = day.bar(typ, k, nxt)
        if b is None:
            return None
        prem = b.open
        out = None
        for h2, r in day.path(typ, k, nxt, exit_):
            if h2 == exit_:
                out = r.open
                break
            if r.low <= prem * (1 - sl):
                out = min(prem * (1 - sl), r.open) if h2 != nxt else prem * (1 - sl)
                break
            if r.high >= prem * (1 + tgt):
                out = max(prem * (1 + tgt), r.open) if h2 != nxt else prem * (1 + tgt)
                break
        if out is None:
            return None
        return (out - prem) * LOT - fee.order(prem, LOT, "buy") - fee.order(out, LOT, "sell")
    return 0.0


def run(df: pd.DataFrame, strat, **kw) -> pd.Series:
    res = {}
    for d, g in df.groupby("day"):
        day = Day(g)
        if "09:20" not in day.atm.index:
            continue
        res[d] = strat(day, **kw)
    return pd.Series(res, dtype=float).dropna()


def straddle(day: Day, entry="09:20", width=0, wings=None, **kw):
    k = day.atm.get(entry)
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
