"""Data source contract.

Intraday frames: columns time (tz-aware Asia/Kolkata), open, high, low, close, volume.
Daily frames:    columns date (datetime.date), open, high, low, close, volume.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

IST = "Asia/Kolkata"
INTRADAY_COLS = ["time", "open", "high", "low", "close", "volume"]
DAILY_COLS = ["date", "open", "high", "low", "close", "volume"]


class DataSource(Protocol):
    def intraday(self, symbol: str, start: date, end: date, interval: int = 5) -> pd.DataFrame:
        """Bars for trading days start..end inclusive."""

    def daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars for start..end inclusive."""


def empty_intraday() -> pd.DataFrame:
    return pd.DataFrame(columns=INTRADAY_COLS)


def empty_daily() -> pd.DataFrame:
    return pd.DataFrame(columns=DAILY_COLS)
