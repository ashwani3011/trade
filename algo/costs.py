"""Indian equity intraday (MIS) transaction costs.

Defaults follow Dhan's published intraday equity charges. Verify against
https://dhan.co/pricing/ periodically and override in config/settings.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    brokerage_pct: float = 0.0003      # 0.03% per executed order ...
    brokerage_cap: float = 20.0        # ... or Rs 20, whichever is lower
    stt_sell_pct: float = 0.00025      # STT 0.025% on sell side (intraday)
    exchange_pct: float = 0.0000297    # NSE transaction charge
    sebi_pct: float = 0.000001         # Rs 10 per crore
    stamp_buy_pct: float = 0.00003     # stamp duty 0.003% on buy side
    gst_pct: float = 0.18              # GST on brokerage + exchange + SEBI fees
    slippage_bps: float = 3.0          # adverse slippage per fill, basis points

    def order_brokerage(self, value: float) -> float:
        return min(self.brokerage_cap, value * self.brokerage_pct)

    def round_trip(self, buy_value: float, sell_value: float) -> float:
        brokerage = self.order_brokerage(buy_value) + self.order_brokerage(sell_value)
        turnover = buy_value + sell_value
        exchange = turnover * self.exchange_pct
        sebi = turnover * self.sebi_pct
        stt = sell_value * self.stt_sell_pct
        stamp = buy_value * self.stamp_buy_pct
        gst = (brokerage + exchange + sebi) * self.gst_pct
        return brokerage + exchange + sebi + stt + stamp + gst

    def slip(self, price: float, side: int, entering: bool) -> float:
        """Apply adverse slippage. side=+1 long, -1 short."""
        direction = side if entering else -side
        return price * (1 + direction * self.slippage_bps / 10_000)
