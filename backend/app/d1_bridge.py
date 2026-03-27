"""HTTP bridge client for Worker-bound D1 access.

The current hosted runtime places D1 bindings on the Worker layer while the
Python API runs inside a Cloudflare container. This client is the container-side
path for reaching that Worker-owned D1 binding over an authenticated internal
HTTP bridge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import settings

_BRIDGE_HEADER = "x-supermarks-bridge-token"


class D1BridgeError(RuntimeError):
    """Raised when the D1 bridge returns an error or is unreachable."""


@dataclass(frozen=True)
class D1Statement:
    sql: str
    params: list[Any] | None = None


class D1BridgeClient:
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "user-agent": "SuperMarksBackend/1.0",
                _BRIDGE_HEADER: self.token,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise D1BridgeError(f"D1 bridge HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise D1BridgeError(f"D1 bridge request failed: {exc.reason}") from exc

    def health(self) -> dict[str, Any]:
        return self._post("/health", {})

    def query_all(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        response = self._post("/query", {"sql": sql, "params": params or [], "result_mode": "all"})
        return list(response.get("rows") or [])

    def query_first(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        response = self._post("/query", {"sql": sql, "params": params or [], "result_mode": "first"})
        row = response.get("row")
        return row if isinstance(row, dict) else None

    def run(self, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        return self._post("/run", {"sql": sql, "params": params or []})

    def batch(self, statements: list[D1Statement]) -> list[dict[str, Any]]:
        return list(
            self._post(
                "/batch",
                {
                    "statements": [
                        {"sql": statement.sql, "params": statement.params or []}
                        for statement in statements
                    ]
                },
            ).get("results")
            or []
        )

    def exec_raw(self, sql: str) -> dict[str, Any]:
        return self._post("/exec", {"sql": sql})


def get_d1_bridge_client() -> D1BridgeClient:
    if not settings.has_d1_bridge:
        raise D1BridgeError("D1 bridge is not configured. Set SUPERMARKS_D1_BRIDGE_URL and SUPERMARKS_D1_BRIDGE_TOKEN.")
    return D1BridgeClient(
        base_url=str(settings.d1_bridge_url or "").rstrip("/"),
        token=str(settings.d1_bridge_token or ""),
        timeout_seconds=float(settings.d1_bridge_timeout_seconds),
    )
