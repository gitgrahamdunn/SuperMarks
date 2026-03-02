from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.blob_service import create_signed_blob_url, get_blob_token

router = APIRouter(prefix="/blob", tags=["blob"])


class BlobTokenResponse(BaseModel):
    token: str


class SignedUrlRequest(BaseModel):
    pathname: str = Field(min_length=1)


class SignedUrlResponse(BaseModel):
    url: str


def _run_async(coro):
    return asyncio.run(coro)


@router.post("/upload-token", response_model=BlobTokenResponse)
def get_upload_token() -> BlobTokenResponse:
    try:
        return BlobTokenResponse(token=get_blob_token())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/signed-url", response_model=SignedUrlResponse)
def get_signed_url(payload: SignedUrlRequest) -> SignedUrlResponse:
    try:
        url = _run_async(create_signed_blob_url(payload.pathname))
        return SignedUrlResponse(url=url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to create signed URL") from exc
