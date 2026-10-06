# Strategy review

Every strategy runs as two paper-trading variants:

- **`<family>.base`**: the rules as they're usually taught in YouTube videos. It's a fixed benchmark and is never tuned.
- **`<family>.v1`**: an enhanced version. It becomes the champion the improvement loop tries to beat.

If an enhancement doesn't beat the base version on live paper results, the enhancement isn't helping. The leaderboard shows this directly.

The four starting families are common price-action setups taught by Indian trading channels. They're a starting point. To add a specific strategy from a video, see "Adding a strategy from a video" below.

---

## Common problems with strategies taught in videos

| Problem | How this system handles it |
|---|---|
| Examples picked in hindsight ("look how well it worked here") | Every rule is replayed on every day and every stock in the universe, including the days it fails |
| Costs ignored. On Rs 20k capital, brokerage + STT + GST + slippage take a big share of each trade | Every trade pays modelled Dhan intraday charges plus 3 bps slippage per fill |
| Fills that can't happen in practice ("buy exactly at the breakout") | The engine fills at the *next* bar's open. A gap through the stop fills at the open, not the stop. If a bar hits both stop and target, the stop counts |
| No position sizing | 1% of equity at risk per trade, 5x MIS leverage cap, at most 3 positions |
| Old videos, different market | Rules are re-validated weekly on the latest 60 trading days |
| Vague rules ("strong candle", "good volume") | Each vague idea becomes a numeric parameter with a range, which the improvement loop tunes |

---

## 1. Opening Range Breakout (`orb`)

**As taught:** mark the high and low of the first 15 minutes. Buy a break above, short a break below. Stop at the other side of the range, target 2R.

**Weaknesses and hidden assumptions:**
- Many breakouts at 09:30–10:00 fail. Without a filter you take every fake-out.
- A very wide opening range puts the stop far away. Position size shrinks and 2R is rarely reached.
- Buying below VWAP, or shorting above it, trades against the day's average participant.

**Enhanced (`orb.v1`):**
- The breakout bar needs at least 1.5x normal bar volume (`vol_mult`).
- Longs only above VWAP, shorts only below (`vwap_filter`).
- The stop is based on ATR (0.25 x daily ATR) instead of the far side of a possibly huge range.
- Days where the opening range is wider than 0.6 x ATR are skipped (`max_range_atr`). These are often news or gap days.
- A 0.05% buffer is required beyond the range, and there are no entries after 11:30.

## 2. Previous Day High/Low Breakout (`pdb`)

**As taught:** buy when price closes above yesterday's high, short below yesterday's low.

**Weaknesses:**
- On gap days the level is already broken at the open. The "breakout" then often marks the top.
- It trades against the higher-timeframe trend as often as with it.

**Enhanced (`pdb.v1`):**
- Trend filter: longs only if yesterday's close is above the 20-day EMA, and shorts only if it's below.
- Days that opened beyond the level are skipped (`skip_gap`).
- The breakout bar needs a volume spike and a 0.05% buffer.

## 3. VWAP Pullback (`vwap_pb`)

**As taught:** in an uptrend (EMA 9 > EMA 21, price above VWAP), buy when price pulls back to VWAP. Mirror the rules for shorts.

**Weaknesses:**
- On range-bound days price crosses VWAP constantly and every touch triggers.
- Entering on the touch bar itself catches falling knives.

**Enhanced (`vwap_pb.v1`):**
- A minimum trend strength is required: price at least 0.4% away from the day's open in the trend's direction.
- A confirmation candle is required: the touch bar must close in the trend's direction.
- The volume filter is on and the target is raised to 2R.

## 4. Support/Resistance Rejection (`sr_reject`)

