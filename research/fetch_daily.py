"""Fetch long daily history for the F&O stock universe + NIFTY 50 from Dhan into data_cache/daily/.
Research only (read-only data API). Run outside market hours."""
import sys, logging
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from algo.data.dhan import DhanDataSource

logging.basicConfig(level=logging.WARNING)
out = Path("data_cache/daily"); out.mkdir(parents=True, exist_ok=True)
src = DhanDataSource(cache_dir="data_cache")
syms = ["NIFTY 50"] + src.fno_underlyings()
start, end = date(2015, 1, 1), date(2026, 10, 9)
ok = fail = 0
for s in syms:
    p = out / f"{s.replace('/', '_')}.csv"
    if p.exists():
        ok += 1; continue
    try:
        df = src.daily(s, start, end)
        if len(df):
            df.to_csv(p, index=False); ok += 1
        else:
            fail += 1
    except Exception as e:
        fail += 1; print("fail", s, str(e)[:120])
print("ok", ok, "fail", fail, "of", len(syms))
