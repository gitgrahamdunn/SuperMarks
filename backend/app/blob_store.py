"""Vercel Blob upload helpers with SDK + HTTP fallback."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

class BlobUploadError(RuntimeError):
    """Raised when blob upload fails."""

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

def upload_bytes(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
    if _is_mock_mode():
        return _mock_upload(pathname, data, content_type)

    sdk_result = _upload_with_sdk(pathname, data, content_type)
    if sdk_result is not None:
        return sdk_result

    return _upload_with_http(pathname, data, content_type)


def upload_rendered_key_page(exam_id: int, page_number: int, local_png_path: Path) -> dict[str, str]:
    """Upload a normalized key page PNG to durable blob storage."""
    pathname = f"exams/{exam_id}/key-pages/page_{page_number:04d}.png"
    return upload_bytes(pathname=pathname, data=local_png_path.read_bytes(), content_type="image/png")
