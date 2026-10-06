"""Option price series for a (underlying, day, call/put, moneyness) request.

DhanOptionSource uses the exact listed contract when it still trades, and
Dhan's expired-options ('rolling') endpoint for older days.
SyntheticOptionSource prices options with Black-Scholes on synthetic bars.
"""
from __future__ import annotations

import calendar
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from ..clock import is_final
from ..data.base import INTRADAY_COLS, IST
from ..data.market import completed_bars

log = logging.getLogger(__name__)
ENTRY_MINUTE = 9 * 60 + 25


@dataclass
class OptionSeries:
    label: str            # e.g. "WIPRO 26OCT 500CE"
    strike: float
    expiry: date
    option_type: str      # CE / PE
    lot_size: int
    bars: pd.DataFrame    # 5-minute OHLCV for the day
    source: str           # "listed" | "rolling" | "synthetic"


class OptionSource(Protocol):
    def series(self, underlying: str, day: date, option_type: str, spot: float,
               otm_pct: float, min_expiry_days: int, otm_index: int | None = None) -> OptionSeries | None: ...


# ------------------------------------------------------------------ helpers
def monthly_expiry(year: int, month: int) -> date:
    """NSE stock options: last Tuesday of the month (last Thursday before Sep 2025).
    Exchange holidays can shift it a day earlier; listed contracts are matched by month."""
    weekday = 1 if (year, month) >= (2025, 9) else 3
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def target_expiry_month(day: date, min_expiry_days: int) -> tuple[int, int, int]:
    """(year, month, expiry_code) where code 1 = near month, 2 = next month."""
    y, m = day.year, day.month
    code = 1
    if day > monthly_expiry(y, m):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if (monthly_expiry(y, m) - day).days < min_expiry_days:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        code = 2
    return y, m, code


def pick_strike(strikes: np.ndarray, spot: float, option_type: str, otm_pct: float,
                otm_index: int | None = None) -> float | None:
    """First strike at least otm_pct% out of the money or, with otm_index, the strike at that
    0-based position in the list of strikes beyond spot (0 = nearest OTM strike)."""
    strikes = np.sort(np.unique(strikes))
    if otm_index is not None:
        cand = strikes[strikes > spot] if option_type == "CE" else strikes[strikes < spot][::-1]
        return float(cand[otm_index]) if len(cand) > otm_index else None
    if option_type == "CE":
        cand = strikes[strikes >= spot * (1 + otm_pct / 100)]
        return float(cand[0]) if len(cand) else None
    cand = strikes[strikes <= spot * (1 - otm_pct / 100)]
    return float(cand[-1]) if len(cand) else None


def strike_step(strikes: np.ndarray, spot: float) -> float:
    s = np.sort(np.unique(strikes))
    near = s[(s > spot * 0.8) & (s < spot * 1.2)]
    diffs = np.diff(near if len(near) > 2 else s)
    return float(np.median(diffs)) if len(diffs) else max(1.0, round(spot * 0.01))


def _label(und: str, expiry: date, strike: float, option_type: str) -> str:
    k = int(strike) if float(strike).is_integer() else strike
    return f"{und} {expiry.strftime('%d%b').upper()} {k}{option_type}"


