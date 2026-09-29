# Self-improving paper-trading system (NSE intraday)

This project paper trades several intraday price-action strategies on real NSE data from the Dhan API. Every day it records each strategy's results in its own ledger. Every week it tries to improve the strategies, with safeguards against overfitting. Rules make every decision, so emotion never enters.

> **Read this first.** No software can promise profits. SEBI's own studies found that about 7 in 10 individual intraday equity traders lost money (FY2023), and over 90% of individual F&O traders lost money (FY2022–FY2024). This system's job is to tell you, with evidence and after costs, whether any strategy has a real edge *before* you risk your Rs 20,000. Most strategies will turn out not to, and learning that cheaply is the point.

## How it works

```
 09:05  routine starts the real-time trader (python -m algo.cli supervise)
 09:15  market opens; the trader wakes 20 s after every 5-minute candle closes
        (09:20:20, 09:25:20, ...), fetches only the candles that exist at that
        moment, and every variant decides:
          ORDER  decided on the candle close -> fills at the next candle's open
          ENTRY / EXIT (stop, target, time exit, square-off) as they happen
          NOTE   why a setup was skipped (e.g. "first candle too big")
        each decision is written with its wall-clock time to
          state/live/<date>.jsonl and reports/live/<date>.md
 hourly routine re-checks the trader (restarts it if needed; restart-safe) and posts an update
 15:36  final candle is complete -> the day is recorded in every variant's ledger
 15:44  routine posts the day's summary + leaderboard
 Sat    self-improvement: promote/retire, search new challengers (+ Claude proposals)
```

For `tg_opt` this means the 9:25 decision is made at **09:25:20** using only the 9:15 and 9:20 candles: rank the gainers, check the first 10-minute candle, pick the strike, and place the order. That's the same information you would have in real trading. The live decisions and the end-of-day record come from the same engine, and a test checks that they always agree.

- **Strategies.** Four stock-intraday price-action families, plus `tg_opt`, the top-gainers option-buying strategy from the video (see [docs/STRATEGIES.md](docs/STRATEGIES.md)).
- **Variants.** Each strategy family runs as `base` (as taught, never changed, a benchmark), a `champion`, and up to 2 `challengers`. Every variant has its own Rs 20,000 paper account.
- **Realistic fills.** Entries fill at the next bar's open plus slippage. A gap through the stop fills at the open. If a bar hits both stop and target, the stop counts. Everything is squared off at 15:15. Costs include brokerage, STT, exchange fees, SEBI fees, stamp duty and GST.
- **Risk rules.** 1% of equity at risk per trade, 5x leverage cap, at most 3 open positions and 6 trades a day, and a 3% daily loss limit.
- **Claude's role.** Claude is a research assistant, not a trader. Each week it reads the results and proposes parameter changes. Its ideas pass through the same validation gate and live paper probation as random candidates. It never places trades. That keeps decisions reproducible and cheap: one API call per strategy per week.
- **Real-money readiness.** The leaderboard flags a variant as `ready_for_real_money` only after it has 40+ paper days, 30+ trades, a profit factor of at least 1.3, a positive net return after costs, and a max drawdown of 10% or less.

The strategies and their enhancements are reviewed in [docs/STRATEGIES.md](docs/STRATEGIES.md).

## Setup

```bash
pip install -r requirements.txt
pytest                                   # fully offline

# try it with synthetic data (no credentials needed; results are meaningless)
python -m algo.cli --source synthetic catch-up --days 30
python -m algo.cli --source synthetic leaderboard
```

### Dhan credentials: the 24-hour token

Dhan access tokens expire 24 hours after they're generated. The system handles this in `algo/data/dhan_auth.py`:

