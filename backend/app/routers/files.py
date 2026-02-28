"""File access endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.storage_provider import LocalDiskProvider, get_storage_provider

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/local")
def get_local_file(key: str = Query(...)) -> FileResponse:
    provider = get_storage_provider()
    if not isinstance(provider, LocalDiskProvider):
        raise HTTPException(status_code=400, detail="Local file endpoint is only available with local storage backend")

    path = provider.resolve_local_path(key)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path)
