from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx


VERCEL_BLOB_BASE = "https://blob.vercel-storage.com"


def blob_mock_enabled() -> bool:
    return os.getenv("BLOB_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def get_blob_token() -> str:
    if blob_mock_enabled():
        return "mock-token"
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")
    return token


async def create_signed_blob_url(pathname: str, expires_seconds: int = 600) -> str:
    if blob_mock_enabled():
        return "https://example.com/fake"

    token = get_blob_token()
    expires_at = int((datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)).timestamp())
    endpoint = f"{VERCEL_BLOB_BASE}/v1/sign/{quote(pathname, safe='/-_.~')}"

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.put(
            endpoint,
            json={"expiresAt": expires_at, "allowWrite": False},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("url"):
        raise RuntimeError("Invalid signed URL response from Vercel Blob")
    return str(payload["url"])


async def download_blob_bytes(pathname: str) -> bytes:
    if blob_mock_enabled():
        return b"%PDF-1.4\n%mock blob content\n"

    token = get_blob_token()
    endpoint = f"{VERCEL_BLOB_BASE}/{quote(pathname, safe='/-_.~')}"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    return response.content
