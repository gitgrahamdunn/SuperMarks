"""Object storage upload helpers backed by local disk or S3-compatible storage."""

from __future__ import annotations

import asyncio
import os
import logging
from pathlib import Path
from urllib.parse import quote

import boto3

from app.settings import settings


logger = logging.getLogger(__name__)

class BlobUploadError(RuntimeError):
    """Raised when blob upload fails."""

def _is_mock_mode() -> bool:
    return os.getenv("BLOB_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def _storage_backend() -> str:
    return settings.storage_backend.lower().strip()


def _stable_url(pathname: str) -> str:
    normalized = pathname.lstrip("/")
    if _storage_backend() == "s3" and settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{quote(normalized, safe='/')}"
    if _storage_backend() != "s3":
        return f"/api/files/local?key={quote(normalized)}"
    return normalized


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
        raise BlobUploadError(
            "Missing storage configuration: " + ", ".join(missing)
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )

def _mock_upload(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    base = "https://blob.mock.local"
    url = f"{base}/{pathname.lstrip('/')}"
    target = settings.data_path / "objects" / pathname.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "url": url,
        "pathname": pathname,
        "contentType": content_type,
        "downloadUrl": url,
    }

def _upload_with_s3(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    client = _get_s3_client()
    try:
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=pathname,
            Body=data,
            ContentType=content_type,
        )
    except Exception as exc:
        logger.exception("S3 upload failed pathname=%s", pathname)
        raise BlobUploadError(f"Object storage upload failed: {exc}") from exc

    stable_url = _stable_url(pathname)
    download_url = stable_url
    if stable_url == pathname:
        try:
            download_url = asyncio.run(
                asyncio.to_thread(
                    client.generate_presigned_url,
                    "get_object",
                    Params={"Bucket": settings.s3_bucket, "Key": pathname},
                    ExpiresIn=3600,
                )
            )
        except Exception:
            download_url = pathname

    return {
        "url": stable_url,
        "pathname": pathname,
        "contentType": content_type,
        "downloadUrl": download_url,
    }

def upload_bytes(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    normalized = pathname.lstrip("/")
    if _is_mock_mode():
        return _mock_upload(normalized, data, content_type)

    if _storage_backend() == "s3":
        return _upload_with_s3(normalized, data, content_type)

    target = settings.data_path / "objects" / normalized
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    url = _stable_url(normalized)
    return {
        "url": url,
        "pathname": normalized,
        "contentType": content_type,
        "downloadUrl": url,
    }


def upload_rendered_key_page(exam_id: int, page_number: int, local_png_path: Path) -> dict[str, str]:
    """Upload a normalized key page PNG to durable blob storage."""
    pathname = f"exams/{exam_id}/key-pages/page_{page_number:04d}.png"
    return upload_bytes(pathname=pathname, data=local_png_path.read_bytes(), content_type="image/png")
