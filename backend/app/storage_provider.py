"""Pluggable object storage providers for uploaded files."""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from app.settings import settings
from app.storage import ensure_dir


class StorageProvider(Protocol):
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> dict[str, str]:
        """Persist bytes and return storage metadata."""

    async def get_signed_url(self, key: str, expires_seconds: int = 3600) -> str:
        """Return a signed or directly accessible URL for a stored object."""


class LocalDiskProvider:
    """Stores objects under data_path/objects for local development."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = ensure_dir(base_dir)

    def _resolve(self, key: str) -> Path:
        clean_key = key.strip("/")
        destination = (self.base_dir / clean_key).resolve()
        if self.base_dir.resolve() not in destination.parents and destination != self.base_dir.resolve():
            raise ValueError("Invalid storage key")
        return destination

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> dict[str, str]:
        del content_type
        destination = self._resolve(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(destination.write_bytes, data)
        return {"key": key, "url": f"/api/files/local?key={quote(key)}"}

    async def get_signed_url(self, key: str, expires_seconds: int = 3600) -> str:
        del expires_seconds
        return f"/api/files/local?key={quote(key)}"

    def resolve_local_path(self, key: str) -> Path:
        return self._resolve(key)

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._resolve(key).read_bytes)


class S3Provider:
    """S3-compatible object storage provider."""

    def __init__(
        self,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str | None = None,
        region: str | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        import boto3

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> dict[str, str]:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        url = await self.get_signed_url(key)
        return {"key": key, "url": url}

    async def get_signed_url(self, key: str, expires_seconds: int = 3600) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/{quote(key)}"
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    async def get_bytes(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=key)
        body = response["Body"]
        return await asyncio.to_thread(body.read)


_provider: StorageProvider | None = None


def _create_provider() -> StorageProvider:
    backend = settings.storage_backend.lower().strip()
    if backend == "s3":
        if not settings.s3_bucket or not settings.s3_access_key_id or not settings.s3_secret_access_key:
            raise RuntimeError("S3 storage backend requires S3_BUCKET, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY")
        return S3Provider(
            bucket=settings.s3_bucket,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            public_base_url=settings.s3_public_base_url,
        )
    return LocalDiskProvider(settings.data_path / "objects")


def get_storage_provider() -> StorageProvider:
    global _provider
    if _provider is None:
        _provider = _create_provider()
    return _provider


def reset_storage_provider() -> None:
    global _provider
    _provider = None


async def get_storage_signed_url(key: str, expires_seconds: int = 3600) -> str:
    return await get_storage_provider().get_signed_url(key, expires_seconds=expires_seconds)


async def materialize_object_to_path(key: str, cache_dir: Path) -> Path:
    provider = get_storage_provider()
    suffix = Path(key).suffix
    target = ensure_dir(cache_dir) / f"{abs(hash(key))}{suffix}"
    if target.exists():
        return target

    if isinstance(provider, LocalDiskProvider):
        source = provider.resolve_local_path(key)
        if source.exists():
            return source

    if not hasattr(provider, "get_bytes"):
        raise RuntimeError("Configured storage provider does not support object reads")

    data = await provider.get_bytes(key)  # type: ignore[attr-defined]
    target.write_bytes(data)
    if not target.suffix:
        guessed = mimetypes.guess_extension("application/octet-stream") or ".bin"
        target = target.with_suffix(guessed)
        target.write_bytes(data)
    return target
