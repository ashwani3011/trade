# Paper-trading desk: project context

Self-improving **paper** trading system for NSE intraday, using real market data from Dhan.
The owner is putting in about Rs 20,000 and wants fully automated, rule-based (no emotion) decisions.
**Paper trading only**: never add order-placement code or place real orders unless the owner asks
explicitly. A variant is flagged `ready_for_real_money` in the leaderboard only after 40+ paper days.

## Where things are
- `README.md`: how it works, commands, Dhan token setup. `docs/STRATEGIES.md`: strategy reviews.
- `algo/`: engine (`engine.py`, `options/engine.py`), strategies, Dhan client (`data/dhan.py`),
  token handling (`data/dhan_auth.py`), real-time loop (`live.py`), self-improvement (`improve.py`).
- `config/settings.yaml` (universe, risk, costs), `config/strategies.yaml` (base/enhanced params).
- Output: `state/` (registry, ledgers, trades, `live/<date>.jsonl` decision journal), `reports/`.

## Strategies
- Stock intraday families: `orb`, `pdb`, `vwap_pb`, `sr_reject`.
- `tg_opt`: the owner's YouTube strategy. At 9:25, take the top NIFTY 50 gainers with a strong green
  first 10-min candle, buy an OTM call, stop at the option's first-candle low, exit by 12:00, and buy
  a put on the top loser as a "hedge". `tg_opt.base` follows the video exactly; `tg_opt.v1` is the
  reviewed, enhanced version (see docs/STRATEGIES.md section 5).
- Each family runs as `.base` (fixed benchmark) plus a champion and up to 2 challengers. Every
  variant has its own Rs 20k paper account.

## Daily operation (this session is the desk)
Routines fire into the desk session: weekdays 09:05 IST (start the trader), hourly 10:05–15:05
(health check and update), 15:44 (day summary), and Saturday 10:22 (`improve`).
- `python -m algo.cli supervise` is idempotent. Before or during market hours it starts the
  real-time trader in the background if it isn't running. After the close it records any
  completed days.
- The trader wakes 20 s after each 5-min candle closes and decides on completed candles only. It
  journals ORDER/ENTRY/EXIT/NOTE decisions with timestamps and is restart-safe. It records the day
  at 15:36.
- After each run, commit `state/` and `reports/` and push (`git pull --rebase` first; retry with
  backoff, since GitHub's credential service sometimes returns 503).
- Updates to the owner should be short and factual: decisions with times, open positions,
  P&L per variant, and exactly what failed if anything did.

## Dhan facts (verified on the live API, Sep 2026)
- Credentials come from env vars `DHAN_CLIENT_ID`, `DHAN_PIN`, `DHAN_TOTP_SECRET`. A 24h token is
  generated automatically via TOTP and cached in `data_cache/` (git-ignored). Never print or
  commit secrets.
- The Data API subscription (Rs 499/month) is required for candles.
- 5-min equity candles run 09:15–15:10 (72 a day); 15:15–15:25 are never returned. Square-off is
  therefore at the 15:10 candle.
- A request with fromDate exactly 09:15:00 drops that day's 09:15 candle, so requests start at
  09:00.
- Timestamps are epoch seconds and map correctly to IST.
- Stock options expire on the last Tuesday of the month; holidays can shift it, e.g. 23 Nov 2026
  (a Monday).

## Development
- `pytest` (fully offline, synthetic data) and `pyflakes algo tests` must pass before pushing.
- Branch: `claude/adoring-planck-88ci7e` (the repo's default branch).
- GitHub Actions workflow is manual-only by the owner's choice. Scheduling stays in Claude.
