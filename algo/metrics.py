from __future__ import annotations

import math

import numpy as np
import pandas as pd


def summarize(trades: pd.DataFrame, daily_pnl: pd.Series, start_equity: float) -> dict:
    """Performance summary. `daily_pnl` is net P&L per trading day (zeros included)."""
    n = len(trades)
    net = float(trades["net"].sum()) if n else 0.0
    wins = trades.loc[trades["net"] > 0, "net"] if n else pd.Series(dtype=float)
    losses = trades.loc[trades["net"] <= 0, "net"] if n else pd.Series(dtype=float)
    equity = start_equity + daily_pnl.cumsum()
    peak = np.maximum.accumulate(np.concatenate([[start_equity], equity.to_numpy()]))
    dd = (peak[1:] - equity.to_numpy()) / peak[1:] if len(equity) else np.array([0.0])
    rets = daily_pnl / (start_equity + daily_pnl.cumsum().shift(fill_value=0))
    sharpe = float(rets.mean() / rets.std() * math.sqrt(250)) if len(rets) > 2 and rets.std() > 0 else 0.0
    gross_loss = float(-losses.sum())
    return {
        "days": int(len(daily_pnl)),
        "trades": n,
        "net_pnl": round(net, 2),
        "return_pct": round(net / start_equity * 100, 2),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "profit_factor": round(float(wins.sum()) / gross_loss, 2) if gross_loss > 0 else (float("inf") if n else 0.0),
        "avg_r": round(float(trades["r_multiple"].mean()), 3) if n else 0.0,
        "max_dd_pct": round(float(dd.max()) * 100, 2) if len(dd) else 0.0,
        "sharpe": round(sharpe, 2),
        "costs": round(float(trades["costs"].sum()), 2) if n else 0.0,
    }


def score(m: dict, min_trades: int) -> float:
    """Single number used to rank variants: return penalised by drawdown."""
    if m["trades"] < min_trades:
        return float("-inf")
    return m["return_pct"] - 0.5 * m["max_dd_pct"]
