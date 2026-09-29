"""Deterministic synthetic market for offline testing and demos.

Not a model of real prices - it exists so the whole pipeline (engine, ledgers,
improvement loop) can run end-to-end without broker credentials.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .base import DAILY_COLS, INTRADAY_COLS, IST, empty_daily, empty_intraday

BARS_PER_DAY = 75  # 09:15 .. 15:25 in 5-minute bars


def _seed(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "little")


def trading_days(start: date, end: date) -> list[date]:
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


class SyntheticDataSource:
    def __init__(self, base_price: float = 1000.0):
        self.base_price = base_price
        self._bars_cache: dict[tuple[str, date, int], pd.DataFrame] = {}

    def _open_price(self, symbol: str, day: date) -> float:
        # Price path anchored to a symbol-specific drift so days chain smoothly.
        rng = np.random.default_rng(_seed(symbol, "anchor"))
        start_price = self.base_price * rng.uniform(0.2, 3.0)
        n = (day - date(2020, 1, 1)).days
        drift = np.sin(n / 45 + rng.uniform(0, 6)) * 0.15 + n * 0.00005
        noise = np.random.default_rng(_seed(symbol, day, "gap")).normal(0, 0.006)
        return float(start_price * np.exp(drift + noise))

    def _day_bars(self, symbol: str, day: date, interval: int) -> pd.DataFrame:
        key = (symbol, day, interval)
        if key not in self._bars_cache:
            self._bars_cache[key] = self._make_day_bars(symbol, day, interval)
        return self._bars_cache[key]

    def _make_day_bars(self, symbol: str, day: date, interval: int) -> pd.DataFrame:
        rng = np.random.default_rng(_seed(symbol, day, interval))
        n = BARS_PER_DAY * 5 // interval
        trend = rng.choice([-1, 0, 0, 1]) * rng.uniform(0.00003, 0.00025)
        vol = rng.uniform(0.0008, 0.0022)
        rets = rng.normal(trend, vol, n)
        close = self._open_price(symbol, day) * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[close[0] / np.exp(rets[0])], close[:-1]])
        high = np.maximum(open_, close) * (1 + rng.exponential(vol * 0.6, n))
        low = np.minimum(open_, close) * (1 - rng.exponential(vol * 0.6, n))
        u_shape = 1.5 + np.cos(np.linspace(0, 2 * np.pi, n))
        volume = np.round(u_shape * rng.lognormal(10, 0.4, n))
        start = datetime.combine(day, time(9, 15))
        times = pd.DatetimeIndex([start + timedelta(minutes=interval * i) for i in range(n)]).tz_localize(IST)
        return pd.DataFrame(
            {"time": times, "open": open_, "high": high, "low": low, "close": close, "volume": volume}
        )[INTRADAY_COLS]

    def intraday(self, symbol: str, start: date, end: date, interval: int = 5) -> pd.DataFrame:
        frames = [self._day_bars(symbol, d, interval) for d in trading_days(start, end)]
        return pd.concat(frames, ignore_index=True) if frames else empty_intraday()

    def daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        rows = []
        for d in trading_days(start, end):
            b = self._day_bars(symbol, d, 5)
            rows.append((d, b["open"].iloc[0], b["high"].max(), b["low"].min(), b["close"].iloc[-1], b["volume"].sum()))
        return pd.DataFrame(rows, columns=DAILY_COLS) if rows else empty_daily()
