from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .engine import Trade
from .metrics import summarize
from .state import Registry, Store

# A variant must clear every bar before it is even considered for real money.
GRADUATION = {"days": 40, "trades": 30, "profit_factor": 1.3, "return_pct": 0.0, "max_dd_pct": 10.0}


def variant_stats(store: Store, variant_id: str, capital: float) -> dict:
    led = store.ledger(variant_id)
    if led.empty:
        return summarize(store.trades(variant_id), pd.Series(dtype=float), capital)
    start = float(led["equity_start"].iloc[0])
    return summarize(store.trades(variant_id), led["pnl"].astype(float).reset_index(drop=True), start)


def graduation_ready(m: dict) -> bool:
    g = GRADUATION
    return (m["days"] >= g["days"] and m["trades"] >= g["trades"] and m["profit_factor"] >= g["profit_factor"]
            and m["return_pct"] > g["return_pct"] and m["max_dd_pct"] <= g["max_dd_pct"])


def leaderboard(store: Store, reg: Registry, capital: float) -> pd.DataFrame:
    rows = []
    for v in reg.variants.values():
        m = variant_stats(store, v.id, capital)
        rows.append({
            "variant": v.id, "role": v.role, "status": v.status, **m,
            "equity": round(store.equity(v.id, capital), 2),
            "ready_for_real_money": graduation_ready(m) and v.status == "active",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["status", "return_pct"], ascending=[True, False]).reset_index(drop=True)


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_daily(reports_dir: Path, day: date, results: dict[str, list[Trade]], board: pd.DataFrame) -> Path:
    out = Path(reports_dir) / "daily" / f"{day.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(
        [{"variant": k, "trades": len(v), "net_pnl": round(sum(t.net for t in v), 2)} for k, v in sorted(results.items())]
    )
    trades = pd.DataFrame([t.to_dict() for ts in results.values() for t in ts])
    if not trades.empty:
        trades = trades[["variant", "symbol", "side", "qty", "entry_time", "entry", "exit_time", "exit", "exit_reason", "net", "r_multiple"]]
    text = [
        f"# Paper trading - {day.isoformat()}\n",
        "## Day summary\n", _md_table(summary),
        "\n## Trades\n", _md_table(trades),
        "\n## Leaderboard (since inception)\n", _md_table(board),
    ]
    out.write_text("\n".join(text))
    write_leaderboard(reports_dir, board, day)
    return out


def write_leaderboard(reports_dir: Path, board: pd.DataFrame, as_of: date) -> Path:
    out = Path(reports_dir) / "LEADERBOARD.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# Leaderboard (as of {as_of.isoformat()})\n\n"
        "Paper trading only. `ready_for_real_money` requires "
        f"{GRADUATION['days']}+ paper days, {GRADUATION['trades']}+ trades, profit factor >= {GRADUATION['profit_factor']}, "
        f"positive net return after costs and max drawdown <= {GRADUATION['max_dd_pct']}%.\n\n"
        + _md_table(board)
    )
    return out
