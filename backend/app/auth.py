from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException
from fastapi import Request

from app.models import AppUser
from app.persistence import open_repository_session, repository_provider
from app.settings import settings

BROWSER_SESSION_COOKIE_NAME = "sm_browser_session"
SESSION_COOKIE_NAME = "sm_session"
APP_TOKEN_STORAGE_KEY = "sm_token"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 8


@dataclass(frozen=True)
class OIDCProviderConfig:
    slug: str
    name: str
    client_id: str
    client_secret: str
    server_metadata_url: str
    scope: str = "openid email profile"
    prompt: str | None = None


@dataclass(frozen=True)
class RequestAuthContext:
    kind: str
    user: AppUser | None = None
    token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.kind in {"api_key", "user"}

    @property
    def is_api_key_admin(self) -> bool:
        return self.kind == "api_key"

    @property
    def user_id(self) -> int | None:
        return self.user.id if self.user else None


_ANONYMOUS_AUTH_CONTEXT = RequestAuthContext(kind="anonymous", user=None, token=None)
_current_auth_context: ContextVar[RequestAuthContext] = ContextVar("supermarks_current_auth_context", default=_ANONYMOUS_AUTH_CONTEXT)


def _expected_api_key() -> str:
    return os.getenv("BACKEND_API_KEY", "").strip()


@lru_cache(maxsize=1)
def configured_oidc_providers() -> dict[str, OIDCProviderConfig]:
    raw = settings.oidc_providers_json.strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise RuntimeError("SUPERMARKS_OIDC_PROVIDERS_JSON must be a JSON array")

    providers: dict[str, OIDCProviderConfig] = {}
    for item in parsed:
        if not isinstance(item, dict):
            raise RuntimeError("Each OIDC provider entry must be an object")
        slug = str(item.get("slug") or "").strip().lower()
        client_id = str(item.get("client_id") or "").strip()
        client_secret = str(item.get("client_secret") or "").strip()
        server_metadata_url = str(item.get("server_metadata_url") or "").strip()
        if not slug or not client_id or not client_secret or not server_metadata_url:
            raise RuntimeError("Each OIDC provider requires slug, client_id, client_secret, and server_metadata_url")
        providers[slug] = OIDCProviderConfig(
            slug=slug,
            name=str(item.get("name") or slug.replace("-", " ").title()).strip(),
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=server_metadata_url,
            scope=str(item.get("scope") or "openid email profile").strip(),
            prompt=str(item.get("prompt") or "").strip() or None,
        )
    return providers


@lru_cache(maxsize=1)
def oidc_oauth_registry() -> OAuth:
    oauth = OAuth()
    for provider in configured_oidc_providers().values():
        oauth.register(
            name=provider.slug,
            client_id=provider.client_id,
            client_secret=provider.client_secret,
            server_metadata_url=provider.server_metadata_url,
            client_kwargs={"scope": provider.scope},
        )
    return oauth


def auth_is_required() -> bool:
    return bool(_expected_api_key() or configured_oidc_providers() or magic_link_enabled())


def auth_is_enabled() -> bool:
    return bool(configured_oidc_providers() or magic_link_enabled())


def magic_link_enabled() -> bool:
    return settings.magic_link_login_enabled and settings.email_delivery_enabled


def _token_secret() -> str:
    explicit = (settings.auth_session_secret or "").strip()
    if explicit:
        return explicit
    fallback = _expected_api_key()
    if fallback:
        return fallback
    raise RuntimeError("Set SUPERMARKS_AUTH_SESSION_SECRET or BACKEND_SESSION_SECRET before enabling auth tokens")


