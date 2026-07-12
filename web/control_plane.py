"""Control-plane port: the ONLY place that talks to the AgentField control plane.

Server-to-server client. It builds its own request headers (never forwards the
browser's ``Cookie``/``Authorization``/``Origin``/session headers) and injects
``X-API-Key`` server-side so the key never reaches the browser. Transport errors,
timeouts, and invalid success bodies fail closed as 502 (plan §9 / B10).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import requests
from deps import BadGateway

_HOP_BY_HOP = {"content-encoding", "content-length", "transfer-encoding", "connection"}

# ─────────────────────────── by-reference body fetch (INT-02 B3) ───────────────────────────
# The research.completed event carries a SMALL owner DTO only (C-Notification); reel-af fetches
# the document BY REFERENCE from the execution ``result`` on demand. The INT-02 result contract
# (per the governing plan + the cross-app handoff spec) places the document under
# ``result.research_package`` and the prompt under ``result.metadata.query`` — NOT the legacy
# request-poll shape (result.markdown/html/sources). reel-af reads ``result`` only and NEVER
# ``notes`` (owner-scoped; a non-owner read returns ``execution_ownership_mismatch`` — ANTI A2).
# Keys are named constants, never magic strings.
RESULT_KEY = "result"
RESEARCH_PACKAGE_KEY = "research_package"
METADATA_KEY = "metadata"
METADATA_QUERY_KEY = "query"
EXECUTION_ID_KEY = "execution_id"


@dataclass(frozen=True)
class ResearchDocument:
    """The by-reference research OUTPUT snapshot. ``package_present`` distinguishes a
    genuinely-empty document from a MALFORMED one whose ``research_package`` was absent
    (flagged loud, still stampable — provenance is by execution_id, not by the package)."""

    research_package: object | None
    research_prompt: str | None
    document_id: str | None
    package_present: bool


def fetch_document_by_ref(
    execution: Mapping, *, dto_prompt: str | None = None
) -> ResearchDocument:
    """Read the research document + prompt from an execution ``result`` by reference.

    ``execution`` is the control-plane execution body (already fetched via ``get_execution``).
    Reads ``result.research_package`` (document), ``result.metadata.query`` (prompt, with a
    fallback to the event DTO's ``research_prompt``), and ``execution_id`` (the document id is
    the execution id; no id is minted). Touches ``result`` and ``execution_id`` ONLY — never
    ``notes`` (ANTI A2)."""
    result = execution.get(RESULT_KEY) or {}
    metadata = result.get(METADATA_KEY) or {}
    package_present = RESEARCH_PACKAGE_KEY in result
    research_package = result.get(RESEARCH_PACKAGE_KEY)
    research_prompt = metadata.get(METADATA_QUERY_KEY)
    if research_prompt is None:
        research_prompt = dto_prompt                 # small-DTO snapshot fallback (C-Notification)
    return ResearchDocument(
        research_package=research_package,
        research_prompt=research_prompt,
        document_id=execution.get(EXECUTION_ID_KEY),
        package_present=package_present,
    )


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
