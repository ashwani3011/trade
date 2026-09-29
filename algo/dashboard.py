"""Snapshot of the paper desk for the dashboard page (dashboard/data.json).

Read-only: collects the registry, leaderboard, equity ledgers, closed trades,
today's live decisions and trader status into one JSON file.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import report
from .clock import IST, market_phase, today_ist
from .config import Settings
from .live import running_pid
from .state import Store


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records")) if not df.empty else []


def build(s: Settings, strategies: dict) -> dict:
    store = Store(s.state_dir)
    reg = store.load_registry(strategies)
    board = report.leaderboard(store, reg, s.capital)
    variants, equity, trades = [], {}, []
    for v in reg.variants.values():
        variants.append({"id": v.id, "family": v.family, "role": v.role, "status": v.status, "note": v.note,
                         "params": v.params, "created": v.created, "parent": v.parent,
                         "retired_reason": v.retired_reason})
        equity[v.id] = _records(store.ledger(v.id))
        trades += _records(store.trades(v.id))
    today = today_ist()
    journal = Path(s.state_dir) / "live" / f"{today.isoformat()}.jsonl"
    live = [json.loads(x) for x in journal.read_text().splitlines() if x.strip()] if journal.exists() else []
    return {
        "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "market_phase": market_phase(),
        "trader_running": running_pid(s) is not None,
        "capital": s.capital,
        "leaderboard": _records(board),
        "variants": variants,
        "equity": equity,
        "trades": sorted(trades, key=lambda t: (t["date"], t["entry_time"])),
        "live_today": live,
        "families": sorted({v.family for v in reg.variants.values()}),
    }


def write(s: Settings, strategies: dict, out: Path = Path("dashboard/data.json")) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(s, strategies), indent=1, default=str))
    return out
