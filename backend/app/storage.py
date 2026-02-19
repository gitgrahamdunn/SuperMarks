"""Filesystem storage utilities."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from app.settings import settings


def ensure_dir(path: Path) -> Path:
    """Create directory if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def reset_dir(path: Path) -> Path:
    """Delete directory if present then recreate it."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def upload_dir(exam_id: int, submission_id: int) -> Path:
    return ensure_dir(settings.data_path / "uploads" / str(exam_id) / str(submission_id))


def pages_dir(exam_id: int, submission_id: int) -> Path:
    return settings.data_path / "pages" / str(exam_id) / str(submission_id)


def crops_dir(exam_id: int, submission_id: int) -> Path:
    return settings.data_path / "crops" / str(exam_id) / str(submission_id)


def save_upload_file(upload: UploadFile, destination: Path) -> None:
    """Persist uploaded file to destination path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)


def relative_to_data(path: Path) -> str:
    """Return path string relative to configured data directory when possible."""
    try:
        return str(path.relative_to(settings.data_path))
    except ValueError:
        return str(path)
