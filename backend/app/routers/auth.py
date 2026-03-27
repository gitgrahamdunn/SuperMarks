from __future__ import annotations

from datetime import timedelta, timezone
import hmac
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from pydantic import BaseModel
from starlette.responses import RedirectResponse

from app.auth import (
    APP_TOKEN_STORAGE_KEY,
    auth_is_enabled,
    build_magic_link_token,
    build_user_bearer_token,
    clear_browser_session,
    current_auth_context,
    dev_login_enabled,
    get_provider_or_404,
    hash_magic_link_token,
    magic_link_enabled,
    normalize_email_address,
    oidc_oauth_registry,
    public_auth_providers,
    set_browser_user_session,
    validate_return_to,
)
from app.emailer import send_magic_link_email
from app.models import utcnow
from app.persistence import commit_repository_session, open_repository_session, repository_provider
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])
user_repo = repository_provider().users


class AuthProviderRead(BaseModel):
    slug: str
    name: str


class AuthUserRead(BaseModel):
    id: int
    email: str | None = None
    full_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    picture_url: str | None = None


class AuthStatusRead(BaseModel):
    auth_enabled: bool
    magic_link_enabled: bool
    dev_login_enabled: bool
    authenticated: bool
    auth_method: str
    user: AuthUserRead | None = None
    providers: list[AuthProviderRead]


class LogoutResponse(BaseModel):
    ok: bool


class MagicLinkRequest(BaseModel):
    email: str
    return_to: str | None = None


class MagicLinkRequestResponse(BaseModel):
    ok: bool


class DevLoginRequest(BaseModel):
    key: str


class DevLoginResponse(BaseModel):
    ok: bool
    token: str


def _oauth_client(provider_slug: str):
    registry = oidc_oauth_registry()
    client = getattr(registry, provider_slug, None)
    if client is not None:
        return client
    return registry.create_client(provider_slug)


def _user_info_payload(_: dict[str, Any]) -> AuthUserRead | None:
    context = current_auth_context()
    if context.user is None:
        return None
    return AuthUserRead(
        id=int(context.user.id or 0),
        email=context.user.email,
        full_name=context.user.full_name,
        given_name=context.user.given_name,
        family_name=context.user.family_name,
        picture_url=context.user.picture_url,
    )


@router.get("/providers", response_model=list[AuthProviderRead])
def list_auth_providers() -> list[AuthProviderRead]:
    return [AuthProviderRead(slug=item.slug, name=item.name) for item in public_auth_providers()]


@router.get("/me", response_model=AuthStatusRead)
def get_auth_status() -> AuthStatusRead:
    context = current_auth_context()
    return AuthStatusRead(
        auth_enabled=auth_is_enabled(),
        magic_link_enabled=magic_link_enabled(),
        dev_login_enabled=dev_login_enabled(),
        authenticated=context.is_authenticated,
        auth_method=context.kind,
        user=_user_info_payload({}),
        providers=[AuthProviderRead(slug=item.slug, name=item.name) for item in public_auth_providers()],
    )


def _validated_magic_link_email(email: str) -> str:
    normalized_email = normalize_email_address(email)
    if not normalized_email or "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    return normalized_email


def _token_is_expired(expires_at) -> bool:
    if expires_at is None:
        return True
    if getattr(expires_at, "tzinfo", None) is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= utcnow()


