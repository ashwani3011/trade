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
- `tg_fno`: the owner's F&O top-gainer spec (6 Oct 2026). From 9:25 it takes the top 2 of all F&O stocks,
  re-ranked every candle. Entry needs a strong bullish opening candle and a touch of the opening high; it
  buys the 3rd OTM call with the stop on the stock. One trade a day. It runs as `tg_fno.base` only and is
  not tuned (docs/STRATEGIES.md section 6).
- Each family runs as `.base` (fixed benchmark) plus a champion and up to 2 challengers. Every
  variant has its own Rs 20k paper account.

## Daily operation
The trader runs on **GitHub Actions** (`.github/workflows/paper-trade.yml`), not in the Claude
container: a Claude cloud container is shut down when idle, which kills any background process.
- Claude routines start the trading runs via `workflow_dispatch` (GitHub's own cron ran hours late
  on 2026-09-30): 08:40 IST morning run (Dhan check, trades 09:15–12:30) and 12:10 afternoon run
  (queued behind it; 12:30 to close, records the day). GitHub cron keeps a 12:15 afternoon backup,
  the 15:55 close safety net and Saturday 10:22 `improve`; there is deliberately no morning cron.
  Runs share one restart-safe journal and commit `state/`, `reports/` and `dashboard/data.json`
  every 15 min.
- Claude routines fire into the desk session: weekdays 09:05 IST, hourly 10:05–15:05, 15:44, and
  Saturday 10:22. They only report: `git pull`, check the latest paper-trade run (GitHub MCP
  `actions_list`), summarise `reports/live/<date>.md`, process dashboard actions, republish the
  dashboard. **Never start a trader in the container** (no `supervise`/`trade-live` there) while
  GitHub runs it; two traders would write the same journal. If a GitHub run failed, report exactly
  what failed.
- The trader wakes 20 s after each 5-min candle closes and decides on completed candles only. It
  journals ORDER/ENTRY/EXIT/NOTE decisions with timestamps and is restart-safe. It records the day
  at 15:36.
- When committing from the container, `git pull --rebase` first; retry with backoff, since
  GitHub's credential service sometimes returns 503.
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
- The GitHub Actions workflow runs the trader on a schedule (enabled by the owner on 2026-09-30);
  Claude routines only report.
