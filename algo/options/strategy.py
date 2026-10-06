"""'Top gainers at 9:25' stock-option buying strategy (from the user-supplied video).

Rules as stated in the video (base variant):
  1. At 9:25 take the top 2 gainers (NIFTY 50, % change vs previous close).
  2. Use the 10-minute chart. The first candle (9:15-9:25) must be green and one of
     the three bullish shapes - modelled as body >= half the candle's range.
     Skip a stock whose first candle is "too big".
  3. Buy an out-of-the-money call (~5-10% above spot) as soon as the first candle closes.
  4. Stop at the low of the option's first 10-minute candle; if that candle is very
     big, stop at 60% of it instead.
  5. Trades are done by ~11:30-12:00. No explicit target (examples show ~1:3).
  6. "Hedge": if the call trade goes into loss, buy an OTM put on the top loser.
"""
from __future__ import annotations

from ..strategies.base import Strategy


class TopGainerOptions(Strategy):
    family = "tg_opt"
    kind = "options"
    description = (
        "At 9:25 pick the top NIFTY 50 gainers whose first 10-min candle is a strong green candle, "
        "buy OTM calls, stop at the option's first-candle low, exit by midday. Optional put leg on top losers."
    )
    defaults = {
        "n_stocks": 2,
        "min_body_ratio": 0.5,     # candle body / range: the three 'bullish candle' shapes
        "max_candle_pct": 2.0,     # skip if first candle range > x% of price (0 = no limit)
        "max_gap_pct": 0.0,        # skip if open gapped > x% from prev close (0 = no limit)
        "vol_mult": 0.0,           # first-candle volume vs normal 10-min volume (0 = off)
        "market_filter": False,    # NIFTY's first candle must point the same way
        "otm_pct": 5.0,            # strike >= spot * (1 + otm_pct/100) for calls
        "min_expiry_days": 0,      # roll to next month if the near expiry is closer than this
        "entry_mode": "open",      # "open": 9:25 bar open, "breakout": above option's first-candle high
        "last_entry": "10:30",
        "big_candle_pct": 30.0,    # option first-candle range > x% of its high -> use stop_frac
        "stop_frac": 0.6,          # stop at high - stop_frac * range on big candles
        "target_r": 3.0,           # 0 = no target
        "trail_be_r": 0.0,         # move stop to entry after +x R (0 = off)
        "exit_time": "12:00",
        "sizing": "one_lot",       # "one_lot" (as in the video) or "risk"
        "risk_pct": 2.0,           # used when sizing == "risk"
        "put_leg": "hedge",        # "none" | "hedge" (video) | "independent" (mirror rules on top losers)
        "first_entry": "09:25",
    }
    space = {
        "n_stocks": ("int", 1, 4),
        "min_body_ratio": ("float", 0.3, 0.9),
        "max_candle_pct": ("float", 0.5, 4.0),
        "max_gap_pct": ("float", 0.0, 4.0),
        "vol_mult": ("float", 0.0, 3.0),
        "market_filter": ("bool",),
        "otm_pct": ("float", 0.0, 8.0),
        "min_expiry_days": ("int", 0, 10),
        "entry_mode": ("choice", ["open", "breakout"]),
        "big_candle_pct": ("float", 10.0, 60.0),
        "stop_frac": ("float", 0.3, 1.0),
        "target_r": ("float", 1.0, 5.0),
        "trail_be_r": ("float", 0.0, 2.0),
        "exit_time": ("choice", ["11:00", "11:30", "12:00", "13:00", "14:30"]),
        "put_leg": ("choice", ["none", "hedge", "independent"]),
    }


class FnoTopGainerCall(Strategy):
    """'F&O top gainer' call buying, as specified by the owner on 2026-10-06.

    From 09:25 rank the F&O stocks by % change vs previous close on every completed candle
    (dynamic, not frozen at 09:25) and evaluate the top 2 in rank order. A gainer qualifies
    when its 09:15-09:25 opening candle is bullish with body >= 60% of range and range
    <= 2.5 x daily ATR, the stock has touched the opening-candle high since 09:25, and an
    OTM call is listed. Buy that call (strike index 2 among strikes above spot, as in the
    source code); the stop is on the underlying: opening-candle low, or high - 60% of the
    range when the range is > 3% of the open. One trade a day, squared off at the close.
    """
    family = "tg_fno"
    kind = "fno"
    description = (
        "From 9:25 take the top 2 F&O gainers (re-ranked every candle) with a strong bullish opening candle "
        "that has touched the opening high, buy an OTM call, stop on the underlying at the opening-candle low."
    )
    defaults = {
        "n_stocks": 2,
        "min_body_ratio": 0.6,     # opening-candle body / range
        "max_range_atr": 2.5,      # opening range <= x * daily ATR
        "atr_days": 14,
        "otm_index": 2,            # 0-based index into strikes above spot (2 = third OTM strike)
        "big_candle_pct": 3.0,     # opening range > x% of its open -> stop at high - stop_frac * range
        "stop_frac": 0.6,
        "max_trades": 1,
        "first_scan": "09:25",
    }
    space: dict = {}               # fixed rules: not tuned by the weekly improvement loop
