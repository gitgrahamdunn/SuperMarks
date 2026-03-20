from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from vercel.blob import AsyncBlobClient

from app.settings import settings


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


def blob_mock_enabled() -> bool:
    return os.getenv("BLOB_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def get_blob_token() -> str:
    if blob_mock_enabled():
        return "mock-token"
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise BlobConfigError(["BLOB_READ_WRITE_TOKEN"])
    return token


def normalize_blob_path(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("pathname must be non-empty")

    if "blob.vercel-storage.com/v1/sign" in value:
        raise RuntimeError("Old blob sign path should never be used")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlsplit(value)
        value = parsed.path

    normalized = value.lstrip("/")
    if not normalized:
        raise ValueError("pathname must include a path")
    return normalized


async def download_blob_bytes(pathname: str) -> tuple[bytes, str | None]:
    normalized_pathname = normalize_blob_path(pathname)
    logger.info("blob_private_read pathname=%s", normalized_pathname)

    result = None
    sdk_exc: Exception | None = None
    try:
        client = AsyncBlobClient()
        result = await client.get(normalized_pathname, access="private")
    except Exception as exc:
        sdk_exc = exc
        if not blob_mock_enabled():
            logger.exception("Blob SDK get failed pathname=%s", normalized_pathname)
            raise BlobDownloadError(
                f"Blob SDK get failed pathname={normalized_pathname} error={exc}"
            ) from exc

    if result is None and blob_mock_enabled():
        local_object = settings.data_path / "objects" / normalized_pathname
        if local_object.exists():
            content_type = None
            if local_object.suffix.lower() == ".png":
                content_type = "image/png"
            elif local_object.suffix.lower() in {".jpg", ".jpeg"}:
                content_type = "image/jpeg"
            elif local_object.suffix.lower() == ".pdf":
                content_type = "application/pdf"
            return local_object.read_bytes(), content_type

    if result is None:
        if sdk_exc is not None:
            logger.exception("Blob SDK get failed pathname=%s", normalized_pathname)
        else:
            logger.exception("Blob not found or unreadable pathname=%s", normalized_pathname)
        raise BlobDownloadError(f"Blob not found or unreadable: {normalized_pathname}")

    public_attrs = sorted(name for name in dir(result) if not name.startswith("_"))
    logger.info(
        "blob_private_read_result_shape type=%s attrs=%s pathname=%s",
        type(result).__name__,
        public_attrs,
        normalized_pathname,
    )

    status_code = getattr(result, "status_code", None)
    if isinstance(status_code, int) and status_code != 200:
        raise BlobDownloadError(f"Blob not found or unreadable: {normalized_pathname}")

    try:
        content_bytes = await _read_blob_result_bytes(result)
    except Exception as exc:
        logger.exception("Blob read failed pathname=%s", normalized_pathname)
        raise BlobDownloadError(
            f"Blob read failed pathname={normalized_pathname} error={exc}"
        ) from exc

    content_type = _extract_content_type(result)
    return content_bytes, content_type


def _extract_content_type(result: Any) -> str | None:
    content_type = getattr(result, "content_type", None)
    if isinstance(content_type, str):
        return content_type
    blob = getattr(result, "blob", None)
    blob_content_type = getattr(blob, "content_type", None)
    if isinstance(blob_content_type, str):
        return blob_content_type
    return None


async def _read_blob_result_bytes(result: Any) -> bytes:
    direct = _coerce_bytes(getattr(result, "content", None))
    if direct is not None:
        return direct

    direct = _coerce_bytes(getattr(result, "body", None))
    if direct is not None:
        return direct

    read_fn = getattr(result, "read", None)
    if callable(read_fn):
        read_value = read_fn()
        if hasattr(read_value, "__await__"):
            read_value = await read_value
        direct = _coerce_bytes(read_value)
        if direct is not None:
            return direct

    response = getattr(result, "response", None)
    if response is not None:
        response_content = _coerce_bytes(getattr(response, "content", None))
        if response_content is not None:
            return response_content

        aiter_bytes = getattr(response, "aiter_bytes", None)
        if callable(aiter_bytes):
            chunks: list[bytes] = []
            iterator = aiter_bytes()
            if isinstance(iterator, AsyncIterator):
                async for chunk in iterator:
                    chunks.append(bytes(chunk))
                return b"".join(chunks)

    public_attrs = sorted(name for name in dir(result) if not name.startswith("_"))
    raise RuntimeError(
        f"Unsupported blob result type={type(result).__name__} attrs={public_attrs}"
    )


def _coerce_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray | memoryview):
        return bytes(value)
    return None


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    del expires_seconds
    normalized_pathname = normalize_blob_path(pathname)
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


normalize_blob_pathname = normalize_blob_path
