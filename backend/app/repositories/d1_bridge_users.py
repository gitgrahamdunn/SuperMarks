"""D1 bridge-backed user repository functions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.d1_bridge import get_d1_bridge_client
from app.models import AppLoginToken, AppUser, utcnow
from app.persistence import DbSession


def _bridge():
    return get_d1_bridge_client()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _hydrate_user(row: dict[str, Any] | None) -> AppUser | None:
    if not isinstance(row, dict):
        return None
    return AppUser.model_validate(row)


def _hydrate_login_token(row: dict[str, Any] | None) -> AppLoginToken | None:
    if not isinstance(row, dict):
        return None
    return AppLoginToken.model_validate(row)


def get_user_by_id(session: DbSession, user_id: int) -> AppUser | None:
    _ = session
    return _hydrate_user(
        _bridge().query_first(
            """
            SELECT id, auth_issuer, auth_subject, email, full_name, given_name, family_name, picture_url, created_at, updated_at
            FROM appuser
            WHERE id = ?
            """,
            [user_id],
        )
    )


def get_user_by_identity(session: DbSession, *, auth_issuer: str, auth_subject: str) -> AppUser | None:
    _ = session
    return _hydrate_user(
        _bridge().query_first(
            """
            SELECT id, auth_issuer, auth_subject, email, full_name, given_name, family_name, picture_url, created_at, updated_at
            FROM appuser
            WHERE auth_issuer = ? AND auth_subject = ?
            """,
            [auth_issuer, auth_subject],
        )
    )


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
    _ = session
    existing = get_user_by_identity(session, auth_issuer=auth_issuer, auth_subject=auth_subject)
    if existing:
        row = _bridge().query_first(
            """
            UPDATE appuser
            SET email = ?, full_name = ?, given_name = ?, family_name = ?, picture_url = ?, updated_at = ?
            WHERE id = ?
            RETURNING id, auth_issuer, auth_subject, email, full_name, given_name, family_name, picture_url, created_at, updated_at
            """,
            [
                email,
                full_name,
                given_name,
                family_name,
                picture_url,
                _normalize_value(utcnow()),
                int(existing.id or 0),
            ],
        )
        updated = _hydrate_user(row)
        if updated is None:
            raise RuntimeError("D1 bridge did not return the updated user row")
        return updated

    row = _bridge().query_first(
        """
        INSERT INTO appuser
            (auth_issuer, auth_subject, email, full_name, given_name, family_name, picture_url, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, auth_issuer, auth_subject, email, full_name, given_name, family_name, picture_url, created_at, updated_at
        """,
        [
            auth_issuer,
            auth_subject,
            email,
            full_name,
            given_name,
            family_name,
            picture_url,
            _normalize_value(utcnow()),
            _normalize_value(utcnow()),
        ],
    )
    created = _hydrate_user(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created user row")
    return created


def create_login_token(
    session: DbSession,
    *,
    user_id: int,
    email: str,
    token_hash: str,
    expires_at,
) -> AppLoginToken:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO applogintoken (user_id, email, token_hash, expires_at, used_at, created_at)
        VALUES (?, ?, ?, ?, NULL, ?)
        RETURNING id, user_id, email, token_hash, expires_at, used_at, created_at
        """,
        [user_id, email, token_hash, _normalize_value(expires_at), _normalize_value(utcnow())],
    )
    created = _hydrate_login_token(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created login token row")
    return created


def get_login_token_by_hash(session: DbSession, *, token_hash: str) -> AppLoginToken | None:
    _ = session
    return _hydrate_login_token(
        _bridge().query_first(
            """
            SELECT id, user_id, email, token_hash, expires_at, used_at, created_at
            FROM applogintoken
            WHERE token_hash = ?
            """,
            [token_hash],
        )
    )


def mark_login_token_used(session: DbSession, token: AppLoginToken, *, used_at=None) -> AppLoginToken:
    _ = session
    row = _bridge().query_first(
        """
        UPDATE applogintoken
        SET used_at = ?
        WHERE id = ?
        RETURNING id, user_id, email, token_hash, expires_at, used_at, created_at
        """,
        [_normalize_value(used_at or utcnow()), int(token.id or 0)],
    )
    updated = _hydrate_login_token(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated login token row")
    return updated
