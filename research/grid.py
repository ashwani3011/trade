"""Robustness grid + walk-forward for the daily strategies (research only)."""
import itertools, sys, json
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import daily_lab as L

P = None
def init():
    global P
    P = L.load_panel()

def job(args):
    name, params, start, end, slip = args
    fn = L.STRATS[name]
    eq, b = fn(P, start, end, costs=L.Costs(slippage=slip), **params)
    s = L.stats(eq, b.trades, b.paid)
    return name, params, start, slip, {k: float(v) if hasattr(v, "item") else v for k, v in s.items()}

if __name__ == "__main__":
    which = sys.argv[1]
    grids = {
        "momentum": dict(top_n=[3, 5, 8, 10], lookback=[63, 126, 252], skip=[0, 21], vol_adj=[True, False], regime=[True, False]),
        "breakout": dict(slots=[3, 5, 8], entry_n=[20, 55, 100, 250], exit_n=[10, 20, 50], regime=[True, False]),
    }
    g = grids[which]
    combos = [dict(zip(g, v)) for v in itertools.product(*g.values())]
    periods = [("2016-01-01", "2021-12-31"), ("2022-01-01", "2026-10-09"), ("2016-01-01", "2026-10-09")]
    tasks = [(which, c, s, e, 0.0005) for c in combos for s, e in periods]
    with Pool(8, initializer=init) as pool:
        out = pool.map(job, tasks)
    Path(f"research/out_{which}.json").write_text(json.dumps(out, default=str))
    print("done", len(out))
