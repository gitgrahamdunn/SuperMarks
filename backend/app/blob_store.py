"""Vercel Blob upload helpers with SDK + HTTP fallback."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx


class BlobUploadError(RuntimeError):
    """Raised when blob upload fails."""


class BlobSignedUrlError(RuntimeError):
    """Raised when a signed read URL cannot be created."""


class BlobDownloadError(RuntimeError):
    """Raised when blob bytes cannot be downloaded."""


def _blob_access_value() -> str:
    return os.getenv("BLOB_PUBLIC_ACCESS", "public").strip() or "public"


def _require_blob_token() -> str:
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise BlobUploadError("Missing BLOB_READ_WRITE_TOKEN")
    return token


def _is_mock_mode() -> bool:
    return os.getenv("BLOB_MOCK", "").strip() == "1"


def _mock_upload(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    from app.settings import settings

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


def _upload_with_sdk(pathname: str, data: bytes, content_type: str) -> dict[str, str] | None:
    try:
        from vercel import blob as vercel_blob  # type: ignore
    except Exception:
        return None

    token = _require_blob_token()
    access = _blob_access_value()

    candidates = [
        getattr(vercel_blob, "put", None),
        getattr(vercel_blob, "upload", None),
        getattr(vercel_blob, "put_blob", None),
    ]
    uploader = next((fn for fn in candidates if callable(fn)), None)
    if uploader is None:
        return None

    try:
        result: Any = uploader(  # type: ignore[misc]
            pathname,
            data,
            {
                "access": access,
                "contentType": content_type,
                "token": token,
            },
        )
    except TypeError:
        result = uploader(pathname=pathname, data=data, access=access, content_type=content_type, token=token)  # type: ignore[misc]
    except Exception:
        return None

    if hasattr(result, "dict"):
        payload = result.dict()
    elif isinstance(result, dict):
        payload = result
    else:
        return None

    url = str(payload.get("url") or "").strip()
    returned_pathname = str(payload.get("pathname") or pathname)
    returned_content_type = str(payload.get("contentType") or content_type)
    download_url = str(payload.get("downloadUrl") or url)
    if not url:
        return None

    return {
        "url": url,
        "pathname": returned_pathname,
        "contentType": returned_content_type,
        "downloadUrl": download_url,
    }


def _upload_with_http(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    token = _require_blob_token()
    access = _blob_access_value()
    url = "https://blob.vercel-storage.com/"
    headers = {
        "Authorization": f"Bearer {token}",
        "x-content-type": content_type,
        "x-add-random-suffix": "0",
    }
    params = {"pathname": pathname, "access": access}

    response = httpx.put(url, content=data, headers=headers, params=params, timeout=60.0)
    if response.status_code >= 400:
        raise BlobUploadError(f"Blob upload failed ({response.status_code}): {response.text[:300]}")

    payload = response.json()
    blob_url = str(payload.get("url") or "").strip()
    if not blob_url:
        raise BlobUploadError("Blob upload succeeded but no URL was returned")

    return {
        "url": blob_url,
        "pathname": str(payload.get("pathname") or pathname),
        "contentType": str(payload.get("contentType") or content_type),
        "downloadUrl": str(payload.get("downloadUrl") or blob_url),
    }


async def get_signed_read_url(pathname: str, expires_seconds: int = 600) -> str:
    if _is_mock_mode():
        return "https://example.com/mock"

    try:
        token = _require_blob_token()
    except Exception as exc:
        raise BlobSignedUrlError("Blob signed URL failed") from exc
    expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)).timestamp())
    endpoint = f"https://blob.vercel-storage.com/v1/sign/{quote(pathname, safe='/-_.~')}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.put(
                endpoint,
                json={"expiresAt": expires_at, "allowWrite": False},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
    except Exception as exc:
        raise BlobSignedUrlError("Blob signed URL failed") from exc

    if response.status_code >= 400:
        raise BlobSignedUrlError("Blob signed URL failed")

    payload = response.json()
    signed_url = str(payload.get("url") or "").strip() if isinstance(payload, dict) else ""
    if not signed_url:
        raise BlobSignedUrlError("Blob signed URL failed")
    return signed_url


async def download_blob_bytes(pathname: str) -> tuple[bytes, str]:
    if _is_mock_mode():
        return (b"%PDF-1.4\n%mock blob content\n", "application/pdf")

    signed_url = await get_signed_read_url(pathname)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(signed_url)

    if response.status_code >= 400:
        raise BlobDownloadError(f"Blob download failed ({response.status_code}) for pathname {pathname}")

    return response.content, response.headers.get("content-type", "")


def upload_bytes(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    if _is_mock_mode():
        return _mock_upload(pathname, data, content_type)

    sdk_result = _upload_with_sdk(pathname, data, content_type)
    if sdk_result is not None:
        return sdk_result

    return _upload_with_http(pathname, data, content_type)
