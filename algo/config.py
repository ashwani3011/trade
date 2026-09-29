from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .costs import CostModel
from .engine import RiskConfig


def _pick(cls, raw: dict | None):
    raw = raw or {}
    names = {f.name for f in fields(cls)}
    unknown = set(raw) - names
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {sorted(unknown)}")
    return cls(**raw)


@dataclass
class ImproveConfig:
    lookback_days: int = 60          # trading days of history used to evaluate candidates
    test_fraction: float = 0.3       # most recent slice held out for validation
    random_candidates: int = 16
    shortlist: int = 4               # best-on-train candidates that go to validation
    min_trades: int = 15
    max_challengers: int = 2         # per family
    min_live_days: int = 10          # paper days before a challenger can be promoted
    max_live_days: int = 30          # challenger is retired if not promoted by then
    retire_drawdown_pct: float = 30.0
    require_positive_test: bool = True  # challenger must be profitable on held-out data
    llm_enabled: bool = True         # used only when ANTHROPIC_API_KEY is set
    llm_model: str = "claude-opus-5-5"
    llm_proposals: int = 3
    seed: int | None = None


@dataclass
class Settings:
    data_source: str = "synthetic"
    interval: int = 5
    capital: float = 20000.0
    universe: list[str] = field(default_factory=list)
    security_ids: dict[str, str] = field(default_factory=dict)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostModel = field(default_factory=CostModel)
    improve: ImproveConfig = field(default_factory=ImproveConfig)
    state_dir: Path = Path("state")
    cache_dir: Path = Path("data_cache")
    reports_dir: Path = Path("reports")
    strategies: dict[str, Any] = field(default_factory=dict)


def load(settings_path: str | Path = "config/settings.yaml", strategies_path: str | Path = "config/strategies.yaml") -> Settings:
    raw = yaml.safe_load(Path(settings_path).read_text()) or {}
    paths = raw.pop("paths", {}) or {}
    s = Settings(
        data_source=raw.pop("data_source", "synthetic"),
        interval=int(raw.pop("interval", 5)),
        capital=float(raw.pop("capital", 20000)),
        universe=list(raw.pop("universe", [])),
        security_ids={str(k): str(v) for k, v in (raw.pop("security_ids", {}) or {}).items()},
        risk=_pick(RiskConfig, raw.pop("risk", None)),
        costs=_pick(CostModel, raw.pop("costs", None)),
        improve=_pick(ImproveConfig, raw.pop("improve", None)),
        state_dir=Path(paths.get("state_dir", "state")),
        cache_dir=Path(paths.get("cache_dir", "data_cache")),
        reports_dir=Path(paths.get("reports_dir", "reports")),
    )
    if raw:
        raise ValueError(f"unknown settings keys: {sorted(raw)}")
    s.strategies = yaml.safe_load(Path(strategies_path).read_text()) or {}
    return s


def make_source(s: Settings):
    if s.data_source == "dhan":
        from .data.dhan import DhanDataSource

        return DhanDataSource(security_ids=s.security_ids, cache_dir=s.cache_dir)
    if s.data_source == "synthetic":
        from .data.synthetic import SyntheticDataSource

        return SyntheticDataSource()
    raise ValueError(f"unknown data_source {s.data_source!r}")
