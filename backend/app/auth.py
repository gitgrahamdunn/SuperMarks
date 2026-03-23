import hashlib
import hmac
import os
import time

from fastapi import Cookie
from fastapi import Header
from fastapi import HTTPException
from fastapi import Request

SESSION_COOKIE_NAME = "sm_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8


def _expected_api_key() -> str:
    return os.getenv("BACKEND_API_KEY", "").strip()


def _session_secret() -> str:
    explicit = os.getenv("BACKEND_SESSION_SECRET", "").strip()
    if explicit:
        return explicit
    return _expected_api_key()


def build_api_session_cookie_value(expected_api_key: str, expires_at: int | None = None) -> str:
    expiry = int(expires_at or (time.time() + SESSION_MAX_AGE_SECONDS))
    secret = _session_secret().encode("utf-8")
    message = f"{expected_api_key}:{expiry}".encode("utf-8")
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def verify_api_session_cookie(cookie_value: str | None, expected_api_key: str) -> bool:
    if not cookie_value or not expected_api_key:
        return False
    try:
        expiry_text, signature = cookie_value.split(".", 1)
        expiry = int(expiry_text)
    except (TypeError, ValueError):
        return False
    if expiry < int(time.time()):
        return False
    secret = _session_secret().encode("utf-8")
    message = f"{expected_api_key}:{expiry}".encode("utf-8")
    expected_signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    if request.method == "OPTIONS":
        return

    expected = _expected_api_key()
    if not expected:
        return

    if x_api_key == expected:
        return
    if verify_api_session_cookie(session_cookie, expected):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")
