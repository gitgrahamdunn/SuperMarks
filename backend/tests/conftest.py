from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _reset_storage_provider() -> None:
    from app.storage_provider import reset_storage_provider

    reset_storage_provider()
    yield
    reset_storage_provider()