def build_api_session_cookie_value(expected_api_key: str, *, expires_at: int | None = None) -> str:
    expiry = int(expires_at or (time.time() + SESSION_MAX_AGE_SECONDS))
    message = f"{expected_api_key}:{expiry}".encode("utf-8")
    signature = hmac.new(_token_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()
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
    message = f"{expected_api_key}:{expiry}".encode("utf-8")
    expected_signature = hmac.new(_token_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, signature)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def build_user_bearer_token(user_id: int, *, expires_at: int | None = None) -> str:
    expiry = int(expires_at or (time.time() + settings.auth_token_ttl_seconds))
    payload = {"uid": user_id, "exp": expiry, "typ": "user"}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_token_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v1.{payload_b64}.{signature}"


def _verify_user_bearer_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        version, payload_b64, provided_signature = token.split(".", 2)
    except ValueError:
        return None
    if version != "v1":
        return None
    expected_signature = hmac.new(_token_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    if payload.get("typ") != "user":
        return None
    return payload


def normalize_email_address(email: str) -> str:
    return email.strip().lower()


def build_magic_link_token() -> str:
    return secrets.token_urlsafe(32)


def hash_magic_link_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _request_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip() or None
    if request.method.upper() == "GET":
        query_token = request.query_params.get("access_token", "").strip()
        if query_token:
            return query_token
    return None


def _session_user_id(request: Request) -> int | None:
    try:
        user_id = request.session.get("user_id")
    except Exception:
        return None
    if isinstance(user_id, int):
        return user_id
    if isinstance(user_id, str) and user_id.isdigit():
        return int(user_id)
    return None


def _load_user_by_id(user_id: int) -> AppUser | None:
    user_repo = repository_provider().users
    with open_repository_session() as session:
        return user_repo.get_user_by_id(session, user_id)


def resolve_request_auth_context(request: Request) -> RequestAuthContext:
    bearer_token = _request_bearer_token(request)
    if bearer_token:
        payload = _verify_user_bearer_token(bearer_token)
        if payload and payload.get("uid"):
            user = _load_user_by_id(int(payload["uid"]))
            if user is not None:
                return RequestAuthContext(kind="user", user=user, token=bearer_token)

    session_user_id = _session_user_id(request)
    if session_user_id is not None:
        user = _load_user_by_id(session_user_id)
        if user is not None:
            return RequestAuthContext(kind="user", user=user, token=None)

    expected_api_key = _expected_api_key()
    presented_api_key = request.headers.get("x-api-key", "").strip()
    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if expected_api_key and (
        presented_api_key == expected_api_key
        or verify_api_session_cookie(session_cookie, expected_api_key)
    ):
        return RequestAuthContext(kind="api_key", user=None, token=None)

    return _ANONYMOUS_AUTH_CONTEXT


def current_auth_context() -> RequestAuthContext:
    return _current_auth_context.get()


def current_user_owner_id() -> int | None:
    context = current_auth_context()
    if context.kind == "user" and context.user is not None:
        return context.user.id
    return None


def can_access_owned_resource(owner_user_id: int | None) -> bool:
    if not auth_is_required():
        return True
    context = current_auth_context()
    if context.is_api_key_admin:
        return True
    if context.kind != "user" or context.user is None:
        return False
    if owner_user_id is None:
        return False
    return context.user.id == owner_user_id


def require_authenticated_request(request: Request) -> None:
    if request.method == "OPTIONS":
        return
    if not auth_is_required():
        return
    if current_auth_context().is_authenticated:
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


@contextmanager
def auth_context_middleware(request: Request):
    context = resolve_request_auth_context(request)
    token = _current_auth_context.set(context)
    try:
        yield context
    finally:
        _current_auth_context.reset(token)


def clear_browser_session(request: Request) -> None:
    try:
        request.session.clear()
    except Exception:
        return


def set_browser_user_session(request: Request, *, user: AppUser | None = None, user_id: int | None = None) -> None:
    request.session.clear()
    resolved_user_id = user_id
    if resolved_user_id is None and user is not None:
        resolved_user_id = int(user.id or 0)
    request.session["user_id"] = int(resolved_user_id or 0)


@dataclass(frozen=True)
class PublicAuthProvider:
    slug: str
    name: str


def public_auth_providers() -> list[PublicAuthProvider]:
    return [PublicAuthProvider(slug=provider.slug, name=provider.name) for provider in configured_oidc_providers().values()]


def get_provider_or_404(provider_slug: str) -> OIDCProviderConfig:
    provider = configured_oidc_providers().get(provider_slug.strip().lower())
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown auth provider")
    return provider


def validate_return_to(return_to: str | None) -> str:
    fallback = settings.auth_return_origin_list[0] if settings.auth_return_origin_list else "http://localhost:5173/auth/callback"
    candidate = (return_to or "").strip() or fallback
    split = urlsplit(candidate)
    if not split.scheme or not split.netloc:
        raise HTTPException(status_code=400, detail="Invalid return_to URL")
    normalized_origin = f"{split.scheme}://{split.netloc}"
    if normalized_origin not in settings.auth_return_origin_list:
        raise HTTPException(status_code=400, detail="return_to origin is not allowed")
    return candidate
