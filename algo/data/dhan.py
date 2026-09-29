"""Dhan v2 market-data client: equity, index and option candles.

Endpoints follow the official DhanHQ-py SDK (dhan-oss/DhanHQ-py):
  /v2/charts/intraday       intraday candles (interval 1, 5, 15, 25, 60)
  /v2/charts/historical     daily candles
  /v2/charts/rollingoption  expired-options candles by strike relative to ATM
Only data endpoints are used - this module never places orders.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from .base import DAILY_COLS, INTRADAY_COLS, IST, empty_daily, empty_intraday
from .dhan_auth import get_access_token

log = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
MAX_INTRADAY_SPAN_DAYS = 85   # Dhan allows ~90 days per intraday request
MAX_ROLLING_SPAN_DAYS = 28    # and ~30 days per expired-options request
# Observed on the live API (Sep 2026): 5-min equity candles run 09:15..15:10 (72 a day) -
# the 15:15-15:25 candles are never returned - and a fromDate of exactly 09:15:00 drops
# that day's 09:15 candle, so requests start at 09:00.
INDEX_IDS = {"NIFTY 50": "13", "NIFTY BANK": "25"}


class DhanError(RuntimeError):
    pass


class DhanDataSource:
    def __init__(
        self,
        client_id: str | None = None,
        security_ids: dict[str, str] | None = None,
        cache_dir: str | Path = "data_cache",
        min_interval_s: float = 0.25,
    ):
        self.client_id = client_id or os.environ.get("DHAN_CLIENT_ID", "")
        self.cache_dir = Path(cache_dir)
        self._token: str | None = None
        self._ids = dict(security_ids or {})
        self._master: pd.DataFrame | None = None
        self._min_interval = min_interval_s
        self._last_call = 0.0
        self._session = requests.Session()

    @property
    def access_token(self) -> str:
        if self._token is None:
            self._token = get_access_token(self.client_id, self.cache_dir)
        return self._token

    # ----------------------------------------------------------- scrip master
    def master(self) -> pd.DataFrame:
        if self._master is None:
            path = self.cache_dir / "scrip_master.csv"
            if not path.exists() or time.time() - path.stat().st_mtime > 20 * 3600:
                path.parent.mkdir(parents=True, exist_ok=True)
                resp = self._session.get(SCRIP_MASTER_URL, timeout=120)
                resp.raise_for_status()
                path.write_bytes(resp.content)
            self._master = pd.read_csv(path, low_memory=False)
        return self._master

    def security_id(self, symbol: str) -> str:
        if symbol not in self._ids:
            df = self.master()
            mask = (df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY") & (df["SEM_SERIES"] == "EQ")
            sub = df.loc[mask]
            self._ids.update({str(s): str(i) for s, i in zip(sub["SEM_TRADING_SYMBOL"], sub["SEM_SMST_SECURITY_ID"])})
        if symbol not in self._ids:
            raise DhanError(f"No NSE equity security id for {symbol}; add it under security_ids in settings.yaml")
        return self._ids[symbol]

    def option_contracts(self, underlying: str) -> pd.DataFrame:
        """Currently listed stock options: security_id, expiry, strike, option_type, lot_size."""
        df = self.master()
        opt = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "OPTSTK")]
        und = opt["SEM_TRADING_SYMBOL"].astype(str).str.rsplit("-", n=3).str[0]
        opt = opt[und == underlying]
        return pd.DataFrame({
            "security_id": opt["SEM_SMST_SECURITY_ID"].astype(str).to_numpy(),
            "expiry": pd.to_datetime(opt["SEM_EXPIRY_DATE"]).dt.date.to_numpy(),
            "strike": opt["SEM_STRIKE_PRICE"].astype(float).to_numpy(),
            "option_type": opt["SEM_OPTION_TYPE"].astype(str).to_numpy(),
            "lot_size": opt["SEM_LOT_UNITS"].astype(float).astype(int).to_numpy(),
        })

    # ------------------------------------------------------------------ http
    def _post(self, path: str, body: dict) -> dict:
        headers = {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        for attempt in range(4):
            wait = self._min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            resp = self._session.post(BASE_URL + path, json=body, headers=headers, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise DhanError(f"{path} {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise DhanError(f"{path} failed after retries")

    def candles(self, security_id: str, segment: str, instrument: str, start: date, end: date, interval: int = 5) -> pd.DataFrame:
        frames, cur = [], start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=MAX_INTRADAY_SPAN_DAYS))
            body = {
                "securityId": str(security_id), "exchangeSegment": segment, "instrument": instrument,
                "interval": str(interval), "oi": False,
                "fromDate": f"{cur.isoformat()} 09:00:00", "toDate": f"{chunk_end.isoformat()} 15:30:00",
            }
            frames.append(_candles_to_frame(self._post("/charts/intraday", body), intraday=True))
            cur = chunk_end + timedelta(days=1)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_intraday()
        return pd.concat(frames).drop_duplicates("time").sort_values("time").reset_index(drop=True)

    # ------------------------------------------------------------ public api
    def intraday(self, symbol: str, start: date, end: date, interval: int = 5) -> pd.DataFrame:
        if symbol in INDEX_IDS:
            return self.candles(INDEX_IDS[symbol], "IDX_I", "INDEX", start, end, interval)
        return self.candles(self.security_id(symbol), "NSE_EQ", "EQUITY", start, end, interval)

    def daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        seg, inst, sid = ("IDX_I", "INDEX", INDEX_IDS[symbol]) if symbol in INDEX_IDS else ("NSE_EQ", "EQUITY", self.security_id(symbol))
        body = {
            "securityId": sid, "exchangeSegment": seg, "instrument": inst, "expiryCode": 0, "oi": False,
            "fromDate": start.isoformat(), "toDate": (end + timedelta(days=1)).isoformat(),  # toDate is exclusive
        }
        return _candles_to_frame(self._post("/charts/historical", body), intraday=False)

    def option_intraday(self, security_id: str, start: date, end: date, interval: int = 5) -> pd.DataFrame:
        return self.candles(security_id, "NSE_FNO", "OPTSTK", start, end, interval)

    def rolling_option(self, underlying: str, start: date, end: date, offset: int, option_type: str,
                       expiry_code: int = 1, interval: int = 5) -> pd.DataFrame:
        """Expired stock-option candles at strike ATM+offset. Includes a per-bar `strike` column,
        because the ATM-relative strike re-maps as spot moves."""
        strike = "ATM" if offset == 0 else f"ATM{offset:+d}"
        key = "ce" if option_type == "CE" else "pe"
        frames, cur = [], start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=MAX_ROLLING_SPAN_DAYS))
            body = {
                "securityId": int(self.security_id(underlying)), "exchangeSegment": "NSE_FNO", "instrument": "OPTSTK",
                "expiryFlag": "MONTH", "expiryCode": expiry_code, "strike": strike,
                "drvOptionType": "CALL" if option_type == "CE" else "PUT",
                "requiredData": ["open", "high", "low", "close", "volume", "strike", "spot"],
                "fromDate": cur.isoformat(), "toDate": (chunk_end + timedelta(days=1)).isoformat(),
                "interval": str(interval),
            }
            payload = self._post("/charts/rollingoption", body)
            data = (payload.get("data") or {}).get(key) or {}
            frames.append(_rolling_to_frame(data))
            cur = chunk_end + timedelta(days=1)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=INTRADAY_COLS + ["strike"])
        return pd.concat(frames).drop_duplicates("time").sort_values("time").reset_index(drop=True)


def _rolling_to_frame(data) -> pd.DataFrame:
    if isinstance(data, list):  # list-of-records shape
        data = pd.DataFrame(data).to_dict(orient="list") if data else {}
    if not data or not data.get("timestamp"):
        return pd.DataFrame(columns=INTRADAY_COLS + ["strike"])
    df = _candles_to_frame(data, intraday=True)
    df["strike"] = pd.Series(data.get("strike", [float("nan")] * len(df)), dtype=float).to_numpy()
    return df


def _candles_to_frame(payload: dict, intraday: bool) -> pd.DataFrame:
    if not payload or not payload.get("timestamp"):
        return empty_intraday() if intraday else empty_daily()
    ts = pd.to_datetime(pd.Series(payload["timestamp"], dtype="int64"), unit="s", utc=True).dt.tz_convert(IST)
    df = pd.DataFrame({k: payload[k] for k in ("open", "high", "low", "close", "volume")}).astype(float)
    if intraday:
        df.insert(0, "time", ts)
        return df[INTRADAY_COLS]
    df.insert(0, "date", ts.dt.date)
    return df[DAILY_COLS]
