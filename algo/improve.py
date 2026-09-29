"""Weekly self-improvement loop (champion / challenger).

For each strategy family:
  1. Promotion review: a challenger that has out-performed the champion over
     the same live paper days (>= min_live_days) replaces it. Challengers that
     don't manage it within max_live_days are retired.
  2. Risk review: any variant whose paper drawdown exceeds retire_drawdown_pct
     is retired (baselines are kept as benchmarks unless they blow up too).
  3. Search: candidate parameter sets (random local mutations of the champion,
     plus Claude's proposals when an API key is available) are ranked on the
     older 'train' slice of recent history; the shortlist must then beat the
     champion on the untouched recent 'test' slice. Survivors become new
     challengers and start paper trading the next day.

Nothing is promoted on backtest results alone - live paper trading is the
final out-of-sample test.
"""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta

import pandas as pd

from . import llm
from .backtest import run as backtest
from .config import Settings, make_option_source, make_source
from .data.synthetic import trading_days
from .metrics import score, summarize
from .report import variant_stats
from .state import Registry, Store, Variant
from .families import FAMILIES as REGISTRY
from .families import DataHub, kind
from .strategies import clamp_params, mutate

log = logging.getLogger(__name__)


def _live_window_score(store: Store, a: str, b: str, min_trades: int) -> tuple[int, float, float]:
    """Compare two variants over the days both were paper traded."""
    la, lb = store.ledger(a), store.ledger(b)
    common = sorted(set(la["date"].astype(str)) & set(lb["date"].astype(str)))
    if not common:
        return 0, float("-inf"), float("-inf")

    def window(vid: str, led: pd.DataFrame) -> float:
        led = led[led["date"].astype(str).isin(common)].reset_index(drop=True)
        tr = store.trades(vid)
        tr = tr[tr["date"].astype(str).isin(common)] if len(tr) else tr
        return score(summarize(tr, led["pnl"].astype(float), float(led["equity_start"].iloc[0])), min_trades)

    return len(common), window(a, la), window(b, lb)


