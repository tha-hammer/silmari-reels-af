"""Reel-job domain types: submission validation, target map, status mapping.

Pure logic only — no DB here (the psycopg adapter implementing ``ReelJobRepoPort``
comes with the Postgres contract-test phase). This module owns the request→row
mapping (plan §6) and the CP→DB status normalization (plan §B12).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from deps import BadRequest

ReelJobStatus = Literal["queued", "producing", "succeeded", "failed", "cancelled"]

# Named target constants (plan §6) — no open-ended ``reel-af.*`` match.
TARGET_TOPIC = "reel-af.reel_topic_to_reel"
TARGET_COMPOSITE = "reel-af.reel_composite_to_reel"
TARGET_ARTICLE = "reel-af.reel_article_to_reel"  # future; not visible/allowlisted yet

# Cross-node deep-research target (Plan 4, ISC-22). Byte-exact node.reasoner —
# dispatched via a dedicated /api/v1/research/run route, NOT the reel allowlist.
TARGET_RESEARCH = "meta_deep_research.execute_deep_research"

# Create-from-research text targets (Plan 5, ISC-30/35). Plan 1 owns the reasoners;
# Plan 5 owns only the allowlist entry + text submission shape + the output→target map.
TARGET_TEXT_REEL = "reel-af.reel_research_to_reel"          # text → video
TARGET_TEXT_CAROUSEL = "reel-af.reel_research_to_carousel"  # text → carousel
TEXT_TARGET_BY_OUTPUT = {"video": TARGET_TEXT_REEL, "carousel": TARGET_TEXT_CAROUSEL}

_RESEARCH_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "research_defaults.json")


def _load_research_defaults() -> dict:
    """Load the one-click research defaults mirror (web/research_defaults.json).

    Drops documentation-only keys (``_``-prefixed). Never carries ``query``
    (caller-supplied), ``model`` (blank => DR server default), or ``api_key``
    (secret-shaped, never mirrored). See the JSON ``_comment`` for the contract.
    """
    with open(_RESEARCH_DEFAULTS_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# The 9 non-secret one-click defaults merged under every research dispatch.
RESEARCH_DEFAULTS = _load_research_defaults()

# Only targets with a visible preset in web/index.html are allowlisted (plan §1).
# Plan 5 adds the two text targets used by the create-from-research fan-out.
ALLOWLISTED_TARGETS = frozenset(
    {TARGET_TOPIC, TARGET_COMPOSITE, TARGET_TEXT_REEL, TARGET_TEXT_CAROUSEL}
)

TITLE_MAX = 120

# Forbidden at top level AND under ``input`` (plan §6). Client can never supply
# ownership/identity — it is always server-derived.
FORBIDDEN_IDENTITY_FIELDS = frozenset(
    {
        "org_id",
        "orgId",
        "created_by",
        "createdBy",
        "user_id",
        "userId",
        "membership",
        "role",
        "active_org_id",
        "activeOrgId",
    }
)


@dataclass(frozen=True)
class ReelSubmission:
    target: str
    title: str
    source_url: str | None
    topic: str | None
    source_research_run_id: uuid.UUID | None
    params: dict
    cp_input: dict  # canonical, identity-free input dispatched to the control plane
    source_handle: str | None = None  # file-mode upload key; presigned to a URL at dispatch (T7)


@dataclass(frozen=True)
class ReelJobRef:
    job_id: uuid.UUID
    org_id: uuid.UUID
    created_by: uuid.UUID
    status: ReelJobStatus
    execution_id: str | None = None
    result_ref: str | None = None
    completed_at: datetime | None = None
    params: dict = field(default_factory=dict)
    source_research_run_id: uuid.UUID | None = None  # provenance (Plan 4, ISC-25)
    created: bool = True  # False when insert_or_get_queued returned an existing row (idempotency)


@dataclass(frozen=True)
class ResearchRunRef:
    """A recorded cross-node research run (Plan 4, ISC-24). Carries ``execution_id``
    so the poll path (``get_research_by_execution``) can resolve a run by it."""

    id: uuid.UUID
    org_id: uuid.UUID
    created_by: uuid.UUID
    status: str
    execution_id: str | None = None


def _reject_forbidden_identity(payload: dict) -> None:
    for key in payload:
        if key in FORBIDDEN_IDENTITY_FIELDS:
            raise BadRequest(f"forbidden identity field: {key}", code="forbidden_field")


def _is_valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except (ValueError, AttributeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# Never forwarded to the control plane: identity fields + the idempotency key
# (the key is ownership/dedup metadata, not reasoner input; plan B0.4) + the
# research provenance reference under BOTH its wire-key and DB-column names
# (a reference for ownership stamping, never reasoner input; Plan 4 cross-plan lock).
_CP_STRIP = FORBIDDEN_IDENTITY_FIELDS | {
    "client_request_id",
    "research_run_id",
    "source_research_run_id",
}


def _clean_input(raw_input: dict) -> dict:
    """Strip forbidden identity fields + idempotency key; the base CP-bound input."""
    return {k: v for k, v in raw_input.items() if k not in _CP_STRIP}


def _sanitized_params(raw_input: dict, target: str, preset: str | None) -> dict:
    params = _clean_input(raw_input)
    params["target"] = target
    if preset is not None:
        params["preset"] = preset
    return params


def build_submission(
    target: str, body: dict, source_research_run_id: uuid.UUID | None = None
) -> ReelSubmission:
    """Validate + canonicalize a submit body into a ``ReelSubmission`` (plan §6).

    Raises ``BadRequest`` for unsupported target, invalid JSON shape, missing
    ``input``, empty topic, invalid URL, missing upload handle, or any forbidden
    identity field at top level or under ``input``.

    ``source_research_run_id`` is the caller's research provenance reference,
    already **validated to belong to the caller's org** by the route (Plan 4,
    ISC-25). It is a *reference*, never trusted for ownership.
    """
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object", code="invalid_json")
    _reject_forbidden_identity(body)

    if target not in ALLOWLISTED_TARGETS:
        raise BadRequest(f"unsupported target: {target}", code="unsupported_target")

    raw_input = body.get("input")
    if not isinstance(raw_input, dict):
        raise BadRequest("missing 'input' object", code="missing_input")
    _reject_forbidden_identity(raw_input)

    if target == TARGET_TOPIC:
        topic = raw_input.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            raise BadRequest("topic must be a non-empty string", code="invalid_topic")
        topic = topic.strip()
        return ReelSubmission(
            target=target,
            title=topic[:TITLE_MAX],
            source_url=None,
            topic=topic,
            source_research_run_id=source_research_run_id,
            params=_sanitized_params(raw_input, target, raw_input.get("preset")),
            cp_input={**_clean_input(raw_input), "topic": topic},
        )

    if target in (TARGET_TEXT_REEL, TARGET_TEXT_CAROUSEL):
        # Create-from-research text branch (Plan 5, ISC-35). Forward the caller's
        # text VERBATIM — no trim beyond the non-empty check — so an edited document
        # rides through byte-exact. Provenance stays on the DB field, stripped from
        # cp_input via _CP_STRIP (never seen by the reasoner).
        text = raw_input.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BadRequest("text must be a non-empty string", code="invalid_text")
        return ReelSubmission(
            target=target,
            title=text.strip()[:TITLE_MAX],
            source_url=None,
            topic=None,
            source_research_run_id=source_research_run_id,
            params=_sanitized_params(raw_input, target, None),
            cp_input={**_clean_input(raw_input), "text": text},
        )

    # TARGET_COMPOSITE — URL mode (has url) or file mode (has source handle).
    preset = raw_input.get("preset")
    if not isinstance(preset, str) or not preset.strip():
        raise BadRequest("preset must be a non-empty string", code="invalid_preset")
    preset = preset.strip()

    raw_url = raw_input.get("url")
    if raw_url is not None:
        if not isinstance(raw_url, str) or not _is_valid_url(raw_url.strip()):
            raise BadRequest("url must be a valid http(s) URL", code="invalid_url")
        normalized = raw_url.strip()
        host = urlparse(normalized).netloc
        return ReelSubmission(
            target=target,
            title=(preset or host)[:TITLE_MAX],
            source_url=normalized,
            topic=None,
            source_research_run_id=source_research_run_id,
            params=_sanitized_params(raw_input, target, preset),
            cp_input={**_clean_input(raw_input), "url": normalized},
        )

    handle = raw_input.get("source")
    if not isinstance(handle, str) or not handle.strip():
        raise BadRequest("file submit requires an upload handle", code="missing_source")
    handle = handle.strip()
    return ReelSubmission(
        target=target,
        title=preset[:TITLE_MAX],
        source_url=None,
        topic=None,
        source_research_run_id=source_research_run_id,
        params=_sanitized_params(raw_input, target, preset),
        cp_input=_clean_input(raw_input),
        source_handle=handle,
    )


def build_research_dispatch(raw_input: dict | None) -> tuple[str, dict]:
    """Build the identity-free control-plane dispatch for a research run (ISC-22).

    Merges the one-click ``RESEARCH_DEFAULTS`` under the caller's ``query`` (and an
    optional ``mode`` override), rejects forbidden identity fields (reusing the same
    gate as ``build_submission``), and returns ``(TARGET_RESEARCH, {"input": ...})``.

    Raises ``BadRequest`` for an empty/whitespace query or any forbidden identity
    field. Unknown keys are dropped — only ``query`` + defaults (+ ``mode``) are sent.
    """
    body = raw_input or {}
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object", code="invalid_json")
    _reject_forbidden_identity(body)

    query = str(body.get("query", "")).strip()
    if not query:
        raise BadRequest("query is required", code="invalid_query")

    payload = {**RESEARCH_DEFAULTS, "query": query}
    mode = body.get("mode")
    if isinstance(mode, str) and mode.strip():
        payload["mode"] = mode.strip()
    return TARGET_RESEARCH, {"input": payload}


# ─────────────────────────── status normalization (plan §B12) ───────────────────────────

_STATUS_MAP: dict[str, ReelJobStatus] = {}
for _fam, _members in {
    "queued": ("queued", "pending", "registered", "submitted"),
    "producing": (
        "running", "processing", "waiting", "paused",
        "ingesting", "transcribing", "rendering", "compositing",
    ),
    "succeeded": ("succeeded", "success", "completed", "complete", "done", "ok"),
    "failed": ("failed", "error", "failure", "errored", "timeout", "timed_out", "unknown"),
    "cancelled": ("cancelled", "canceled", "cancel"),
}.items():
    for _m in _members:
        _STATUS_MAP[_m] = _fam  # type: ignore[assignment]

TERMINAL_STATUSES: frozenset[ReelJobStatus] = frozenset({"succeeded", "failed", "cancelled"})


def normalize_reel_status(cp_status: str | None) -> ReelJobStatus:
    """Map any CP status family to a DB status. Unknown/unparseable → 'failed'."""
    if not isinstance(cp_status, str):
        return "failed"
    return _STATUS_MAP.get(cp_status.strip().lower(), "failed")
