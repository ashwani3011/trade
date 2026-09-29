# Self-improving paper-trading system (NSE intraday)

This project paper trades several intraday price-action strategies on real NSE data from the Dhan API. Every day it records each strategy's results in its own ledger. Every week it tries to improve the strategies, with safeguards against overfitting. Rules make every decision, so emotion never enters.

> **Read this first.** No software can promise profits. SEBI's own studies found that about 7 in 10 individual intraday equity traders lost money (FY2023), and over 90% of individual F&O traders lost money (FY2022–FY2024). This system's job is to tell you, with evidence and after costs, whether any strategy has a real edge *before* you risk your Rs 20,000. Most strategies will turn out not to, and learning that cheaply is the point.

## How it works

```
               ┌────────────── every trading day, 16:05 IST ──────────────┐
 Dhan API ──►  │ 5-min bars ─► replay each bar (no look-ahead) ─► per-variant │ ─► reports/daily/*.md
 (read only)   │ for 15 NIFTY stocks    for every active variant      ledgers │    reports/LEADERBOARD.md
               └──────────────────────────────────────────────────────────┘
               ┌────────────── every Saturday ────────────────────────────┐
               │ 1. promote challengers that beat the champion live         │
               │ 2. retire variants with drawdown > 30%                     │
               │ 3. generate candidates: random tweaks + Claude proposals   │ ─► state/improve_log.jsonl
               │ 4. rank on older 70% of last 60 days, validate on newest   │
               │    30%, and only then paper trade as a challenger          │
               └──────────────────────────────────────────────────────────┘
```

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

### With real Dhan data

```bash
export DHAN_CLIENT_ID=...        # from web.dhan.co -> My Profile -> DhanHQ Trading APIs
export DHAN_ACCESS_TOKEN=...
export ANTHROPIC_API_KEY=...     # optional: enables Claude's weekly proposals

python -m algo.cli backtest --family orb --days 60     # check data access + a first look
python -m algo.cli catch-up --days 5                   # paper trade recent days
python -m algo.cli improve                             # run the weekly loop now
```

The Dhan client (`algo/data/dhan.py`) follows the v2 historical-data docs, but it hasn't been run against the live API yet. Run the `backtest` command above first. If it fails, the error message includes Dhan's response.

### Fully automated (GitHub Actions)

`.github/workflows/paper-trade.yml` runs `catch-up` at 16:05 IST on weekdays and `improve` on Saturdays. It commits `state/` and `reports/` back to the repo, so the history is versioned.

1. Add repository secrets: `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, and optionally `ANTHROPIC_API_KEY`.
2. Make sure the workflow is on the default branch. Scheduled workflows only run from the default branch.
3. Dhan access tokens expire, so renew the `DHAN_ACCESS_TOKEN` secret when it does. If a run fails with a 401, the token has expired.

## Commands

| Command | What it does |
|---|---|
| `run-day [--date D]` | Paper trade all active variants for one day (idempotent) |
| `catch-up --days N` | Run any missed days in the last N calendar days |
| `improve` | Promotion and retirement review, then the parameter search |
| `backtest --family F [--variant V] --days N` | One-off backtest of a variant |
| `leaderboard` | Print and write `reports/LEADERBOARD.md` |

## Layout

```
algo/
  data/          dhan.py (API client), synthetic.py (offline data), market.py (cache + day loader)
  strategies/    orb, prev_day_breakout, vwap_pullback, level_rejection (+ base contract)
  engine.py      bar-replay paper broker
  costs.py       Indian intraday cost model
  improve.py     champion/challenger loop
  llm.py         Claude proposals (structured JSON output)
config/          settings.yaml (universe, risk, costs, loop), strategies.yaml (base/enhanced params)
state/           registry.json, ledgers/, trades/, improve_log.jsonl   (written by the bot)
reports/         daily/*.md, LEADERBOARD.md                             (written by the bot)
```

## Before real money

- Paper trade for at least 2–3 months. The graduation bar is deliberately strict.
- Live trading through Dhan's order API isn't implemented yet, on purpose. Before adding it, check the current SEBI/exchange rules for retail API and algo trading. The 2025 retail algo framework covers static IPs, algo registration and order-rate limits, and Dhan publishes how it applies them.
- Start with 1 variant and a fraction of capital. Keep the kill switches: daily loss limit and drawdown retirement.
