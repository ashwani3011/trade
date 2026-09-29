"""Dhan v2 market-data client (historical + intraday candles).

Docs: https://dhanhq.co/docs/v2/historical-data/
Credentials come from env vars DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN.
Only data endpoints are used - this module never places orders.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .base import DAILY_COLS, INTRADAY_COLS, IST, empty_daily, empty_intraday

BASE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
MAX_INTRADAY_SPAN_DAYS = 85  # Dhan allows ~90 days per intraday request


class DhanError(RuntimeError):
    pass


class DhanDataSource:
    def __init__(
        self,
        client_id: str | None = None,
        access_token: str | None = None,
        security_ids: dict[str, str] | None = None,
        cache_dir: str | Path = "data_cache",
        min_interval_s: float = 0.25,
    ):
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID", "")
        self.access_token = access_token or os.environ.get("DHAN_ACCESS_TOKEN", "")
        if not self.access_token:
            raise DhanError("DHAN_ACCESS_TOKEN is not set")
        self.cache_dir = Path(cache_dir)
        self._ids = dict(security_ids or {})
        self._min_interval = min_interval_s
        self._last_call = 0.0
        self._session = requests.Session()

    # ------------------------------------------------------------------ ids
    def security_id(self, symbol: str) -> str:
        if symbol not in self._ids:
            self._ids.update(self._load_scrip_master())
        if symbol not in self._ids:
            raise DhanError(f"No NSE equity security id for {symbol}; add it to settings.yaml")
        return self._ids[symbol]

    def _load_scrip_master(self) -> dict[str, str]:
        path = self.cache_dir / "scrip_master.csv"
        if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
            path.parent.mkdir(parents=True, exist_ok=True)
            resp = self._session.get(SCRIP_MASTER_URL, timeout=60)
            resp.raise_for_status()
            path.write_bytes(resp.content)
        df = pd.read_csv(path, low_memory=False)
        mask = (
            (df["SEM_EXM_EXCH_ID"] == "NSE")
            & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")
            & (df["SEM_SERIES"] == "EQ")
        )
        sub = df.loc[mask, ["SEM_TRADING_SYMBOL", "SEM_SMST_SECURITY_ID"]]
        return {str(s): str(i) for s, i in zip(sub["SEM_TRADING_SYMBOL"], sub["SEM_SMST_SECURITY_ID"])}

    # ----------------------------------------------------------------- http
    def _post(self, path: str, body: dict) -> dict:
        wait = self._min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        headers = {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        for attempt in range(4):
            self._last_call = time.time()
            resp = self._session.post(BASE_URL + path, json=body, headers=headers, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise DhanError(f"{path} {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise DhanError(f"{path} failed after retries")

    # ----------------------------------------------------------------- data
    def intraday(self, symbol: str, start: date, end: date, interval: int = 5) -> pd.DataFrame:
        frames = []
        cur = start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=MAX_INTRADAY_SPAN_DAYS))
            body = {
                "securityId": self.security_id(symbol),
                "exchangeSegment": "NSE_EQ",
                "instrument": "EQUITY",
                "interval": str(interval),
                "oi": False,
                "fromDate": f"{cur.isoformat()} 09:15:00",
                "toDate": f"{chunk_end.isoformat()} 15:30:00",
            }
            frames.append(_candles_to_frame(self._post("/charts/intraday", body), intraday=True))
            cur = chunk_end + timedelta(days=1)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_intraday()
        return pd.concat(frames).drop_duplicates("time").sort_values("time").reset_index(drop=True)

    def daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        body = {
            "securityId": self.security_id(symbol),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "oi": False,
            "fromDate": start.isoformat(),
            "toDate": (end + timedelta(days=1)).isoformat(),  # toDate is exclusive
        }
        return _candles_to_frame(self._post("/charts/historical", body), intraday=False)


def _candles_to_frame(payload: dict, intraday: bool) -> pd.DataFrame:
    if not payload or not payload.get("timestamp"):
        return empty_intraday() if intraday else empty_daily()
    ts = pd.to_datetime(pd.Series(payload["timestamp"], dtype="int64"), unit="s", utc=True).dt.tz_convert(IST)
    df = pd.DataFrame(
        {
            "open": payload["open"],
            "high": payload["high"],
            "low": payload["low"],
            "close": payload["close"],
            "volume": payload["volume"],
        }
    ).astype(float)
    if intraday:
        df.insert(0, "time", ts)
        return df[INTRADAY_COLS]
    df.insert(0, "date", ts.dt.date)
    return df[DAILY_COLS]
