from .base import Signal, Strategy, SymbolDay, clamp_params, mutate
from .level_rejection import LevelRejection
from .orb import OpeningRangeBreakout
from .prev_day_breakout import PrevDayBreakout
from .vwap_pullback import VwapPullback

REGISTRY: dict[str, type[Strategy]] = {
    cls.family: cls for cls in (OpeningRangeBreakout, PrevDayBreakout, VwapPullback, LevelRejection)
}


def build(family: str, params: dict | None = None) -> Strategy:
    if family not in REGISTRY:
        raise KeyError(f"unknown strategy family {family!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[family](params)


__all__ = ["REGISTRY", "Signal", "Strategy", "SymbolDay", "build", "clamp_params", "mutate"]
