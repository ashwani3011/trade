"""Causal indicators: every value at index i uses only data up to i."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.empty(len(values), dtype=float)
    if len(values) == 0:
        return out
    alpha = 2.0 / (period + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    typical = (high + low + close) / 3.0
    cum_vol = np.cumsum(volume)
    cum_pv = np.cumsum(typical * volume)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(cum_vol > 0, cum_pv / np.maximum(cum_vol, 1e-12), typical)
    return out


def atr(daily: pd.DataFrame, period: int = 14) -> float:
    """Wilder-style ATR of the last `period` daily bars (NaN if too short)."""
    if len(daily) < 2:
        return float("nan")
    h, l, c = daily["high"].to_numpy(), daily["low"].to_numpy(), daily["close"].to_numpy()
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))[1:]
    return float(np.mean(tr[-period:]))
