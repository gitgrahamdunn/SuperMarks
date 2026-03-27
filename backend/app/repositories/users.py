"""User-oriented repository functions."""

from __future__ import annotations

from sqlmodel import select

from app.models import AppLoginToken, AppUser, utcnow
from app.persistence import DbSession


def get_user_by_id(session: DbSession, user_id: int) -> AppUser | None:
    return session.get(AppUser, user_id)


def get_user_by_identity(session: DbSession, *, auth_issuer: str, auth_subject: str) -> AppUser | None:
    return session.exec(
        select(AppUser).where(
            AppUser.auth_issuer == auth_issuer,
            AppUser.auth_subject == auth_subject,
        )
    ).first()


def upsert_user_identity(
    session: DbSession,
    *,
    auth_issuer: str,
    auth_subject: str,
    email: str | None,
    full_name: str | None,
    given_name: str | None,
    family_name: str | None,
    picture_url: str | None,
) -> AppUser:
    existing = get_user_by_identity(
        session,
        auth_issuer=auth_issuer,
        auth_subject=auth_subject,
    )
    if existing:
        existing.email = email
        existing.full_name = full_name
        existing.given_name = given_name
        existing.family_name = family_name
        existing.picture_url = picture_url
        existing.updated_at = utcnow()
        session.add(existing)
        session.flush()
        return existing

    created = AppUser(
        auth_issuer=auth_issuer,
        auth_subject=auth_subject,
        email=email,
        full_name=full_name,
        given_name=given_name,
        family_name=family_name,
        picture_url=picture_url,
    )
    session.add(created)
    session.flush()
    return created


def create_login_token(
    session: DbSession,
    *,
    user_id: int,
    email: str,
    token_hash: str,
    expires_at,
) -> AppLoginToken:
    row = AppLoginToken(
        user_id=user_id,
        email=email,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def get_login_token_by_hash(session: DbSession, *, token_hash: str) -> AppLoginToken | None:
    return session.exec(select(AppLoginToken).where(AppLoginToken.token_hash == token_hash)).first()


def mark_login_token_used(session: DbSession, token: AppLoginToken, *, used_at=None) -> AppLoginToken:
    token.used_at = used_at or utcnow()
    session.add(token)
    session.flush()
    return token