def review_live(store: Store, reg: Registry, s: Settings, today: date) -> None:
    cfg = s.improve
    for v in reg.active():
        m = variant_stats(store, v.id, s.capital)
        if m["max_dd_pct"] > cfg.retire_drawdown_pct:
            v.status, v.retired_reason = "retired", f"drawdown {m['max_dd_pct']}% > {cfg.retire_drawdown_pct}%"
            store.log({"event": "retire", "variant": v.id, "reason": v.retired_reason})
    for family in {v.family for v in reg.active()}:
        champ = reg.champion(family)
        for ch in reg.challengers(family):
            if champ is None:
                ch.role = "champion"
                champ = ch
                store.log({"event": "promote", "variant": ch.id, "reason": "no active champion"})
                continue
            days, ch_score, champ_score = _live_window_score(store, ch.id, champ.id, min_trades=max(3, cfg.min_trades // 3))
            if days >= cfg.min_live_days and ch_score > champ_score:
                champ.role, champ.status, champ.retired_reason = "champion", "retired", f"replaced by {ch.id}"
                ch.role = "champion"
                store.log({"event": "promote", "variant": ch.id, "replaced": champ.id, "live_days": days,
                           "challenger_score": ch_score, "champion_score": champ_score})
                champ = ch
            elif days >= cfg.max_live_days:
                ch.status, ch.retired_reason = "retired", f"no edge over {champ.id} after {days} live days"
                store.log({"event": "retire", "variant": ch.id, "reason": ch.retired_reason})


def _evidence(store: Store, reg: Registry, family: str, s: Settings, champ_train: dict, champ_test: dict) -> dict:
    ev = {"champion_backtest_train": champ_train, "champion_backtest_test": champ_test, "variants": {}}
    for v in reg.variants.values():
        if v.family != family:
            continue
        tr = store.trades(v.id)
        entry = {"role": v.role, "status": v.status, "params": v.params, "live_paper": variant_stats(store, v.id, s.capital)}
        if len(tr):
            entry["by_exit_reason"] = tr.groupby("exit_reason")["net"].agg(["count", "sum"]).round(2).to_dict()
            entry["by_side"] = tr.groupby("side")["net"].agg(["count", "sum"]).round(2).to_dict()
            entry["by_entry_hour"] = tr.groupby(tr["entry_time"].str[:2])["net"].agg(["count", "sum"]).round(2).to_dict()
        ev["variants"][v.id] = entry
    return ev


def search_family(
    family: str, reg: Registry, store: Store, s: Settings, train: list, test: list, rng: random.Random
) -> list[Variant]:
    cfg = s.improve
    champ = reg.champion(family)
    if champ is None or len(reg.challengers(family)) >= cfg.max_challengers:
        return []
    cls = REGISTRY[family]

    def evaluate(params: dict, days: list) -> dict:
        return backtest(family, params, days, s.capital, s)[2]

    train_min = max(3, int(cfg.min_trades * len(train) / max(1, len(train) + len(test))))
    test_min = max(2, cfg.min_trades - train_min)
    champ_train, champ_test = evaluate(champ.params, train), evaluate(champ.params, test)

    candidates: list[tuple[dict, str]] = []
    seen = {tuple(sorted(champ.params.items()))}
    for _ in range(cfg.random_candidates * 3):
        if len(candidates) >= cfg.random_candidates:
            break
        p = mutate(cls.space, champ.params, rng, n_changes=rng.choice([1, 2, 3]))
        key = tuple(sorted(p.items()))
        if key not in seen:
            seen.add(key)
            candidates.append((p, "random mutation"))

    if cfg.llm_enabled and llm.available():
        review, proposals = llm.propose(
            cfg.llm_model, family, cls.description, cls.space, champ.params,
            _evidence(store, reg, family, s, champ_train, champ_test), cfg.llm_proposals,
        )
        if review:
            store.log({"event": "llm_review", "family": family, "review": review})
        for prop in proposals:
            p = {**champ.params, **clamp_params(cls.space, prop.get("params", {}))}
            key = tuple(sorted(p.items()))
            if key not in seen:
                seen.add(key)
                candidates.append((p, "claude: " + str(prop.get("rationale", ""))[:300]))

    scored = []
    for p, origin in candidates:
        m = evaluate(p, train)
        scored.append((score(m, train_min), p, origin, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    champ_train_score, champ_test_score = score(champ_train, train_min), score(champ_test, test_min)

    new: list[Variant] = []
    for tr_score, p, origin, tr_m in scored[: cfg.shortlist]:
        if tr_score <= champ_train_score:
            continue
        te_m = evaluate(p, test)
        te_score = score(te_m, test_min)
        accepted = te_score > champ_test_score and (te_m["return_pct"] > 0 or not cfg.require_positive_test)
        store.log({"event": "candidate", "family": family, "origin": origin, "params": p, "train": tr_m,
                   "test": te_m, "champion_train": champ_train, "champion_test": champ_test, "accepted": accepted})
        if accepted and len(reg.challengers(family)) + len(new) < cfg.max_challengers:
            vid = reg.next_id(family)
            v = Variant(vid, family, p, "challenger", created=date.today().isoformat(), parent=champ.id,
                        note=f"{origin} | train {tr_m['return_pct']}% / test {te_m['return_pct']}%")
            reg.variants[vid] = v
            new.append(v)
    return new


def run_improve(s: Settings, today: date | None = None, source=None, option_source=None) -> dict:
    today = today or date.today()
    store = Store(s.state_dir)
    reg = store.load_registry(s.strategies)
    review_live(store, reg, s, today)

    cfg = s.improve
    families = sorted({v.family for v in reg.active()})
    cal = trading_days(today - timedelta(days=int(cfg.lookback_days * 1.6) + 5), today - timedelta(days=1))
    src = source or make_source(s)
    hub = DataHub(s, src, option_source or make_option_source(s, src))
    hub.load(cal, {kind(f) for f in families})
    summary: dict = {"days": {}, "new_challengers": []}
    rng = random.Random(cfg.seed if cfg.seed is not None else today.toordinal())
    for family in families:
        contexts = [c for c in (hub.context(family, d) for d in cal) if c is not None][-cfg.lookback_days:]
        summary["days"][family] = len(contexts)
        if len(contexts) < 10:
            log.warning("%s: only %d days of history - skipping parameter search", family, len(contexts))
            continue
        split = int(len(contexts) * (1 - cfg.test_fraction))
        for v in search_family(family, reg, store, s, contexts[:split], contexts[split:], rng):
            summary["new_challengers"].append(v.id)
            log.info("new challenger %s: %s", v.id, v.note)
    store.save_registry(reg)
    store.log({"event": "improve_run", **summary})
    return summary
