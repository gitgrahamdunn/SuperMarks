from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


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
        parsed = urlsplit(value)
        value = parsed.path

    normalized = value.lstrip("/")
    if not normalized:
        raise ValueError("pathname must include a path")
    return normalized


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _snippet(body: bytes, limit: int = 300) -> str:
    return body.decode("utf-8", errors="replace")[:limit]


async def get_signed_read_url(pathname: str, expires_seconds: int = 600) -> str:
    normalized_pathname = normalize_blob_pathname(pathname)
    if blob_mock_enabled():
        return "https://example.com/mock"

    token = get_blob_token()

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
        raise BlobSignedUrlError(
            f"Blob signed URL request failed pathname={normalized_pathname} url={_safe_url(endpoint)} error={exc}"
        ) from exc

    if response.status_code != 200:
        raise BlobSignedUrlError(
            "Blob signed URL failed "
            f"pathname={normalized_pathname} url={_safe_url(endpoint)} status={response.status_code} "
            f"body={_snippet(response.content)}"
        )

    payload = response.json() if response.content else {}
    signed_url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
    if not signed_url:
        raise BlobSignedUrlError(
            f"Blob signed URL failed pathname={normalized_pathname} url={_safe_url(endpoint)} status=200 body=missing url"
        )
    return signed_url


async def download_blob_bytes(pathname: str) -> tuple[bytes, str]:
    normalized_pathname = normalize_blob_pathname(pathname)
    if blob_mock_enabled():
        return (_MOCK_BYTES, "application/pdf")

    signed = await get_signed_read_url(normalized_pathname)
    safe_signed = _safe_url(signed)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(signed)
    except Exception as exc:
        raise BlobDownloadError(
            f"Blob download request failed pathname={normalized_pathname} url={safe_signed} error={exc}"
        ) from exc

    if response.status_code != 200:
        raise BlobDownloadError(
            "Blob download failed "
            f"pathname={normalized_pathname} url={safe_signed} status={response.status_code} "
            f"body={_snippet(response.content)}"
        )

    return response.content, response.headers.get("content-type", "")


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    return await get_signed_read_url(pathname, expires_seconds=expires_seconds)
