"""Run the NIFTY options strategy set and print per-lot monthly results (research only)."""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import options_lab as O

def expiry_day(d):
    d = pd.Timestamp(d)
    return d.weekday() == (3 if d < pd.Timestamp("2025-09-01") else 1)   # Thu, then Tue from Sep 2025

def yr(s):
    s = s.copy(); s.index = pd.to_datetime(s.index)
    return (s.groupby(s.index.year).sum() / s.groupby(s.index.year).apply(lambda x: x.index.to_period("M").nunique())).round(0).to_dict()

if __name__ == "__main__":
    df = O.load()
    runs = {
        "straddle 9:20 no SL": (O.straddle, dict(sl=None)),
        "straddle 9:20 SL30": (O.straddle, dict(sl=0.3)),
        "straddle 9:20 SL50": (O.straddle, dict(sl=0.5)),
        "strangle +-2 no SL": (O.straddle, dict(sl=None, width=2)),
        "strangle +-2 SL50": (O.straddle, dict(sl=0.5, width=2)),
        "iron fly wings+-6 no SL": (O.straddle, dict(sl=None, wings=6)),
        "iron fly wings+-6 SL50": (O.straddle, dict(sl=0.5, wings=6)),
        "ORB option buy sl30 t60": (O.orb_buy, dict()),
    }
    for name, (fn, kw) in runs.items():
        s = O.run(df, fn, **kw)
        e = s[[expiry_day(d) for d in s.index]]
        print(f"{name:26s} {O.summary(s, 0)}")
        print(f"{'':26s} by year {yr(s)} | expiry days only: {O.summary(e, 0)['per_month_avg']}/month, worst day {round(e.min())}")
