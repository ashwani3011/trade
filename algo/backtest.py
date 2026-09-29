from __future__ import annotations

import pandas as pd

from .costs import CostModel
from .data.market import DayData
from .engine import RiskConfig, simulate_day, trades_frame
from .metrics import summarize
from .strategies import build


def run(
    family: str,
    params: dict,
    days: list[DayData],
    start_equity: float,
    risk: RiskConfig,
    costs: CostModel,
    variant: str = "backtest",
) -> tuple[pd.DataFrame, pd.Series, dict]:
    """Compounding multi-day replay. Returns (trades, daily pnl, summary)."""
    strat = build(family, params)
    equity = start_equity
    all_trades, pnl = [], {}
    for day in days:
        trades = simulate_day(strat, day, equity, risk, costs, variant=variant)
        day_pnl = sum(t.net for t in trades)
        equity += day_pnl
        pnl[day.day] = day_pnl
        all_trades.extend(trades)
    tf = trades_frame(all_trades)
    daily = pd.Series(pnl, dtype=float)
    return tf, daily, summarize(tf, daily, start_equity)
