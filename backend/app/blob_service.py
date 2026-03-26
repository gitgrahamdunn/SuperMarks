from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import quote, urlsplit

import boto3

from app.settings import settings


class BlobConfigError(RuntimeError):
    """Raised when required blob configuration is missing."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("Blob storage not configured")


class BlobDownloadError(RuntimeError):
    """Raised when blob bytes cannot be downloaded."""


class BlobSignedUrlError(RuntimeError):
    """Raised when blob URL for frontend access cannot be created."""


logger = logging.getLogger(__name__)


def blob_mock_enabled() -> bool:
    return os.getenv("BLOB_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_blob_path(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("pathname must be non-empty")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlsplit(value)
        value = parsed.path

    normalized = value.lstrip("/")
    if not normalized:
        raise ValueError("pathname must include a path")
    return normalized


def _storage_backend() -> str:
    return settings.storage_backend.lower().strip()


def _local_object_path(pathname: str):
    return settings.data_path / "objects" / pathname


def _guess_content_type(pathname: str) -> str | None:
    lower = pathname.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".pdf"):
        return "application/pdf"
    return None


def _missing_s3_config() -> list[str]:
    missing: list[str] = []
    if not (settings.s3_bucket or "").strip():
        missing.append("S3_BUCKET")
    if not (settings.s3_access_key_id or "").strip():
        missing.append("S3_ACCESS_KEY_ID")
    if not (settings.s3_secret_access_key or "").strip():
        missing.append("S3_SECRET_ACCESS_KEY")
    return missing


def _get_s3_client():
    missing = _missing_s3_config()
    if missing:
        raise BlobConfigError(missing)
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )


async def download_blob_bytes(pathname: str) -> tuple[bytes, str | None]:
    normalized_pathname = normalize_blob_path(pathname)
    logger.info("blob_read pathname=%s backend=%s", normalized_pathname, _storage_backend())

    local_object = _local_object_path(normalized_pathname)
    if blob_mock_enabled() or _storage_backend() != "s3":
        if local_object.exists():
            return await asyncio.to_thread(local_object.read_bytes), _guess_content_type(normalized_pathname)
        raise BlobDownloadError(f"Blob not found or unreadable: {normalized_pathname}")

    try:
        response = await asyncio.to_thread(
            _get_s3_client().get_object,
            Bucket=settings.s3_bucket,
            Key=normalized_pathname,
        )
    except BlobConfigError:
        raise
    except Exception as exc:
        logger.exception("S3 blob get failed pathname=%s", normalized_pathname)
        raise BlobDownloadError(
            f"Blob get failed pathname={normalized_pathname} error={exc}"
        ) from exc

    body = response.get("Body")
    if body is None:
        raise BlobDownloadError(f"Blob body unavailable: {normalized_pathname}")
    content = await asyncio.to_thread(body.read)
    content_type = response.get("ContentType")
    return content, content_type if isinstance(content_type, str) else None


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    normalized_pathname = normalize_blob_path(pathname)

    if blob_mock_enabled():
        return "https://example.com/mock"

    if _storage_backend() != "s3":
        return f"/api/files/local?key={quote(normalized_pathname)}"

    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{quote(normalized_pathname, safe='/')}"

    try:
        return await asyncio.to_thread(
            _get_s3_client().generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": normalized_pathname},
            ExpiresIn=expires_seconds,
        )
    except BlobConfigError:
        raise
    except Exception as exc:
        logger.exception("Blob signed URL generation failed pathname=%s", normalized_pathname)
        raise BlobSignedUrlError(
            f"Blob signed URL generation failed pathname={normalized_pathname} error={exc}"
        ) from exc


async def get_signed_read_url(pathname: str, expires_seconds: int = 600) -> str:
    return await create_signed_blob_url(pathname, expires_seconds=expires_seconds)


normalize_blob_pathname = normalize_blob_path
