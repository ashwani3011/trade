"""End-to-end: daily paper trading + improvement loop on synthetic data."""
from datetime import date
from pathlib import Path

from algo.config import load
from algo.data.synthetic import SyntheticDataSource, trading_days
from algo.improve import run_improve
from algo.runner import run_day
from algo.state import Store

ROOT = Path(__file__).resolve().parents[1]


def settings(tmp_path):
    s = load(ROOT / "config/settings.yaml", ROOT / "config/strategies.yaml")
    s.data_source = "synthetic"
    s.universe = s.universe[:5]
    s.state_dir, s.cache_dir, s.reports_dir = tmp_path / "state", tmp_path / "cache", tmp_path / "reports"
    s.improve.llm_enabled = False
    s.improve.lookback_days = 20
    s.improve.random_candidates = 4
    s.improve.shortlist = 2
    s.improve.min_trades = 4
    s.improve.seed = 7
    return s


def test_daily_run_is_idempotent_and_reports(tmp_path):
    s = settings(tmp_path)
    src = SyntheticDataSource()
    day = date(2025, 3, 4)
    first = run_day(s, day, source=src)
    assert first and set(first) == {f"{f}.{v}" for f in s.strategies for v in ("base", "v1")}
    again = run_day(s, day, source=src)
    assert again == {}  # already recorded
    assert (s.reports_dir / "daily" / "2025-03-04.md").exists()
    assert (s.reports_dir / "LEADERBOARD.md").exists()
    store = Store(s.state_dir)
    assert len(store.ledger("orb.v1")) == 1


def test_weekend_is_skipped(tmp_path):
    s = settings(tmp_path)
    assert run_day(s, date(2025, 3, 8), source=SyntheticDataSource()) is None


def test_improve_loop_runs(tmp_path):
    s = settings(tmp_path)
    src = SyntheticDataSource()
    for d in trading_days(date(2025, 2, 3), date(2025, 3, 7)):
        run_day(s, d, source=src)
    summary = run_improve(s, today=date(2025, 3, 10), source=src)
    assert summary["days"] >= 10
    store = Store(s.state_dir)
    reg = store.load_registry(s.strategies)
    for fam in s.strategies:
        assert reg.champion(fam) is not None
        assert len(reg.challengers(fam)) <= s.improve.max_challengers
    assert (s.state_dir / "improve_log.jsonl").exists()


def test_new_family_is_added_to_existing_registry(tmp_path):
    s = settings(tmp_path)
    orb_only = {"orb": s.strategies["orb"]}
    store = Store(s.state_dir)
    assert {v.family for v in store.load_registry(orb_only).variants.values()} == {"orb"}
    reg = store.load_registry(s.strategies)
    assert {v.family for v in reg.variants.values()} == set(s.strategies)
