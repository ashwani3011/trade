# Paper-trading desk: project context

Self-improving **paper** trading system for NSE intraday, using real market data from Dhan.
The owner is putting in about Rs 20,000 and wants fully automated, rule-based (no emotion) decisions.
**Paper trading by default**: never add order-placement code or place real orders unless the owner asks
explicitly. A variant is flagged `ready_for_real_money` in the leaderboard only after 40+ paper days.
**One exception, asked for explicitly by the owner on 2026-10-08:** `pdb.base` also trades real money
on Dhan through `algo/real.py`, controlled by `config/real.yaml` (Rs 20k, Rs 200 risk/trade, max 5
entries a day, Rs 600 daily loss limit, stop-loss placed at Dhan on every entry). Shadow (nothing sent)
on 2026-10-09. Live was armed for 2026-10-12, then put back to shadow on 2026-10-09 after a 180-day
backtest showed pdb.base negative after costs; live again only on the owner's explicit word.
It runs only on the Oracle VM (`host: paper-trader`).
- `config/real.yaml` is re-read every candle; a committed change reaches the VM within 15 min (the
  push timer pulls). `mode: off|shadow|live`, `kill: true` exits everything. On the VM,
  `touch ~/trade/KILL` is the instant kill switch. Never widen the scope (other variants, more
  capital, higher risk) without the owner's explicit instruction.
- Output: `state/real/<date>.json|.jsonl` and `reports/real/<date>.md` (no secrets or balances).
  Real target exits are market orders at the first candle check after paper's target, so they
  usually fill a little worse than paper.

## Where things are
- `README.md`: how it works, commands, Dhan token setup. `docs/STRATEGIES.md`: strategy reviews.
- `algo/`: engine (`engine.py`, `options/engine.py`), strategies, Dhan client (`data/dhan.py`),
  token handling (`data/dhan_auth.py`), real-time loop (`live.py`), self-improvement (`improve.py`),
  real-money desk for pdb.base (`real.py`).
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
Since 2026-10-09 the trader runs on an **Oracle Cloud VM** (`deploy/oracle/`, Mumbai, static IP
80.225.250.206, user `ubuntu`, repo `~/trade`, secrets in `~/.paper-trade.env`), not in the Claude
container (a container is shut down when idle, which kills background processes).
- systemd: `paper-trade.timer` starts `paper-trade.service` Mon–Fri 08:45 IST (`git pull`, `check`,
  then one all-day `trade-live` that records the day at 15:36 and pushes on exit);
  `paper-push.timer` commits and pushes `state/`, `reports/`, `dashboard/data.json` every 15 min
  09:00–16:45. Commits are titled `paper-trade (oracle): ...`, pushed with a write deploy key.
- Claude cannot reach the VM; the owner runs any VM command. Claude checks it through the commits.
- GitHub Actions (`.github/workflows/paper-trade.yml`) is now only the manual fallback
  (`workflow_dispatch` morning/afternoon/close) plus the Saturday 10:22 `improve` cron. The weekday
  crons were removed (they ran hours late; the late close replayed old days).
- Claude routines fire into the desk session: weekdays 09:05 IST, hourly 10:05–15:05, 15:44, and
  Saturday 10:22. They report from the Oracle commits (`git log`), summarise
  `reports/live/<date>.md`, process dashboard actions and republish the dashboard.
- Fallback: if there is no `paper-trade (oracle)` commit for today by the 10:05 check (or none for
  45+ min during market hours), the VM trader is down. Only then dispatch the GitHub `morning`
  (before 12:30) or `afternoon` run, and tell the owner. **Never run two traders at once**, and never
  start a trader in the container.
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
