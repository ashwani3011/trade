from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
DATA_FINAL = time(15, 35)   # a day's candles are treated as complete after this


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def is_final(day: date, now: datetime | None = None) -> bool:
    """True once a day's candles can no longer change."""
    now = now or now_ist()
    return day < now.date() or (day == now.date() and now.time() >= DATA_FINAL)


def market_phase(now: datetime | None = None) -> str:
    """'closed' (weekend), 'pre', 'open' or 'post'."""
    now = now or now_ist()
    if now.weekday() >= 5:
        return "closed"
    if now.time() < MARKET_OPEN:
        return "pre"
    if now.time() < DATA_FINAL:
        return "open"
    return "post"
