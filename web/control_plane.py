"""Control-plane port: the ONLY place that talks to the AgentField control plane.

Server-to-server client. It builds its own request headers (never forwards the
browser's ``Cookie``/``Authorization``/``Origin``/session headers) and injects
``X-API-Key`` server-side so the key never reaches the browser. Transport errors,
timeouts, and invalid success bodies fail closed as 502 (plan §9 / B10).
"""

from __future__ import annotations

import os

import requests
from deps import BadGateway

_HOP_BY_HOP = {"content-encoding", "content-length", "transfer-encoding", "connection"}


class HttpControlPlane:
    def __init__(self) -> None:
        self._base = os.getenv(
            "AGENTFIELD_SERVER", "http://control-plane.railway.internal:8080"
        ).rstrip("/")
        self._api_key = os.getenv("AGENTFIELD_API_KEY", "")
        self._timeout = float(os.getenv("PROXY_TIMEOUT_S", "120"))

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    def dispatch_async(self, target: str, body: dict) -> tuple[int, dict, dict]:
        return self._request("POST", f"/api/v1/execute/async/{target}", json=body)

    def get_execution(self, execution_id: str) -> tuple[int, dict, dict]:
        return self._request("GET", f"/api/v1/executions/{execution_id}")

    def _request(self, method: str, path: str, *, json: dict | None = None):
        try:
            resp = requests.request(
                method, f"{self._base}{path}", json=json,
                headers=self._headers(), timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise BadGateway(f"control plane transport error: {exc}") from exc
        out_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        try:
            payload = resp.json() if resp.content else {}
        except ValueError as exc:
            raise BadGateway(f"control plane returned invalid JSON ({resp.status_code})") from exc
        return resp.status_code, payload, out_headers
