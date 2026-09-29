import base64
import json
import time

from algo.data import dhan_auth
from algo.data.dhan import _candles_to_frame, _rolling_to_frame


def test_totp_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert dhan_auth.totp(secret, at=59, digits=8) == "94287082"
    assert dhan_auth.totp(secret, at=1111111109, digits=8) == "07081804"


def _jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS512'})}.{enc({'exp': exp})}.sig"


def test_token_resolution(tmp_path, monkeypatch):
    for k in ("DHAN_ACCESS_TOKEN", "DHAN_PIN", "DHAN_TOTP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    fresh, stale = _jwt(time.time() + 20 * 3600), _jwt(time.time() - 60)
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", fresh)
    assert dhan_auth.get_access_token("1", tmp_path) == fresh

    monkeypatch.setenv("DHAN_ACCESS_TOKEN", stale)
    monkeypatch.setenv("DHAN_PIN", "1234")
    monkeypatch.setenv("DHAN_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    calls = []
    monkeypatch.setattr(dhan_auth, "generate_with_totp", lambda c, p, s: calls.append(c) or fresh)
    assert dhan_auth.get_access_token("1", tmp_path) == fresh
    assert dhan_auth.get_access_token("1", tmp_path) == fresh  # second call served from cache
    assert calls == ["1"]


def test_expired_env_token_without_totp_explains(tmp_path, monkeypatch):
    for k in ("DHAN_PIN", "DHAN_TOTP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", _jwt(time.time() - 60))
    try:
        dhan_auth.get_access_token("1", tmp_path)
    except dhan_auth.DhanAuthError as e:
        assert "expired" in str(e)
    else:
        raise AssertionError("expected DhanAuthError")


def test_parse_candles_and_rolling():
    payload = {"open": [1, 2], "high": [2, 3], "low": [0.5, 1.5], "close": [1.5, 2.5], "volume": [10, 20],
               "timestamp": [1776829500, 1776829800]}  # 2026-04-22 09:15 / 09:20 IST
    df = _candles_to_frame(payload, intraday=True)
    assert list(df["time"].dt.strftime("%H:%M")) == ["09:15", "09:20"]
    r = _rolling_to_frame({**payload, "strike": [500, 500]})
    assert list(r["strike"]) == [500, 500]
    recs = [dict(zip(payload, vals)) | {"strike": 510} for vals in zip(*payload.values())]
    assert list(_rolling_to_frame(recs)["strike"]) == [510, 510]
