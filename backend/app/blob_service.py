from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException


class BlobConfigError(RuntimeError):
    """Raised when required blob configuration is missing."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("Blob not configured")


class BlobSignedUrlError(RuntimeError):
    """Raised when a signed read URL cannot be created."""


class BlobDownloadError(RuntimeError):
    """Raised when blob bytes cannot be downloaded."""


_MOCK_BYTES = b"%PDF-1.4\n%mock blob content\n"


def blob_mock_enabled() -> bool:
    return os.getenv("BLOB_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def get_blob_token() -> str:
    if blob_mock_enabled():
        return "mock-token"
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise BlobConfigError(["BLOB_READ_WRITE_TOKEN"])
    return token


def normalize_blob_pathname(pathname: str) -> str:
    value = (pathname or "").strip()
    if not value:
        raise ValueError("pathname must be non-empty")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = parsed.path

    normalized = value.lstrip("/")
    if not normalized:
        raise ValueError("pathname must include a path")
    return normalized


async def get_signed_read_url(pathname: str, expires_seconds: int = 600) -> str:
    normalized_pathname = normalize_blob_pathname(pathname)
    if blob_mock_enabled():
        return "https://example.com/mock"

    try:
        token = get_blob_token()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": "Blob signed URL failed", "pathname": normalized_pathname},
        ) from exc

    expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)).timestamp())
    endpoint = f"https://blob.vercel-storage.com/v1/sign/{quote(normalized_pathname, safe='/-_.~')}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.put(
                endpoint,
                json={"expiresAt": expires_at, "allowWrite": False},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": "Blob signed URL failed", "pathname": normalized_pathname},
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"message": "Blob signed URL failed", "pathname": normalized_pathname, "status": response.status_code},
        )

    payload = response.json() if response.content else {}
    signed_url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
    if not signed_url:
        raise HTTPException(
            status_code=502,
            detail={"message": "Blob signed URL failed", "pathname": normalized_pathname},
        )
    return signed_url


async def download_blob_bytes(pathname: str) -> tuple[bytes, str]:
    normalized_pathname = normalize_blob_pathname(pathname)
    if blob_mock_enabled():
        return (_MOCK_BYTES, "application/pdf")

    signed = await get_signed_read_url(normalized_pathname)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(signed)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": "Blob download failed", "pathname": normalized_pathname},
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={"message": "Blob download failed", "pathname": normalized_pathname, "status": response.status_code},
        )

    return response.content, response.headers.get("content-type", "")


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    return await get_signed_read_url(pathname, expires_seconds=expires_seconds)
