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

## 4. Recommendation
- Stop pursuing intraday stock strategies for real money. Keep them on paper only for
  research.
- Candidate for real money: momentum A as a monthly CNC (delivery) rotation. It needs about
  4–10 orders a month, has no leverage, and holds nothing intraday.
- Next step: paper-trade it in the live system on real daily data before any real money.
- Simple alternative: a Nifty200 Momentum 30 index fund or ETF, which follows the same idea
  diversified over 30 stocks. It has no market filter, so its drawdowns are deeper.
