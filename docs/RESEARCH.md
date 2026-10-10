# Strategy research, 10 Oct 2026

Question from the owner: which strategy can make money with about Rs 20,000? Everything below
was tested on real Dhan data, with full charges and a Rs 20k account, filling at the next bar.
Code: `research/` (`fetch_daily.py`, `daily_lab.py`, `grid.py`). No orders are placed.

## 1. What the industry evidence says
- SEBI: 71% of individual intraday traders in the cash segment lost money in FY23
  (Business Standard, 24 Jul 2024). 93% of individual F&O traders lost money over FY22–FY24,
  more than Rs 1.8 lakh crore in total (SEBI press release, Sep 2024). The profits went to prop
  desks and FPIs, mostly through algorithmic trading.
- The edge with the best evidence that a small account can use is medium-term momentum and
  trend-following. NSE's Nifty200 Momentum 30 returned 19.2% a year against 15.1% for the
  Nifty 200 (Apr 2005–Feb 2026, NSE whitepaper), with deep drawdowns in some years
  (2008, 2016).
- Intraday momentum (the first half hour predicts the last; Gao et al., JFE 2018) is real in
  the US, but it is worth only a few basis points.

## 2. Intraday on our data (Jan–Oct 2026, 5-min bars, 15 large caps)
- All four intraday families lose money after costs over 180 days. pdb.base: +Rs 5,786 gross,
  Rs 14,174 costs, −Rs 8,388 net. orb.base: −7,414. vwap_pb.base: −14,967.
  sr_reject.base: −17,797.
- Intraday momentum on these stocks: midday → last half hour correlation 0.23, but only about
  3 bps of edge against roughly 12 bps of round-trip costs. First half hour → last: about 0.
- Conclusion: the intraday edges reachable here are smaller than the costs. That is a structural
  problem, not a parameter problem.

## 3. Swing / positional (daily bars, 2016-01 to 2026-10, F&O universe of 213 stocks)
Delivery (CNC) costs: brokerage 0, STT 0.1% on each side, stamp 0.015%, DP Rs 15.93 per sell,
slippage 0.05% per side. Whole shares only.

| strategy | CAGR | max DD | trades | note |
|---|---|---|---|---|
| NIFTY 50 buy & hold | 10.1% | −38% | – | |
| universe equal-weight hold | 19.5% | −38% | – | inflated by survivorship (today's F&O list) |
| **momentum A: top 10 by 12-month return, monthly, cash if NIFTY < 200DMA** | **29.0%** | **−22%** | 288 | |
| momentum, top 5, 6m vol-adjusted | 23.4% | −34% | 252 | |
| Donchian 55/20 breakout | 22.1% | −43% | 272 | |
| RSI(2) pullback | −25.6% | −96% | 1,542 | +0.54%/trade gross, but about 1.1%/trade in costs on Rs 2k positions |

Robustness of momentum A:
- **Walk-forward:** 2016–21 gave 29.4% / −18%. Untouched 2022–26 gave 28.7% / −16%, against
  an equal-weight hold of 23.4% / −21% and NIFTY at 5.3%.
- All 96 grid settings were positive in 2022–26 (median 24%).
- The NIFTY filter at 100/150/200/250 DMA gives 28–30%. Lookbacks of 189/252/315 days give
  29–31%. A hold buffer of 0/2/5 gives 27–30%. Slippage of 0.2% instead of 0.05% costs about
  0.6%/yr.
- Monthly rebalancing is needed: quarterly gives 18.8% / −43%.
- Yearly: 2016 +10.5, 2017 +28, 2018 −2.6, 2019 +8.9, 2020 +46, 2021 +113, 2022 +1.7,
  2023 +74, 2024 +81, 2025 +13, 2026 YTD −8.6.
- Trades are held for a median of 62 days. 58% are winners, averaging +36%; losers average −10%.
  The strategy is in the market about 75% of days.

**Honest caveats**
- **Survivorship bias.** The universe is today's F&O list, which is why simply holding it shows
  19.5% against NIFTY's 10.1%. The edge to trust is relative: about +5–6%/yr over holding the
  same stocks, with roughly half the drawdown.
- On NIFTY-50 members only, momentum A gives 18.8% / −17% against an equal-weight hold of
  18.1% / −40%. There, the main benefit is the drawdown cut from the filter, not extra return.
- A realistic forward expectation is NIFTY plus a few % a year with smaller drawdowns, and some
  flat or negative years. It will not repeat 29%/yr.

## 4. NIFTY weekly options, intraday (Jan 2022 – Oct 2026, 1,179 days)
Data: Dhan expired-option 5-min candles, strikes ATM−10..ATM+10, CE and PE
(`research/fetch_nifty_options.py`, `research/options_lab.py`, `research/run_options.py`).
Results are per lot at today's lot size of 65, after costs (Rs 20/order, STT, exchange, GST,
0.5% slippage).

Data caveats:
- Dhan's rolling candles mix two strikes in open/high/low at ATM switches, so all fills and stops
  use 5-min closes. That means candle-close stops, not tick stops.
- Contracts that move outside ATM±10 are priced at intrinsic value plus nearby time value.
- An earlier run that dropped such days overstated option selling a lot: the straddle showed
  +8,197/month, against −1,964 with every day included.

| strategy (per lot) | avg Rs/month | months + | worst day | max DD | capital needed |
|---|---|---|---|---|---|
| 9:20 short straddle, no stop | −1,964 | 48% | −32,086 | −1.98 L | ~1.1–2.5 L margin |
| 9:20 straddle, 30% leg stop | −968 | 50% | −11,905 | −1.23 L | same |
| 9:20 straddle, 50% leg stop | +758 | 55% | −15,984 | −0.85 L | same |
| strangle ±2 strikes, 50% stop | +605 | 55% | −12,756 | −0.79 L | same |
| iron fly, wings ±6, no stop | −7,033 | 19% | −14,169 | −4.1 L | ~0.4–1 L |
| expiry days only, straddle 50% stop | +1,900 | – | −10,131 | – | ~1.5–2.5 L |
| ORB option BUYING (ATM, 30% stop / 60% target) | −1,926 | 47% | −7,522 | −1.52 L | ~Rs 10k premium |

Takeaways:
- Option buying, the only F&O trade a Rs 20k account can place, loses money.
- Option selling needs 1–2.5 lakh of margin. Its best variants earned about 0.3–1%/month on that
  margin, with single days of −10k to −32k per lot.
- Intraday hedging (iron fly) costs more in wing decay and fees than it saves.

## 5. Recommendation
- Stop pursuing intraday stock strategies for real money. Keep them on paper only for
  research.
- Candidate for real money: momentum A as a monthly CNC (delivery) rotation. It needs about
  4–10 orders a month, has no leverage, and holds nothing intraday.
- Next step: paper-trade it in the live system on real daily data before any real money.
- Simple alternative: a Nifty200 Momentum 30 index fund or ETF, which follows the same idea
  diversified over 30 stocks. It has no market filter, so its drawdowns are deeper.