@router.post("/magic-link/request", response_model=MagicLinkRequestResponse)
async def request_magic_link(payload: MagicLinkRequest, request: Request) -> MagicLinkRequestResponse:
    if not magic_link_enabled():
        raise HTTPException(status_code=404, detail="Magic link login is not enabled")

    normalized_email = _validated_magic_link_email(payload.email)
    return_to = validate_return_to(payload.return_to)
    raw_token = build_magic_link_token()
    token_hash = hash_magic_link_token(raw_token)
    expires_at = utcnow() + timedelta(seconds=settings.magic_link_token_ttl_seconds)

    with open_repository_session() as session:
        user = user_repo.upsert_user_identity(
            session,
            auth_issuer="email",
            auth_subject=normalized_email,
            email=normalized_email,
            full_name=None,
            given_name=None,
            family_name=None,
            picture_url=None,
        )
        user_repo.create_login_token(
            session,
            user_id=int(user.id or 0),
            email=normalized_email,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        commit_repository_session(session)

    verify_url = str(request.url_for("verify_magic_link"))
    magic_link_url = f"{verify_url}?token={raw_token}&return_to={return_to}"
    await send_magic_link_email(email=normalized_email, magic_link_url=magic_link_url)
    return MagicLinkRequestResponse(ok=True)


@router.post("/dev-login", response_model=DevLoginResponse)
def login_with_dev_key(payload: DevLoginRequest, request: Request) -> DevLoginResponse:
    if not dev_login_enabled():
        raise HTTPException(status_code=404, detail="Developer login is not enabled")

    expected_key = (settings.dev_login_key or "").strip()
    if not expected_key or not hmac.compare_digest(payload.key.strip(), expected_key):
        raise HTTPException(status_code=401, detail="Invalid developer login key")

    dev_email = normalize_email_address(settings.dev_login_email)
    with open_repository_session() as session:
        user = user_repo.upsert_user_identity(
            session,
            auth_issuer="dev-login",
            auth_subject=dev_email,
            email=dev_email,
            full_name=settings.dev_login_name.strip() or "Codex Dev",
            given_name="Codex",
            family_name="Dev",
            picture_url=None,
        )
        user_id = int(user.id or 0)
        commit_repository_session(session)

    set_browser_user_session(request, user_id=user_id)
    token = build_user_bearer_token(user_id)
    return DevLoginResponse(ok=True, token=token)


@router.get("/login/{provider_slug}")
async def begin_login(provider_slug: str, request: Request, return_to: str | None = Query(default=None)):
    provider = get_provider_or_404(provider_slug)
    redirect_target = validate_return_to(return_to)
    request.session["auth_return_to"] = redirect_target
    client = _oauth_client(provider.slug)
    callback_url = str(request.url_for("complete_login", provider_slug=provider.slug))
    authorize_params: dict[str, str] = {}
    if provider.prompt:
        authorize_params["prompt"] = provider.prompt
    return await client.authorize_redirect(request, callback_url, **authorize_params)


@router.get("/callback/{provider_slug}", name="complete_login")
async def complete_login(provider_slug: str, request: Request):
    provider = get_provider_or_404(provider_slug)
    client = _oauth_client(provider.slug)
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OAuth callback failed: {exc}") from exc

    userinfo = token.get("userinfo") if isinstance(token, dict) else None
    if not isinstance(userinfo, dict):
        try:
            fetched = await client.userinfo(token=token)
            userinfo = dict(fetched)
        except Exception:
            userinfo = None
    if not isinstance(userinfo, dict):
        raise HTTPException(status_code=400, detail="Provider did not return user profile data")

    subject = str(userinfo.get("sub") or "").strip()
    issuer = str(userinfo.get("iss") or provider.server_metadata_url).strip()
    if not subject or not issuer:
        raise HTTPException(status_code=400, detail="Provider identity payload was incomplete")

    with open_repository_session() as session:
        user = user_repo.upsert_user_identity(
            session,
            auth_issuer=issuer,
            auth_subject=subject,
            email=str(userinfo.get("email") or "").strip() or None,
            full_name=str(userinfo.get("name") or "").strip() or None,
            given_name=str(userinfo.get("given_name") or "").strip() or None,
            family_name=str(userinfo.get("family_name") or "").strip() or None,
            picture_url=str(userinfo.get("picture") or userinfo.get("avatar_url") or "").strip() or None,
        )
        user_id = int(user.id or 0)
        commit_repository_session(session)

    set_browser_user_session(request, user_id=user_id)
    app_token = build_user_bearer_token(user_id)
    return_to = validate_return_to(request.session.pop("auth_return_to", None))
    separator = "&" if "#" in return_to else "#"
    return RedirectResponse(f"{return_to}{separator}{APP_TOKEN_STORAGE_KEY}={app_token}", status_code=302)


@router.get("/magic-link/verify", name="verify_magic_link")
def verify_magic_link(token: str, request: Request, return_to: str | None = Query(default=None)):
    if not magic_link_enabled():
        raise HTTPException(status_code=404, detail="Magic link login is not enabled")

    trimmed_token = token.strip()
    if not trimmed_token:
        raise HTTPException(status_code=400, detail="Magic link is invalid or expired")
    token_hash = hash_magic_link_token(trimmed_token)

    with open_repository_session() as session:
        login_token = user_repo.get_login_token_by_hash(session, token_hash=token_hash)
        if login_token is None or login_token.used_at is not None or _token_is_expired(login_token.expires_at):
            raise HTTPException(status_code=400, detail="Magic link is invalid or expired")

        user = user_repo.get_user_by_id(session, int(login_token.user_id or 0))
        if user is None:
            raise HTTPException(status_code=400, detail="Magic link user account was not found")

        user_id = int(user.id or 0)
        user_repo.mark_login_token_used(session, login_token)
        commit_repository_session(session)

    set_browser_user_session(request, user_id=user_id)
    app_token = build_user_bearer_token(user_id)
    target = validate_return_to(return_to)
    separator = "&" if "#" in target else "#"
    return RedirectResponse(f"{target}{separator}{APP_TOKEN_STORAGE_KEY}={app_token}", status_code=302)


@router.post("/logout", response_model=LogoutResponse)
def logout(request: Request) -> LogoutResponse:
    clear_browser_session(request)
    return LogoutResponse(ok=True)
