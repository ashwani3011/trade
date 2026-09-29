from __future__ import annotations

import numpy as np

from .base import Signal, Strategy, SymbolDay


class PrevDayBreakout(Strategy):
    family = "pdb"
    description = "Buy a close above the previous day's high / sell a close below its low."
    defaults = {
        "buffer_pct": 0.05,
        "target_r": 2.0,
        "atr_stop": 0.3,         # stop = level -/+ atr_stop * ATR
        "vol_mult": 0.0,
        "trend_filter": False,   # longs only above daily EMA20, shorts only below
        "skip_gap": False,       # skip if the day opened beyond the level (move already happened)
        "first_entry": "09:30",
        "last_entry": "13:30",
    }
    space = {
        "buffer_pct": ("float", 0.0, 0.3),
        "target_r": ("float", 1.0, 4.0),
        "atr_stop": ("float", 0.1, 0.8),
        "vol_mult": ("float", 0.0, 3.0),
        "trend_filter": ("bool",),
        "skip_gap": ("bool",),
    }

    def on_bar(self, sd: SymbolDay, i: int, st: dict) -> Signal | None:
        if i < 1 or st.get("done") or not np.isfinite(sd.pdh) or not np.isfinite(sd.atr):
            return None
        buf = self.p["buffer_pct"] / 100
        c, pc = sd.close[i], sd.close[i - 1]
        side = 0
        if c > sd.pdh * (1 + buf) and pc <= sd.pdh * (1 + buf):
            side, level = 1, sd.pdh
        elif c < sd.pdl * (1 - buf) and pc >= sd.pdl * (1 - buf):
            side, level = -1, sd.pdl
        if not side:
            return None
        if self.p["skip_gap"] and (sd.open[0] - level) * side > 0:
            return None
        if self.p["trend_filter"] and np.isfinite(sd.daily_ema) and (sd.pdc - sd.daily_ema) * side <= 0:
            return None
        if not self.vol_ok(sd, i):
            return None
        st["done"] = True
        stop = level - side * self.p["atr_stop"] * sd.atr
        return self.bracket(side, c, stop, self.p["target_r"], f"PD{'H' if side > 0 else 'L'} break")