**As taught (price action):** a pin bar that pokes through a key level (yesterday's high or low) and closes back inside signals a failed breakout. Fade it.

**Weaknesses:**
- On strong trend days the level eventually breaks. Fading every poke fights the trend.
- "Long wick" is subjective.

**Enhanced (`sr_reject.v1`):**
- The wick must be at least 55% of the bar's range.
- The rejection bar needs a volume spike.
- The next bar must confirm by breaking the pin bar's body (`confirm_break`) before entry.

---

## 5. Top gainers @ 9:25 option buying (`tg_opt`), from the transcript you shared

**The video's rules, as implemented in `tg_opt.base`:**
1. At 9:25, take the **top 2 gainers** (NIFTY 50, % change vs yesterday's close). The system calculates this from candles instead of reading the NSE website.
2. Use a **10-minute** chart. The first candle (9:15–9:25) must be **green** and one of the "three bullish candles". This is modelled as a body of at least 50% of the candle's range. If the first candle is "too big", skip the stock; that's set at more than 2% of price.
3. Buy an **OTM call about 5% above spot** when the first candle closes, at the 9:25 open.
4. **Stop** at the low of the option's first 10-minute candle. If that candle is "very big" (range over 30% of its high), the stop goes at the 60% level instead.
5. Target of 3R (the video's examples claim about 1:3), with a **time exit at 12:00** ("finished by 11:30–12").
6. **Put leg ("hedge"):** if the call goes into loss, buy an OTM put on the **top loser**.
7. **One lot** per trade, as in the video.

### Review: what's missing, assumed or out of date

| # | Issue in the video | Why it matters | `tg_opt.v1` (enhanced) |
|---|---|---|---|
| 1 | Two hand-picked days (22–23 April) | Hindsight. Losing days aren't shown | Every day is paper traded, including days with no trade |
| 2 | Exits chosen after the fact ("exit around 7" when the high was 8; "0.80" when the high was 0.90) | The 1:3 results come from hindsight exits | Fixed target, a breakeven trail after +1R, and a time exit |
| 3 | "Enter at the high of the candle" and "enter at next candle's open" are both said | Contradictory rules | `entry_mode`: base = open, enhanced = breakout above the option's first-candle high |
| 4 | Stop "a little lower, comes with experience" | Discretionary | Formalised as a parameter the loop can tune |
| 5 | Far-OTM stock options (5–10% OTM) | Low delta, wide spreads, fast time decay. Cheap premiums make the Rs 40 round-trip brokerage a huge share of the risk (a synthetic test trade lost 14R on costs alone) | Near-ATM strike (1.5% OTM), 1% slippage modelled per fill |
| 6 | One lot regardless of risk. The WIPRO example risked Rs 3,800 | That's **19% of a Rs 20k account** on one trade | Risk-based sizing: skip any trade whose 1-lot risk exceeds 5% of equity. The report shows every skip |
| 7 | Lot sizes are out of date. SEBI raised the minimum F&O contract value in Nov 2024, so lots are now much bigger | Many stock options now cost more than Rs 20k per lot | Affordability checked per trade and reported |
| 8 | The "hedge" is a put on a *different* stock, bought after the call is already losing | That's not a hedge. It's a second directional bet made in reaction to a loss, and it doubles premium at risk | Puts on top losers become an **independent mirror setup** with the same filters, decided at 9:25 |
| 9 | No market context | Top gainers on a red index day often fade | NIFTY's first 10-min candle must agree |
| 10 | No volume or gap check | A gap-up with a weak first candle is often exhaustion | First-candle volume at least 1.5x normal, gap at most 3% |
| 11 | Expiry not considered | Near expiry, OTM premiums decay quickly | Roll to next month if expiry is under 5 days away (stock expiry is now the last Tuesday of the month) |

**Data used:** 5-minute option candles from Dhan. The exact listed contract is used when it still trades. For older days, the system uses Dhan's expired-options endpoint (strike relative to ATM) and follows one fixed strike through the day. Lot sizes for expired months use the current lot size, which is an approximation.

---

## 6. F&O top gainer call buying (`tg_fno`), the owner's spec of 6 Oct 2026

Tracked as one fixed variant, `tg_fno.base`, with its own Rs 20k account. It has no enhanced version and the weekly improvement loop does not tune it.

- **Universe and ranking:** every NSE stock with listed options (about 213, read from Dhan's instrument master). From 09:25, on every completed 5-min candle, stocks are ranked by % change vs previous close. The top 2 are evaluated in rank order, so the ranking is dynamic and not frozen at 09:25.
- **Opening candle:** 09:15-09:25, built from the 09:15 and 09:20 five-minute candles. This gives the same OHLC as ten one-minute candles.
- **Qualifies when:**
  - the opening candle is bullish;
  - its body is at least 60% of its range;
  - its range is at most 2.5 x the daily ATR(14);
  - the stock has traded at or above the opening-candle high since 09:25;
  - an OTM call is listed.
- **Option:** the call at 0-based index 2 among strikes above spot, i.e. the third OTM strike. This matches the source code the spec came from (`otmOffsetStrikes = 2`). Change `otm_index` to 1 in `config/strategies.yaml` for the literal "2 strikes OTM".
- **Stop:** on the underlying stock, not the premium. It is the opening-candle low, or opening high - 60% of the opening range when the range is more than 3% of the open.
- **Fills:** decisions are taken on completed candles and filled at the next candle's open, both entry and stop exit.
- **Limits:** one trade a day, one lot. A lot that costs more than the account is skipped with a note.
- **Close:** square-off at the close of the 15:10 candle, which is the 15:15 price. Dhan returns no later candles.
- **Assumptions to confirm:**
  - The ATR is daily ATR(14).
  - The live NSE gainers page is replaced by ranking the full F&O list from Dhan candles, because NSE blocks most cloud servers. It is also replayable and restart-safe.
- **`r_multiple`:** for this family it is net P&L divided by the premium paid.

## Adding a strategy from a video

1. Write the rules down exactly: entry trigger, stop, target, time window and filters. Anything vague ("strong move") becomes a parameter with a range.
2. Create `algo/strategies/<name>.py` with a `Strategy` subclass. Copy the pattern of an existing one: `defaults`, `space`, `on_bar`.
3. Register it in `algo/strategies/__init__.py` and add `base` and `enhanced` params to `config/strategies.yaml`.
4. Run `pytest`. The no-look-ahead test runs automatically on every registered strategy.
5. On the next run, the new family is added to the registry automatically and paper trading starts with `<name>.base` and `<name>.v1`.
