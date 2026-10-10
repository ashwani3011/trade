"""Fetch expired NIFTY weekly option candles (5-min) from Dhan for strikes ATM-6..ATM+6,
CE and PE, into data_cache/nifty_opt/<CE|PE>_<offset>.csv. Research only; run outside market hours."""
import sys, logging
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from algo.data.dhan import DhanDataSource

logging.basicConfig(level=logging.WARNING)
out = Path("data_cache/nifty_opt"); out.mkdir(parents=True, exist_ok=True)
src = DhanDataSource(cache_dir="data_cache")
start, end = date(2022, 1, 1), date(2026, 10, 9)
for opt in ("CALL", "PUT"):
    key = "ce" if opt == "CALL" else "pe"
    for off in range(-6, 7):
        p = out / f"{key.upper()}_{off:+d}.csv"
        if p.exists():
            continue
        frames, cur = [], start
        while cur <= end:
            ce = min(end, cur + timedelta(days=28))
            body = {"securityId": 13, "exchangeSegment": "NSE_FNO", "instrument": "OPTIDX", "expiryFlag": "WEEK",
                    "expiryCode": 1, "strike": "ATM" if off == 0 else f"ATM{off:+d}", "drvOptionType": opt,
                    "requiredData": ["open", "high", "low", "close", "volume", "oi", "strike", "spot"],
                    "fromDate": cur.isoformat(), "toDate": (ce + timedelta(days=1)).isoformat(), "interval": "5"}
            try:
                d = (src._post("/charts/rollingoption", body).get("data") or {}).get(key) or {}
                if isinstance(d, dict) and d.get("timestamp"):
                    frames.append(pd.DataFrame({k: d[k] for k in ("timestamp", "open", "high", "low", "close", "volume", "oi", "strike", "spot") if k in d}))
            except Exception as e:
                print("fail", opt, off, cur, str(e)[:100])
            cur = ce + timedelta(days=1)
        if frames:
            df = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp")
            df.to_csv(p, index=False)
            print(key, off, len(df), flush=True)
