"""Caching loader that turns a DataSource into per-day bar sets for the engine."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .base import IST, DataSource

log = logging.getLogger(__name__)


@dataclass
class DayData:
    day: date
    bars: dict[str, pd.DataFrame]         # symbol -> that day's intraday bars
    daily_hist: dict[str, pd.DataFrame]   # symbol -> daily bars strictly before `day`


class MarketData:
    def __init__(self, source: DataSource, symbols: list[str], cache_dir: str | Path, interval: int = 5):
        self.source = source
        self.symbols = symbols
        self.interval = interval
        self.cache_dir = Path(cache_dir)
        self._daily: dict[str, pd.DataFrame] = {}

    # --------------------------------------------------------------- cache
    def _day_path(self, symbol: str, day: date) -> Path:
        return self.cache_dir / f"{self.interval}m" / symbol / f"{day.isoformat()}.csv"

    def _read_day(self, symbol: str, day: date) -> pd.DataFrame | None:
        path = self._day_path(symbol, day)
        if not path.exists():
            return None
        df = pd.read_csv(path)
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(IST)
        return df

    def _fill_cache(self, symbol: str, days: list[date]) -> None:
        missing = [d for d in days if not self._day_path(symbol, d).exists()]
        if not missing:
            return
        try:
            bars = self.source.intraday(symbol, min(missing), max(missing), self.interval)
        except Exception as exc:  # keep going with other symbols
            log.warning("intraday fetch failed for %s: %s", symbol, exc)
            return
        by_day = {d: g for d, g in bars.groupby(bars["time"].dt.date)} if not bars.empty else {}
        today = date.today()
        for d in missing:
            g = by_day.get(d)
            if g is None and d >= today:
                continue  # data may still arrive; don't cache an empty day
            path = self._day_path(symbol, d)
            path.parent.mkdir(parents=True, exist_ok=True)
            (g if g is not None else bars.iloc[0:0]).to_csv(path, index=False)

    # ---------------------------------------------------------------- public
    def load_daily(self, start: date, end: date) -> None:
        for s in self.symbols:
            try:
                self._daily[s] = self.source.daily(s, start, end)
            except Exception as exc:
                log.warning("daily fetch failed for %s: %s", s, exc)
                self._daily[s] = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    def days(self, days: list[date], history_days: int = 90) -> list[DayData]:
        """Load everything needed to simulate each of `days`. Days with no bars are skipped."""
        if not days:
            return []
        self.load_daily(min(days) - timedelta(days=history_days * 3 // 2), max(days))
        for s in self.symbols:
            self._fill_cache(s, days)
        out = []
        for d in sorted(days):
            bars = {}
            for s in self.symbols:
                df = self._read_day(s, d)
                if df is not None and len(df) >= 10:
                    bars[s] = df.reset_index(drop=True)
            if not bars:
                continue
            hist = {}
            for s in bars:
                daily = self._daily.get(s)
                if daily is not None and not daily.empty:
                    hist[s] = daily[daily["date"] < d].tail(history_days).reset_index(drop=True)
            out.append(DayData(d, bars, hist))
        return out
