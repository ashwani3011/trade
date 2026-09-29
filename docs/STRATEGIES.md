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

## Adding a strategy from a video

1. Write the rules down exactly: entry trigger, stop, target, time window and filters. Anything vague ("strong move") becomes a parameter with a range.
2. Create `algo/strategies/<name>.py` with a `Strategy` subclass. Copy the pattern of an existing one: `defaults`, `space`, `on_bar`.
3. Register it in `algo/strategies/__init__.py` and add `base` and `enhanced` params to `config/strategies.yaml`.
4. Run `pytest`. The no-look-ahead test runs automatically on every registered strategy.
5. On the next run, the new family is added to the registry automatically and paper trading starts with `<name>.base` and `<name>.v1`.
