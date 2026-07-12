"""Owner-interface reader for deep-research run detail (INT Phase 0, Behavior 3).

reel-af does NOT own the owner's research_run table — the deep-research node does
(ARCHITECTURE §11). Where reel-af needs run detail it reads it through the OWNER's
interface, keyed by ``execution_id`` (API Composition), reusing the identity-free
control-plane client. This module issues no SQL and never touches the owner tables
directly (ISC-4).
"""

from __future__ import annotations

from deps import BadGateway, NotFound

HTTP_NOT_FOUND = 404
HTTP_ERROR_FLOOR = 400


class OwnerInterfaceResearchRunReader:
    """Reads deep-research run detail via the owner's control-plane interface,
    keyed by ``execution_id``. Fail-closed: a missing run 404s, any other non-2xx
    or malformed body raises ``BadGateway`` — it never synthesizes a row and never
    falls through to a local owner-table read."""

    def __init__(self, control_plane):
        self._cp = control_plane

    def read(self, ctx, execution_id: str) -> dict:
        # execution_id is the ONLY key (master §4) — identity-free client (no Cookie/Authorization).
        status, body, _headers = self._cp.get_execution(execution_id)
        if status == HTTP_NOT_FOUND:
            raise NotFound("research run not found")
        if status >= HTTP_ERROR_FLOOR or not isinstance(body, dict):
            raise BadGateway("owner interface unavailable")
        return body                                       # foreign data by-id; never a local owner-table read
