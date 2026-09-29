from __future__ import annotations

import numpy as np

from .base import Signal, Strategy, SymbolDay


class LevelRejection(Strategy):
    family = "sr_reject"
    description = (
        "Price-action fade: a bar pokes through yesterday's high (low) but closes back "
        "inside with a long wick - a failed breakout / pin bar at a key level."
    )
    defaults = {
        "wick_ratio": 0.5,        # rejection wick must be >= this fraction of the bar's range
        "atr_stop": 0.1,          # stop = wick extreme -/+ atr_stop * ATR
        "target_r": 2.0,
        "vol_mult": 0.0,
        "confirm_break": False,   # wait for the next bar to break the pin bar's body extreme
        "first_entry": "09:30",
        "last_entry": "14:00",
    }
    space = {
        "wick_ratio": ("float", 0.3, 0.8),
        "atr_stop": ("float", 0.0, 0.5),
        "target_r": ("float", 1.0, 4.0),
        "vol_mult": ("float", 0.0, 3.0),
        "confirm_break": ("bool",),
    }

    def on_bar(self, sd: SymbolDay, i: int, st: dict) -> Signal | None:
        if st.get("done") or not np.isfinite(sd.pdh) or not np.isfinite(sd.atr):
            return None
        pending = st.get("pending")
        if pending is not None:
            side, trigger, stop = pending
            st["pending"] = None
            if (sd.close[i] - trigger) * side > 0:
                st["done"] = True
                return self.bracket(side, sd.close[i], stop, self.p["target_r"], "level rejection (confirmed)")
        o, h, l, c = sd.open[i], sd.high[i], sd.low[i], sd.close[i]
        rng = h - l
        if rng <= 0:
            return None
        side = 0
        if h > sd.pdh and c < sd.pdh and (h - max(o, c)) >= self.p["wick_ratio"] * rng:
            side, stop, trigger = -1, h + self.p["atr_stop"] * sd.atr, min(o, c)
        elif l < sd.pdl and c > sd.pdl and (min(o, c) - l) >= self.p["wick_ratio"] * rng:
            side, stop, trigger = 1, l - self.p["atr_stop"] * sd.atr, max(o, c)
        if not side or not self.vol_ok(sd, i):
            return None
        if self.p["confirm_break"]:
            st["pending"] = (side, trigger, stop)
            return None
        st["done"] = True
        return self.bracket(side, c, stop, self.p["target_r"], "level rejection")
