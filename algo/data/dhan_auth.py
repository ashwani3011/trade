"""Dhan access-token management.

Dhan access tokens expire 24 hours after they are generated. Resolution order:

1. A token generated earlier today and cached in <cache_dir>/.dhan_token.json
   (git-ignored), if it is still valid for at least 30 minutes.
2. DHAN_ACCESS_TOKEN from the environment, if it is still valid.
3. A fresh token from Dhan's PIN + TOTP endpoint, when DHAN_PIN and
   DHAN_TOTP_SECRET are set. This is what makes daily runs fully hands-off.

Endpoint (from the official DhanHQ-py SDK):
  POST https://auth.dhan.co/app/generateAccessToken?dhanClientId=..&pin=..&totp=..
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
MIN_REMAINING_S = 30 * 60


class DhanAuthError(RuntimeError):
    pass


def totp(secret_b32: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """RFC 6238 TOTP (SHA-1), the same code an authenticator app shows."""
    key = base64.b32decode(secret_b32.replace(" ", "").upper() + "=" * (-len(secret_b32.replace(" ", "")) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def token_expiry(token: str) -> float | None:
    """Unix expiry from the JWT payload (no signature check - only used to decide on renewal)."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(data["exp"])
    except Exception:
        return None


def _valid(token: str | None) -> bool:
    if not token:
        return False
    exp = token_expiry(token)
    return exp is None or exp - time.time() > MIN_REMAINING_S  # unknown expiry: let the API decide


def _extract_token(resp_json: dict) -> str | None:
    for key in ("accessToken", "access_token", "token"):
        if resp_json.get(key):
            return str(resp_json[key])
    data = resp_json.get("data")
    return _extract_token(data) if isinstance(data, dict) else None


def generate_with_totp(client_id: str, pin: str, totp_secret: str) -> str:
    # Avoid submitting a code that's about to roll over.
    if 30 - time.time() % 30 < 3:
        time.sleep(3.5)
    resp = requests.post(
        AUTH_URL,
        params={"dhanClientId": client_id, "pin": pin, "totp": totp(totp_secret)},
        timeout=30,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:300]}
    token = _extract_token(body) if resp.status_code == 200 else None
    if not token:
        # Never log the request (it contains the PIN); the response carries no secrets.
        raise DhanAuthError(f"TOTP token generation failed ({resp.status_code}): {str(body)[:300]}")
    return token


def get_access_token(client_id: str, cache_dir: str | Path) -> str:
    cache = Path(cache_dir) / ".dhan_token.json"
    try:
        cached = json.loads(cache.read_text()).get("token") if cache.exists() else None
    except (OSError, ValueError):
        cached = None
    if _valid(cached):
        return cached

    env_token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if env_token and _valid(env_token):
        return env_token

    pin, secret = os.environ.get("DHAN_PIN", "").strip(), os.environ.get("DHAN_TOTP_SECRET", "").strip()
    if pin and secret:
        if not client_id:
            raise DhanAuthError("DHAN_CLIENT_ID is required for TOTP token generation")
        token = generate_with_totp(client_id, pin, secret)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"token": token, "generated": time.time()}))
        try:
            os.chmod(cache, 0o600)
        except OSError:
            pass
        log.info("generated a fresh Dhan access token via TOTP")
        return token

    if env_token:
        raise DhanAuthError("DHAN_ACCESS_TOKEN has expired. Paste a new one, or set DHAN_PIN + DHAN_TOTP_SECRET for auto-renewal")
    raise DhanAuthError("No Dhan credentials: set DHAN_CLIENT_ID plus DHAN_PIN + DHAN_TOTP_SECRET (or DHAN_ACCESS_TOKEN)")
