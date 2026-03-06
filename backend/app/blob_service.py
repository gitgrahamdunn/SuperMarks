from __future__ import annotations

import os
import logging
from urllib.parse import urlsplit

from vercel.blob import AsyncBlobClient


class BlobConfigError(RuntimeError):
    """Raised when required blob configuration is missing."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("Blob not configured")


class BlobDownloadError(RuntimeError):
    """Raised when blob bytes cannot be downloaded."""


class BlobSignedUrlError(RuntimeError):
    """Raised when blob URL for frontend access cannot be created."""


logger = logging.getLogger(__name__)


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


async def download_blob_bytes(pathname: str) -> tuple[bytes, str | None]:
    normalized_pathname = normalize_blob_pathname(pathname)
    if blob_mock_enabled():
        return (_MOCK_BYTES, "application/pdf")

    client = AsyncBlobClient()
    try:
        result = await client.get(normalized_pathname, access="private")
    except Exception as exc:
        logger.exception("Blob SDK get failed pathname=%s", normalized_pathname)
        raise BlobDownloadError(
            f"Blob SDK get failed pathname={normalized_pathname} error={exc}"
        ) from exc

    if result is None or result.status_code != 200 or result.stream is None:
        try:
            raise RuntimeError("blob result missing/invalid")
        except RuntimeError as exc:
            logger.exception("Blob not found or unreadable pathname=%s", normalized_pathname)
            raise BlobDownloadError(f"Blob not found or unreadable: {normalized_pathname}") from exc

    chunks: list[bytes] = []
    try:
        async for chunk in result.stream:
            chunks.append(chunk)
    except Exception as exc:
        logger.exception("Blob stream read failed pathname=%s", normalized_pathname)
        raise BlobDownloadError(
            f"Blob stream read failed pathname={normalized_pathname} error={exc}"
        ) from exc

    content_type = None
    try:
        content_type = result.blob.content_type
    except Exception:
        content_type = None
    return b"".join(chunks), content_type


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    del expires_seconds
    normalized_pathname = normalize_blob_pathname(pathname)
    if blob_mock_enabled():
        return "https://example.com/mock"

    try:
        result = await AsyncBlobClient().get(normalized_pathname, access="private")
    except Exception as exc:
        logger.exception("Blob signed URL lookup failed pathname=%s", normalized_pathname)
        raise BlobSignedUrlError(f"Blob signed URL lookup failed pathname={normalized_pathname} error={exc}") from exc

    if result is None or result.status_code != 200:
        raise BlobSignedUrlError(f"Blob not found or unreadable: {normalized_pathname}")

    candidate = str(getattr(result, "url", "") or "").strip()
    if candidate:
        return candidate
    candidate = str(getattr(result, "download_url", "") or "").strip()
    if candidate:
        return candidate
    raise BlobSignedUrlError(f"Blob URL unavailable for pathname={normalized_pathname}")


async def get_signed_read_url(pathname: str, expires_seconds: int = 600) -> str:
    return await create_signed_blob_url(pathname, expires_seconds=expires_seconds)
