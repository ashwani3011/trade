from __future__ import annotations

import numpy as np

from .base import Signal, Strategy, SymbolDay


class OpeningRangeBreakout(Strategy):
    family = "orb"
    description = "Trade the first close outside the opening range (first N bars)."
    defaults = {
        "or_bars": 3,            # 3 x 5m = 15-minute opening range
        "buffer_pct": 0.05,      # breakout must clear the range by this % of price
        "target_r": 2.0,
        "stop_mode": "range",    # "range" = other side of OR, "atr" = entry -/+ atr_stop * ATR
        "atr_stop": 0.25,
        "vol_mult": 0.0,         # 0 disables the volume filter
        "vwap_filter": False,    # long only above VWAP, short only below
        "max_range_atr": 0.0,    # skip days whose OR exceeds this x ATR (0 disables)
        "first_entry": "09:30",
        "last_entry": "11:30",
    }
    space = {
        "or_bars": ("int", 2, 6),
        "buffer_pct": ("float", 0.0, 0.3),
        "target_r": ("float", 1.0, 4.0),
        "stop_mode": ("choice", ["range", "atr"]),
        "atr_stop": ("float", 0.1, 0.6),
        "vol_mult": ("float", 0.0, 3.0),
        "vwap_filter": ("bool",),
        "max_range_atr": ("float", 0.0, 1.0),
    }

    def on_bar(self, sd: SymbolDay, i: int, st: dict) -> Signal | None:
        n = self.p["or_bars"]
        if i < n or st.get("done"):
            return None
        if "hi" not in st:
            st["hi"] = float(np.max(sd.high[:n]))
            st["lo"] = float(np.min(sd.low[:n]))
            mra = self.p["max_range_atr"]
            if mra and np.isfinite(sd.atr) and st["hi"] - st["lo"] > mra * sd.atr:
                st["done"] = True
                return None
        c = sd.close[i]
        buf = self.p["buffer_pct"] / 100
        side = 1 if c > st["hi"] * (1 + buf) else -1 if c < st["lo"] * (1 - buf) else 0
        if not side:
            return None
        if self.p["vwap_filter"] and (c - sd.vwap[i]) * side <= 0:
            return None
        if not self.vol_ok(sd, i):
            return None
        st["done"] = True  # only the first qualified breakout of the day
        if self.p["stop_mode"] == "atr" and np.isfinite(sd.atr):
            stop = c - side * self.p["atr_stop"] * sd.atr
        else:
            stop = st["lo"] if side > 0 else st["hi"]
        return self.bracket(side, c, stop, self.p["target_r"], f"ORB {'up' if side > 0 else 'down'}")