| Option | Environment variables | Daily effort |
|---|---|---|
| **Recommended: auto-generate each morning** | `DHAN_CLIENT_ID`, `DHAN_PIN`, `DHAN_TOTP_SECRET` | none |
| Paste a token every day | `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | update the variable daily |

To set up TOTP, go to web.dhan.co → My Profile → DhanHQ Trading APIs → enable TOTP. Dhan shows a QR code and a text secret. Keep the text secret as `DHAN_TOTP_SECRET`, and scan the QR code with your authenticator app too. The system then generates the same 6-digit code itself and calls Dhan's `generateAccessToken` endpoint once a day. The token is cached in `data_cache/.dhan_token.json`, which is git-ignored and never committed.

> PIN + TOTP secret together give full login access to your Dhan account. Store them only as environment secrets (Claude environment settings or GitHub secrets), never in the repo or in chat.

Where to put them:
- **This Claude Code session:** cloud environment menu → Edit → environment variables. Add `api.dhan.co`, `auth.dhan.co` and `images.dhan.co` to the allowed network domains in the same place.
- **GitHub Actions (optional alternative):** repository Settings → Secrets → Actions.
- **Your own machine:** `export ...` in the shell.

```bash
python -m algo.cli check                                 # token + data access
python -m algo.cli backtest --family tg_opt --days 30    # first real-data look
python -m algo.cli auto                                  # what the scheduler runs
```

The Dhan client follows the official DhanHQ-py SDK's endpoints, but it hasn't been run against the live API from here. `check` is the first thing to run. Errors include Dhan's response text.

### Scheduling

**GitHub Actions is the scheduler** (`.github/workflows/paper-trade.yml`). It runs on GitHub's servers and doesn't depend on any Claude session staying up.

| IST (weekdays) | Run | What it does |
|---|---|---|
| 08:45 | morning | `check` (token, candle times) → `reports/dhan_check.md`, then live decisions 09:15–12:30 |
| 12:15 | afternoon | queued behind the morning run; live decisions 12:30–close, then records the day |
| 15:55 | close | safety net: records the day if the afternoon run was missed |
| Sat 10:22 | improve | weekly champion/challenger search |

Results are committed to the repo every 15 minutes while trading. GitHub caps a job at 6 hours, so the day is split into two runs that continue one decision journal. Both runs are restart-safe.

Setup: repository **Settings → Secrets and variables → Actions → New repository secret** for `DHAN_CLIENT_ID`, `DHAN_PIN` and `DHAN_TOTP_SECRET` (optional: `ANTHROPIC_API_KEY`). To test right away: **Actions → paper-trade → Run workflow → command `check`**.

GitHub's scheduled runs can start 5–30 minutes late at busy times. That's why the morning run starts at 08:45 and then waits for the open.

Alternatives: on an always-on machine, run `python -m algo.cli trade-live` from cron at 09:05 on weekdays. In a Claude Code session with the keys, `python -m algo.cli supervise` starts the trader in the background.

## Commands

| Command | What it does |
|---|---|
| `supervise` | Start the real-time trader in the background if it isn't running; after the close, record missed days |
| `trade-live` | Run the real-time trader in the foreground until the close |
| `stop-live` | Stop the background trader |
| `auto` | Live snapshot during market hours, final record after 15:35 IST |
| `live` | Replay today so far into `reports/live/<date>.md` |
| `check` | Verify Dhan token, equity/index/option data access |
| `run-day [--date D]` | Paper trade all active variants for one day (idempotent) |
| `catch-up --days N` | Run any missed days in the last N calendar days |
| `improve` | Promotion and retirement review, then the parameter search |
| `backtest --family F [--variant V] --days N` | One-off backtest of a variant |
| `leaderboard` | Print and write `reports/LEADERBOARD.md` |

## Layout

```
algo/
  data/          dhan.py (API client), synthetic.py (offline data), market.py (cache + day loader)
  data/dhan_auth.py  24h token: cached / env / auto-generated via PIN + TOTP
  strategies/    orb, prev_day_breakout, vwap_pullback, level_rejection (+ base contract)
  options/       tg_opt: strategy params, option data (listed + expired contracts), engine
  engine.py      bar-replay paper broker
  costs.py       Indian intraday cost model
  improve.py     champion/challenger loop
  llm.py         Claude proposals (structured JSON output)
config/          settings.yaml (universe, risk, costs, loop), strategies.yaml (base/enhanced params)
state/           registry.json, ledgers/, trades/, improve_log.jsonl   (written by the bot)
reports/         daily/*.md, live/*.md, LEADERBOARD.md                            (written by the bot)
```

## Before real money

- Paper trade for at least 2–3 months. The graduation bar is deliberately strict.
- Live trading through Dhan's order API isn't implemented yet, on purpose. Before adding it, check the current SEBI/exchange rules for retail API and algo trading. The 2025 retail algo framework covers static IPs, algo registration and order-rate limits, and Dhan publishes how it applies them.
- Start with 1 variant and a fraction of capital. Keep the kill switches: daily loss limit and drawdown retirement.
