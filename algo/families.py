"""All strategy families, their data needs, and one `simulate` entry point."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .data.market import DayData, MarketData
from .engine import Trade, simulate_day
from .options.engine import DayResult, OptionsDay, simulate_options_day
from .options.strategy import TopGainerOptions
from .strategies import REGISTRY as EQUITY_FAMILIES
from .strategies.base import Strategy

FAMILIES: dict[str, type[Strategy]] = {**EQUITY_FAMILIES, TopGainerOptions.family: TopGainerOptions}


def kind(family: str) -> str:
    return getattr(FAMILIES[family], "kind", "equity")


def build(family: str, params: dict | None = None) -> Strategy:
    if family not in FAMILIES:
        raise KeyError(f"unknown strategy family {family!r}; known: {sorted(FAMILIES)}")
    return FAMILIES[family](params)


@dataclass
class DataHub:
    """Loads (and caches in memory) everything the variants need for a set of days."""
    settings: object
    source: object
    option_source: object = None
    _equity: dict[date, DayData] = field(default_factory=dict)
    _options: dict[date, OptionsDay] = field(default_factory=dict)
    _md: dict[str, MarketData] = field(default_factory=dict)

    def _market(self, name: str, symbols: list[str]) -> MarketData:
        # kept across calls so the live loop doesn't refetch daily history every bar
        if name not in self._md:
            self._md[name] = MarketData(self.source, symbols, self.settings.cache_dir, self.settings.interval)
        return self._md[name]

    def load(self, days: list[date], kinds: set[str], min_bars: int = 10) -> list[date]:
        s = self.settings
        loaded: set[date] = set()
        if "equity" in kinds:
            md = self._market("equity", s.universe)
            for d in md.days(days, min_bars=min_bars):
                self._equity[d.day] = d
                loaded.add(d.day)
        if "options" in kinds:
            o = s.options
            md = self._market("options", o.universe + [o.index_symbol])
            for d in md.days(days, min_bars=min(min_bars, 2)):
                self._options[d.day] = OptionsDay(d.day, d, o.index_symbol, self.option_source)
                loaded.add(d.day)
        return sorted(loaded)

    def context(self, family: str, day: date):
        return (self._options if kind(family) == "options" else self._equity).get(day)


def simulate(family: str, params: dict, ctx, equity: float, settings, variant: str = "", final: bool = True) -> DayResult:
    strat = build(family, params)
    if kind(family) == "options":
        return simulate_options_day(strat, ctx, equity, settings.options.costs, variant=variant, final=final)
    res = DayResult()
    res.trades = simulate_day(strat, ctx, equity, settings.risk, settings.costs, variant=variant,
                              interval=settings.interval, final=final, open_out=res.open_positions,
                              pending_out=res.pending_orders)
    return res


__all__ = ["FAMILIES", "DataHub", "DayResult", "Trade", "build", "kind", "simulate"]
