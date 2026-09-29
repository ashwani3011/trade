from __future__ import annotations

import pandas as pd

from .engine import trades_frame
from .families import simulate
from .metrics import summarize


def run(family: str, params: dict, contexts: list, start_equity: float, settings, variant: str = "backtest"
        ) -> tuple[pd.DataFrame, pd.Series, dict]:
    """Compounding multi-day replay over prepared day contexts. Returns (trades, daily pnl, summary)."""
    equity = start_equity
    all_trades, pnl = [], {}
    for ctx in contexts:
        trades = simulate(family, params, ctx, equity, settings, variant=variant).trades
        day_pnl = sum(t.net for t in trades)
        equity += day_pnl
        pnl[ctx.day] = day_pnl
        all_trades.extend(trades)
    tf = trades_frame(all_trades)
    daily = pd.Series(pnl, dtype=float)
    return tf, daily, summarize(tf, daily, start_equity)
