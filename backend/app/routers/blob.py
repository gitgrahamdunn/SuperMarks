from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.blob_service import create_signed_blob_url

router = APIRouter(prefix="/blob", tags=["blob"])


class SignedUrlRequest(BaseModel):
    pathname: str = Field(min_length=1)


class SignedUrlResponse(BaseModel):
    url: str


def _run_async(coro):
    return asyncio.run(coro)


@router.post("/signed-url", response_model=SignedUrlResponse)
def get_signed_url(payload: SignedUrlRequest) -> SignedUrlResponse:
    try:
        url = _run_async(create_signed_blob_url(payload.pathname))
        return SignedUrlResponse(url=url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Blob signed URL failed") from exc
