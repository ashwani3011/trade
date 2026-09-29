"""Strategy plugin contract.

A strategy sees one symbol-day at a time through `SymbolDay` arrays and is
called once per closed bar with index `i`. It must only read indices <= i
(tests enforce this). A returned Signal is filled at the NEXT bar's open.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np


@dataclass
class SymbolDay:
    symbol: str
    minute: np.ndarray        # minutes since midnight (IST) of each bar's start
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    vwap: np.ndarray
    ema_fast: np.ndarray      # EMA(9) of 5-minute closes
    ema_slow: np.ndarray      # EMA(21) of 5-minute closes
    pdh: float                # previous day high / low / close
    pdl: float
    pdc: float
    atr: float                # 14-day ATR from daily bars
    daily_ema: float          # 20-day EMA of daily closes (as of yesterday)
    avg_bar_volume: float     # 20-day average daily volume / bars per day

    def __len__(self) -> int:
        return len(self.close)


@dataclass
class Signal:
    side: int                 # +1 long, -1 short
    stop: float
    target: float | None
    reason: str = ""


def hhmm(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


class Strategy:
    family: ClassVar[str] = ""
    description: ClassVar[str] = ""
    defaults: ClassVar[dict[str, Any]] = {}
    # name -> ("float", lo, hi) | ("int", lo, hi) | ("bool",) | ("choice", [options])
    space: ClassVar[dict[str, tuple]] = {}

    def __init__(self, params: dict[str, Any] | None = None):
        params = dict(params or {})
        unknown = set(params) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.family}: unknown params {sorted(unknown)}")
        self.p = {**self.defaults, **params}
        self.first_entry_minute = hhmm(self.p.get("first_entry", "09:20"))
        self.last_entry_minute = hhmm(self.p.get("last_entry", "14:30"))

    def on_bar(self, sd: SymbolDay, i: int, st: dict) -> Signal | None:
        raise NotImplementedError

    # ---------------------------------------------------------- helpers
    def vol_ok(self, sd: SymbolDay, i: int) -> bool:
        mult = self.p.get("vol_mult", 0)
        if not mult or not np.isfinite(sd.avg_bar_volume) or sd.avg_bar_volume <= 0:
            return True
        return sd.volume[i] >= mult * sd.avg_bar_volume

    def bracket(self, side: int, entry: float, stop: float, target_r: float, reason: str) -> Signal | None:
        risk = (entry - stop) * side
        if risk <= entry * 0.0005:  # stop too tight to be meaningful after costs
            return None
        target = entry + side * risk * target_r if target_r and target_r > 0 else None
        return Signal(side, stop, target, reason)


def clamp_params(space: dict[str, tuple], params: dict[str, Any]) -> dict[str, Any]:
    """Coerce values into the declared space; drops keys that aren't tunable."""
    out = {}
    for k, v in params.items():
        spec = space.get(k)
        if spec is None:
            continue
        kind = spec[0]
        if kind == "float":
            out[k] = round(float(min(max(float(v), spec[1]), spec[2])), 4)
        elif kind == "int":
            out[k] = int(min(max(int(round(float(v))), spec[1]), spec[2]))
        elif kind == "bool":
            out[k] = bool(v)
        elif kind == "choice" and v in spec[1]:
            out[k] = v
    return out


def mutate(space: dict[str, tuple], params: dict[str, Any], rng: random.Random, n_changes: int = 2) -> dict[str, Any]:
    """Random local perturbation of a parameter set within its space."""
    new = dict(params)
    keys = rng.sample(sorted(space), k=min(n_changes, len(space)))
    for k in keys:
        spec = space[k]
        kind = spec[0]
        cur = new.get(k)
        if kind == "float":
            width = (spec[2] - spec[1]) * 0.2
            base = float(cur) if cur is not None else (spec[1] + spec[2]) / 2
            new[k] = base + rng.uniform(-width, width)
        elif kind == "int":
            base = int(cur) if cur is not None else (spec[1] + spec[2]) // 2
            new[k] = base + rng.choice([-2, -1, 1, 2])
        elif kind == "bool":
            new[k] = not bool(cur)
        elif kind == "choice":
            new[k] = rng.choice(spec[1])
    return {**params, **clamp_params(space, new)}