# --------------------------------------------------------------------- Dhan
class DhanOptionSource:
    def __init__(self, dhan, cache_dir: str | Path, interval: int = 5):
        self.dhan = dhan
        self.cache_dir = Path(cache_dir) / "options"
        self.interval = interval
        self._contracts: dict[str, pd.DataFrame] = {}

    def _contracts_for(self, und: str) -> pd.DataFrame:
        if und not in self._contracts:
            self._contracts[und] = self.dhan.option_contracts(und)
        return self._contracts[und]

    def _cached(self, key: str, day: date, fetch) -> pd.DataFrame:
        path = self.cache_dir / f"{key}.csv"
        if is_final(day) and path.exists():
            df = pd.read_csv(path)
            df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
            return df
        df = fetch()
        if is_final(day):
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False)
        return df

    def series(self, underlying, day, option_type, spot, otm_pct, min_expiry_days, otm_index=None):
        contracts = self._contracts_for(underlying)
        if contracts.empty:
            log.info("%s has no listed stock options", underlying)
            return None
        y, m, code = target_expiry_month(day, min_expiry_days)
        listed = contracts[(contracts["option_type"] == option_type)
                           & contracts["expiry"].map(lambda e: (e.year, e.month) == (y, m) and e >= day)]
        lot = int(contracts["lot_size"].iloc[-1])
        if not listed.empty:
            strike = pick_strike(listed["strike"].to_numpy(), spot, option_type, otm_pct, otm_index)
            if strike is None:
                return None
            row = listed[listed["strike"] == strike].iloc[0]
            bars = self._cached(f"{row['security_id']}_{day}", day,
                                lambda: self.dhan.option_intraday(row["security_id"], day, day, self.interval))
            if not is_final(day):
                bars = completed_bars(bars, self.interval)
            if bars.empty:
                return None
            return OptionSeries(_label(underlying, row["expiry"], strike, option_type), strike, row["expiry"],
                                option_type, int(row["lot_size"]), bars.reset_index(drop=True), "listed")
        return self._rolling(underlying, day, option_type, spot, otm_pct, code, y, m, lot, contracts, otm_index)

    def _rolling(self, und, day, option_type, spot, otm_pct, code, y, m, lot, contracts, otm_index=None):
        step = strike_step(contracts["strike"].to_numpy(), spot)
        atm = round(spot / step) * step
        target = pick_strike(np.arange(atm - 12 * step, atm + 12.5 * step, step), spot, option_type, otm_pct, otm_index)
        if target is None:
            return None
        k = int(np.clip(round((target - atm) / step), -10, 10))
        month_start = date(day.year, day.month, 1)
        month_end = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        fetch_end = month_end if is_final(month_end) else day
        frames = []
        for off in range(max(-10, k - 2), min(10, k + 2) + 1):
            key = f"roll_{und}_{option_type}_{code}_{off:+d}_{month_start:%Y%m%d}_{fetch_end:%Y%m%d}"
            df = self._cached(key, day, lambda off=off: self.dhan.rolling_option(
                und, month_start, fetch_end, off, option_type, code, self.interval))
            if not df.empty:
                frames.append(df[df["time"].dt.date == day])
        if not frames:
            return None
        allb = pd.concat(frames, ignore_index=True)
        minute = allb["time"].dt.hour * 60 + allb["time"].dt.minute
        at_entry = allb[minute == ENTRY_MINUTE]
        if at_entry.empty:
            return None
        # the strike that ATM+k pointed to at entry time, then follow that fixed strike all day
        strikes_at_entry = at_entry["strike"].dropna().unique()
        strike = float(min(strikes_at_entry, key=lambda s: abs(s - target)))
        bars = allb[allb["strike"] == strike].drop_duplicates("time").sort_values("time").reset_index(drop=True)
        if bars.empty:
            return None
        expiry = monthly_expiry(y, m)
        return OptionSeries(_label(und, expiry, strike, option_type), strike, expiry, option_type, lot,
                            bars[INTRADAY_COLS], "rolling")


# ---------------------------------------------------------------- synthetic
def _bs(spot: np.ndarray, strike: float, t: float, vol: float, option_type: str, r: float = 0.065) -> np.ndarray:
    t = max(t, 1e-4)
    d1 = (np.log(spot / strike) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = np.vectorize(lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2))))
    if option_type == "CE":
        return spot * n(d1) - strike * math.exp(-r * t) * n(d2)
    return strike * math.exp(-r * t) * n(-d2) - spot * n(-d1)


class SyntheticOptionSource:
    def __init__(self, equity_source, vol: float = 0.35, interval: int = 5):
        self.eq = equity_source
        self.vol = vol
        self.interval = interval

    def series(self, underlying, day, option_type, spot, otm_pct, min_expiry_days, otm_index=None):
        und = self.eq.intraday(underlying, day, day, self.interval)
        if not is_final(day):
            und = completed_bars(und, self.interval)
        if und.empty:
            return None
        y, m, _ = target_expiry_month(day, min_expiry_days)
        expiry = monthly_expiry(y, m)
        step = max(1.0, float(10 ** math.floor(math.log10(spot)) / 20))
        strike = pick_strike(np.arange(round(spot / step) * step - 20 * step, spot * 1.3, step), spot, option_type, otm_pct,
                             otm_index)
        if strike is None:
            return None
        minutes_left = 375 - np.arange(len(und)) * self.interval
        t = ((expiry - day).days + minutes_left / 375) / 365
        o, h, l, c = (und[k].to_numpy() for k in ("open", "high", "low", "close"))
        price = lambda s: np.array([max(0.05, v) for v in _bs(s, strike, float(np.mean(t)), self.vol, option_type)])
        po, pc = price(o), price(c)
        up, dn = (price(h), price(l)) if option_type == "CE" else (price(l), price(h))
        bars = pd.DataFrame({"time": und["time"], "open": po, "high": np.maximum.reduce([po, pc, up]),
                             "low": np.minimum.reduce([po, pc, dn]), "close": pc, "volume": und["volume"] / 10})
        lot = int(max(1, round(1_500_000 / spot / 25)) * 25)
        return OptionSeries(_label(underlying, expiry, strike, option_type), strike, expiry, option_type, lot,
                            bars[INTRADAY_COLS], "synthetic")
