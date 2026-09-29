# Dhan data check — 2026-09-29 (run at 20:13 IST, after market close)

## `python -m algo.cli check`

```
token OK, expires 2026-09-30 20:13 IST
RELIANCE: 431 bars, last 2026-09-29 15:10:00+05:30
  2026-09-22: 71 candles 09:20-15:10 CHECK: expected 75 candles 09:15-15:25
  2026-09-23: 72 candles 09:15-15:10 CHECK: expected 75 candles 09:15-15:25
  2026-09-24: 72 candles 09:15-15:10 CHECK: expected 75 candles 09:15-15:25
  2026-09-25: 72 candles 09:15-15:10 CHECK: expected 75 candles 09:15-15:25
  2026-09-28: 72 candles 09:15-15:10 CHECK: expected 75 candles 09:15-15:25
  2026-09-29: 72 candles 09:15-15:10 CHECK: expected 75 candles 09:15-15:25
NIFTY 50: 151 bars
RELIANCE listed options: 546 contracts, expiries [2026-09-29, 2026-10-27, 2026-11-23]
```

## Raw Dhan `/charts/intraday` timestamps (RELIANCE, 5-min, 2026-09-29)

```
72 [1790653500.0, 1790653800.0] [1790674200.0, 1790674500.0, 1790674800.0]
```

| epoch | IST |
|---|---|
| 1790653500 | 2026-09-29 09:15 |
| 1790653800 | 2026-09-29 09:20 |
| 1790674200 | 2026-09-29 15:00 |
| 1790674500 | 2026-09-29 15:05 |
| 1790674800 | 2026-09-29 15:10 |

## Finding

- Timestamp conversion is correct: the raw epochs map exactly to the IST times we print. There's no timezone shift.
- Dhan returns only 72 candles per day, ending at the 15:10 candle. The 15:15, 15:20 and 15:25 candles are missing from Dhan's own response, even ~5 hours after close.
- 2026-09-22 is also missing the 09:15 candle. That's probably the edge of the requested lookback window, not a data gap.
- Impact: the last ~15 minutes of each session are not visible to the paper trader from this endpoint.

## Earlier credential check (14:08 UTC)

### Environment variables

- DHAN_CLIENT_ID: set
- DHAN_PIN: set
- DHAN_TOTP_SECRET: set
- DHAN_ACCESS_TOKEN: missing
- ANTHROPIC_API_KEY: missing

