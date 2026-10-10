"""Daily-bar (swing / positional) strategy lab on real NSE data from Dhan.

Research only: no orders. Reads data_cache/daily/<SYMBOL>.csv (research/fetch_daily.py).
Every strategy trades a Rs 20,000 cash (delivery, CNC) account with whole shares,
decides on a day's close and fills at the NEXT day's open, and pays Dhan delivery charges:
  brokerage 0, STT 0.1% buy + 0.1% sell, NSE txn 0.00297%, SEBI 0.0001%, stamp 0.015% (buy),
  GST 18% on txn + SEBI, DP Rs 15.93 per scrip sold, plus slippage per side.

Caveat: the universe is today's F&O list, so stocks that later fell out are missing
(survivorship bias). Every result is therefore also compared with simply holding the same
universe equal-weighted, which carries the same bias.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data_cache/daily")
INDEX = "NIFTY 50"


# ------------------------------------------------------------------ data
def load_panel(min_years: float = 1.0) -> dict[str, pd.DataFrame]:
    frames = {}
    for p in sorted(DATA.glob("*.csv")):
        df = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
        if len(df) >= 250 * min_years:
            frames[p.stem] = df
    fields = ["open", "high", "low", "close", "volume"]
    idx = frames[INDEX].index
    return {f: pd.DataFrame({s: d[f] for s, d in frames.items()}).reindex(idx) for f in fields}


# ------------------------------------------------------------------ costs
@dataclass
class Costs:
    slippage: float = 0.0005     # 0.05% per side
    dp_per_sell: float = 15.93   # Rs 13.5 + GST per scrip per sell day

    def buy(self, value: float) -> float:
        txn, sebi = value * 0.0000297, value * 0.000001
        return value * (0.001 + 0.00015 + self.slippage) + txn + sebi + 0.18 * (txn + sebi)

    def sell(self, value: float) -> float:
        txn, sebi = value * 0.0000297, value * 0.000001
        return value * (0.001 + self.slippage) + txn + sebi + 0.18 * (txn + sebi) + self.dp_per_sell


# ------------------------------------------------------------------ engine
@dataclass
class Book:
    cash: float
    costs: Costs
    pos: dict[str, int] = field(default_factory=dict)
    entry: dict[str, tuple] = field(default_factory=dict)   # sym -> (date, price)
    trades: list[dict] = field(default_factory=list)
    paid: float = 0.0

    def buy(self, sym, qty, px, day):
        if qty < 1:
            return
        value = qty * px
        c = self.costs.buy(value)
        if value + c > self.cash:
            qty = int((self.cash - 20) / (px * (1 + 0.0017 + self.costs.slippage)))
            if qty < 1:
                return
            value = qty * px
            c = self.costs.buy(value)
        self.cash -= value + c
        self.paid += c
        self.pos[sym] = self.pos.get(sym, 0) + qty
        self.entry.setdefault(sym, (day, px))

    def sell(self, sym, px, day, why=""):
        qty = self.pos.pop(sym, 0)
        if qty < 1:
            return
        value = qty * px
        c = self.costs.sell(value)
        self.cash += value - c
        self.paid += c
        d0, p0 = self.entry.pop(sym)
        self.trades.append({"sym": sym, "entry_date": d0, "exit_date": day, "entry": p0, "exit": px,
                            "qty": qty, "ret": px / p0 - 1, "why": why})

    def value(self, closes: pd.Series) -> float:
        return self.cash + sum(q * closes.get(s, np.nan) for s, q in self.pos.items() if np.isfinite(closes.get(s, np.nan)))


def stats(eq: pd.Series, trades: list[dict], paid: float) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    ret = eq.pct_change().dropna()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    t = pd.DataFrame(trades)
    return {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()), "final": round(eq.iloc[-1]),
            "cagr_pct": round(100 * cagr, 1), "max_dd_pct": round(100 * dd, 1),
            "sharpe": round(ret.mean() / ret.std() * math.sqrt(250), 2) if ret.std() > 0 else 0,
            "trades": len(t), "win_pct": round(100 * (t.ret > 0).mean(), 1) if len(t) else 0,
            "costs": round(paid)}


def yearly(eq: pd.Series) -> dict:
    eq = eq.dropna()
    y = eq.groupby(eq.index.year).last()
    prev = pd.concat([pd.Series([eq.iloc[0]], index=[y.index[0] - 1]), y]).shift(1).dropna()
    return {int(k): round(100 * (y[k] / prev[k] - 1), 1) for k in y.index}


def sma(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def rsi(close: pd.DataFrame, n: int) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ------------------------------------------------------------------ strategies
def run_momentum(P, start, end, capital=20000, top_n=5, lookback=126, skip=21, vol_adj=True,
                 regime=True, rebalance="M", costs=None, hold_buffer=2):
    """Monthly rotation into the top-N stocks by (vol-adjusted) 6-12m momentum, skipping the
    last month. Cash when NIFTY is below its 200-day average (regime filter)."""
    costs = costs or Costs()
    C, O = P["close"], P["open"]
    stocks = [c for c in C.columns if c != INDEX]
    mom = C[stocks].shift(skip) / C[stocks].shift(lookback) - 1
    if vol_adj:
        mom = mom / (C[stocks].pct_change().rolling(lookback).std() * math.sqrt(250))
    liquid = (C[stocks] * P["volume"][stocks]).rolling(20).median() > 5e7   # > Rs 5 cr/day
    nifty_ok = C[INDEX] > sma(C[[INDEX]], 200)[INDEX]
    days = C.loc[start:end].index
    period = days.to_period(rebalance)
    rebal_days = set(days[np.r_[True, period[1:] != period[:-1]]])
    book, eq, pending = Book(capital, costs), {}, None
    for i, d in enumerate(days):
        if pending is not None:                       # execute yesterday's decision at today's open
            target = pending
            for s in list(book.pos):
                if s not in target and np.isfinite(O.at[d, s]):
                    book.sell(s, O.at[d, s], d, "rebalance")
            new = [s for s in target if s not in book.pos and np.isfinite(O.at[d, s])]
            if new:
                slot = book.value(O.loc[d]) / max(1, top_n)
                for s in new:
                    book.buy(s, int(slot / O.at[d, s]), O.at[d, s], d)
            pending = None
        if d in rebal_days and i + 1 < len(days):
            if regime and not nifty_ok.get(d, False):
                pending = []
            else:
                m = mom.loc[d][liquid.loc[d].fillna(False)].dropna().sort_values(ascending=False)
                ranked = list(m.index)
                keep = [s for s in book.pos if s in ranked[: top_n + hold_buffer]]   # hysteresis cuts turnover
                pending = (keep + [s for s in ranked if s not in keep])[:top_n]
        eq[d] = book.value(C.loc[d])
    return pd.Series(eq), book


def run_meanrev(P, start, end, capital=20000, slots=4, rsi_n=2, entry_rsi=10, exit_sma=5, max_hold=10,
                trend_sma=200, regime=True, costs=None, stop_pct=None):
    """Connors-style pullback: in an uptrend (close > 200-day SMA), buy when 2-day RSI < 10,
    sell when the close gets back above the 5-day SMA or after 10 days."""
    costs = costs or Costs()
    C, O = P["close"], P["open"]
    stocks = [c for c in C.columns if c != INDEX]
    r = rsi(C[stocks], rsi_n)
    up = C[stocks] > sma(C[stocks], trend_sma)
    ex = sma(C[stocks], exit_sma)
    liquid = (C[stocks] * P["volume"][stocks]).rolling(20).median() > 5e7
    nifty_ok = C[INDEX] > sma(C[[INDEX]], 200)[INDEX]
    days = C.loc[start:end].index
    book, eq, to_buy, to_sell = Book(capital, costs), {}, [], []
    held_days: dict[str, int] = {}
    for i, d in enumerate(days):
        for s in to_sell:
            if s in book.pos and np.isfinite(O.at[d, s]):
                book.sell(s, O.at[d, s], d, "exit")
                held_days.pop(s, None)
        free = slots - len(book.pos)
        for s in to_buy[:max(0, free)]:
            if np.isfinite(O.at[d, s]):
                slot = book.value(O.loc[d].fillna(C.loc[d])) / slots
                book.buy(s, int(min(slot, book.cash) / O.at[d, s]), O.at[d, s], d)
                held_days[s] = 0
        to_buy, to_sell = [], []
        for s in list(book.pos):
            held_days[s] = held_days.get(s, 0) + 1
            c = C.at[d, s]
            stopped = stop_pct and book.entry[s][1] and c < book.entry[s][1] * (1 - stop_pct)
            if c > ex.at[d, s] or held_days[s] >= max_hold or stopped:
                to_sell.append(s)
        if (not regime or nifty_ok.get(d, False)) and len(book.pos) - len(to_sell) < slots:
            cand = r.loc[d][(r.loc[d] < entry_rsi) & up.loc[d] & liquid.loc[d].fillna(False)]
            to_buy = [s for s in cand.sort_values().index if s not in book.pos]
        eq[d] = book.value(C.loc[d])
    return pd.Series(eq), book


def run_breakout(P, start, end, capital=20000, slots=5, entry_n=55, exit_n=20, regime=True, costs=None):
    """Donchian trend following: buy a close above the prior 55-day high (strongest first),
    sell a close below the prior 20-day low."""
    costs = costs or Costs()
    C, O = P["close"], P["open"]
    stocks = [c for c in C.columns if c != INDEX]
    hi = C[stocks].rolling(entry_n).max().shift(1)
    lo = C[stocks].rolling(exit_n).min().shift(1)
    strength = C[stocks] / C[stocks].shift(126) - 1
    liquid = (C[stocks] * P["volume"][stocks]).rolling(20).median() > 5e7
    nifty_ok = C[INDEX] > sma(C[[INDEX]], 200)[INDEX]
    days = C.loc[start:end].index
    book, eq, to_buy, to_sell = Book(capital, costs), {}, [], []
    for d in days:
        for s in to_sell:
            if s in book.pos and np.isfinite(O.at[d, s]):
                book.sell(s, O.at[d, s], d, "exit")
        free = slots - len(book.pos)
        for s in to_buy[:max(0, free)]:
            if np.isfinite(O.at[d, s]):
                slot = book.value(O.loc[d].fillna(C.loc[d])) / slots
                book.buy(s, int(min(slot, book.cash) / O.at[d, s]), O.at[d, s], d)
        to_buy = []
        to_sell = [s for s in book.pos if C.at[d, s] < lo.at[d, s]]
        if not regime or nifty_ok.get(d, False):
            sig = (C.loc[d, stocks] > hi.loc[d]) & liquid.loc[d].fillna(False)
            cand = strength.loc[d][sig[sig].index].dropna().sort_values(ascending=False)
            to_buy = [s for s in cand.index if s not in book.pos]
        eq[d] = book.value(C.loc[d])
    return pd.Series(eq), book


def run_hold(P, start, end, capital=20000, symbols=None):
    """Benchmark: buy and hold (NIFTY 50 index, or the whole universe equal-weighted)."""
    C = P["close"].loc[start:end]
    if symbols == [INDEX]:
        s = C[INDEX].dropna()
        return capital * s / s.iloc[0]
    stocks = [c for c in C.columns if c != INDEX]
    first = C[stocks].apply(lambda col: col.dropna().iloc[0] if col.notna().any() else np.nan)
    norm = C[stocks].ffill() / first
    avail = C[stocks].notna().iloc[0]
    return capital * norm.loc[:, avail].mean(axis=1)


STRATS = {"momentum": run_momentum, "meanrev": run_meanrev, "breakout": run_breakout}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-10-09")
    args = ap.parse_args()
    P = load_panel()
    print("universe", P["close"].shape[1] - 1, "stocks,", P["close"].index[0].date(), "->", P["close"].index[-1].date())
    for name, fn in STRATS.items():
        eq, book = fn(P, args.start, args.end)
        print(name, stats(eq, book.trades, book.paid), yearly(eq))
    for label, syms in (("NIFTY 50 hold", [INDEX]), ("universe EW hold", None)):
        eq = run_hold(P, args.start, args.end, symbols=syms)
        print(label, stats(eq, [], 0), yearly(eq))
