"""Persistent state: the variant registry plus one ledger per variant.

state/registry.json            all variants ever created, with role and status
state/ledgers/<variant>.csv    one row per paper-traded day (equity curve)
state/trades/<variant>.csv     every paper trade
state/improve_log.jsonl        audit trail of every improvement decision
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .engine import Trade, trades_frame
from .families import FAMILIES as REGISTRY

LEDGER_COLS = ["date", "equity_start", "pnl", "equity_end", "trades"]


@dataclass
class Variant:
    id: str
    family: str
    params: dict
    role: str                 # baseline | champion | challenger
    status: str = "active"    # active | retired
    created: str = ""
    parent: str | None = None
    note: str = ""
    retired_reason: str = ""


@dataclass
class Registry:
    variants: dict[str, Variant] = field(default_factory=dict)

    def active(self) -> list[Variant]:
        return [v for v in self.variants.values() if v.status == "active"]

    def champion(self, family: str) -> Variant | None:
        return next((v for v in self.active() if v.family == family and v.role == "champion"), None)

    def challengers(self, family: str) -> list[Variant]:
        return [v for v in self.active() if v.family == family and v.role == "challenger"]

    def next_id(self, family: str) -> str:
        n = sum(1 for v in self.variants.values() if v.family == family and v.role in ("challenger", "champion"))
        return f"{family}.v{n + 1}"


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "ledgers").mkdir(parents=True, exist_ok=True)
        (self.root / "trades").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ registry
    @property
    def registry_path(self) -> Path:
        return self.root / "registry.json"

    def load_registry(self, strategies_cfg: dict) -> Registry:
        reg = Registry()
        if self.registry_path.exists():
            raw = json.loads(self.registry_path.read_text())
            reg = Registry({k: Variant(**v) for k, v in raw["variants"].items()})
        known = {v.family for v in reg.variants.values()}
        new = {f: spec for f, spec in strategies_cfg.items() if f not in known}
        if new or not self.registry_path.exists():
            self._seed(reg, new)
            self.save_registry(reg)
        return reg

    def _seed(self, reg: Registry, cfg: dict) -> None:
        """Each new family gets a fixed 'base' benchmark and, if it has one, an 'enhanced' champion."""
        today = date.today().isoformat()
        for family, spec in cfg.items():
            if family not in REGISTRY:
                raise KeyError(f"strategies.yaml: unknown family {family!r}")
            reg.variants[f"{family}.base"] = Variant(
                f"{family}.base", family, dict(spec.get("base", {})), "baseline", created=today,
                note=spec.get("base_note", "Rules as commonly taught"),
            )
            if "enhanced" not in spec:
                continue
            reg.variants[f"{family}.v1"] = Variant(
                f"{family}.v1", family, dict(spec.get("enhanced", {})), "champion", created=today,
                note=spec.get("enhanced_note", "Enhanced version"),
            )

    def save_registry(self, reg: Registry) -> None:
        data = {"updated": datetime.now().isoformat(timespec="seconds"),
                "variants": {k: asdict(v) for k, v in reg.variants.items()}}
        self.registry_path.write_text(json.dumps(data, indent=2, sort_keys=True))

    # -------------------------------------------------------------- ledgers
    def ledger(self, variant_id: str) -> pd.DataFrame:
        path = self.root / "ledgers" / f"{variant_id}.csv"
        if not path.exists():
            return pd.DataFrame(columns=LEDGER_COLS)
        return pd.read_csv(path)

    def trades(self, variant_id: str) -> pd.DataFrame:
        path = self.root / "trades" / f"{variant_id}.csv"
        if not path.exists():
            return trades_frame([])
        return pd.read_csv(path)

    def equity(self, variant_id: str, capital: float) -> float:
        led = self.ledger(variant_id)
        return float(led["equity_end"].iloc[-1]) if len(led) else capital

    def has_day(self, variant_id: str, day: date) -> bool:
        led = self.ledger(variant_id)
        return bool(len(led)) and day.isoformat() in set(led["date"].astype(str))

    def record_day(self, variant_id: str, day: date, equity_start: float, trades: list[Trade]) -> None:
        pnl = round(sum(t.net for t in trades), 2)
        row = pd.DataFrame([[day.isoformat(), round(equity_start, 2), pnl, round(equity_start + pnl, 2), len(trades)]],
                           columns=LEDGER_COLS)
        led_path = self.root / "ledgers" / f"{variant_id}.csv"
        row.to_csv(led_path, mode="a", header=not led_path.exists(), index=False)
        if trades:
            tr_path = self.root / "trades" / f"{variant_id}.csv"
            trades_frame(trades).to_csv(tr_path, mode="a", header=not tr_path.exists(), index=False)

    def log(self, event: dict) -> None:
        event = {"ts": datetime.now().isoformat(timespec="seconds"), **event}
        with open(self.root / "improve_log.jsonl", "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
