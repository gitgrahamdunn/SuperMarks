from __future__ import annotations

import os

from app.blob_store import download_blob_bytes, get_signed_read_url


class BlobConfigError(RuntimeError):
    """Raised when required blob configuration is missing."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("Blob not configured")


def blob_mock_enabled() -> bool:
    return os.getenv("BLOB_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def get_blob_token() -> str:
    if blob_mock_enabled():
        return "mock-token"
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise BlobConfigError(["BLOB_READ_WRITE_TOKEN"])
    return token


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    return await get_signed_read_url(pathname, expires_seconds=expires_seconds)
