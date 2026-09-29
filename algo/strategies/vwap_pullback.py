from __future__ import annotations

import numpy as np

from .base import Signal, Strategy, SymbolDay


class VwapPullback(Strategy):
    family = "vwap_pb"
    description = "In an intraday trend, enter when price pulls back to VWAP and bounces."
    defaults = {
        "touch_pct": 0.1,         # low must come within this % of VWAP
        "min_trend_pct": 0.0,     # |close - day open| must exceed this % in trend direction
        "ema_filter": True,       # EMA9 above EMA21 for longs (below for shorts)
        "confirm_bar": False,     # require a bullish (bearish) close on the touch bar
        "atr_stop": 0.2,          # stop = bar extreme -/+ atr_stop * ATR
        "target_r": 1.5,
        "vol_mult": 0.0,
        "max_trades": 1,
        "first_entry": "09:45",
        "last_entry": "14:00",
    }
    space = {
        "touch_pct": ("float", 0.02, 0.4),
        "min_trend_pct": ("float", 0.0, 1.5),
        "ema_filter": ("bool",),
        "confirm_bar": ("bool",),
        "atr_stop": ("float", 0.05, 0.6),
        "target_r": ("float", 1.0, 4.0),
        "vol_mult": ("float", 0.0, 3.0),
        "max_trades": ("int", 1, 3),
    }

    def on_bar(self, sd: SymbolDay, i: int, st: dict) -> Signal | None:
        if i < 3 or st.get("n", 0) >= self.p["max_trades"] or not np.isfinite(sd.atr):
            return None
        c, v = sd.close[i], sd.vwap[i]
        side = 1 if c > v else -1
        if self.p["ema_filter"] and (sd.ema_fast[i] - sd.ema_slow[i]) * side <= 0:
            return None
        if (c / sd.open[0] - 1) * 100 * side < self.p["min_trend_pct"]:
            return None
        tol = self.p["touch_pct"] / 100
        touched = sd.low[i] <= v * (1 + tol) if side > 0 else sd.high[i] >= v * (1 - tol)
        if not touched:
            return None
        if self.p["confirm_bar"] and (c - sd.open[i]) * side <= 0:
            return None
        if not self.vol_ok(sd, i):
            return None
        extreme = min(sd.low[i], v) if side > 0 else max(sd.high[i], v)
        stop = extreme - side * self.p["atr_stop"] * sd.atr
        sig = self.bracket(side, c, stop, self.p["target_r"], f"VWAP pullback {'long' if side > 0 else 'short'}")
        if sig:
            st["n"] = st.get("n", 0) + 1
        return sig
