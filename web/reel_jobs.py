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

# Composite "count" contract — how many composite reels to cut (plan Behavior 1).
# Kept next to the target constants; mirrored by CFG.ui in web/index.html.
COMPOSITE_COUNT_DEFAULT = 1
COMPOSITE_COUNT_MIN = 1
COMPOSITE_COUNT_MAX = 12

# Metadata accepted for every target but never persisted/forwarded.
_METADATA_INPUT_KEYS = frozenset(
    {"client_request_id", "research_run_id", "source_research_run_id"}
)

# Target-specific allowed input keys (plan Behavior 2). Anything else under
# ``input`` is an authenticated UI-boundary hardening reject (unsupported_input_field).
TOPIC_ALLOWED_INPUT_KEYS = frozenset({"topic"}) | _METADATA_INPUT_KEYS
# URL mode carries a legacy duplicate ``source`` (compat-only; must equal ``url``).
COMPOSITE_URL_ALLOWED_INPUT_KEYS = (
    frozenset({"url", "source", "preset", "count"}) | _METADATA_INPUT_KEYS
)
COMPOSITE_FILE_ALLOWED_INPUT_KEYS = (
    frozenset({"source", "preset", "count"}) | _METADATA_INPUT_KEYS
)
TEXT_ALLOWED_INPUT_KEYS = frozenset({"text"}) | _METADATA_INPUT_KEYS

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


# Never forwarded to the control plane: identity fields, idempotency metadata,
# and research provenance references. Canonical ``cp_input``/``params`` are built
# from normalized locals below, so metadata can never leak through.
_CP_STRIP = FORBIDDEN_IDENTITY_FIELDS | {
    "client_request_id",
    "research_run_id",
    "source_research_run_id",
}


def _reject_unsupported_fields(raw_input: dict, allowed: frozenset[str]) -> None:
    """Reject any per-job field outside the target's allowed set (plan Behavior 2)."""
    for key in raw_input:
        if key not in allowed:
            raise BadRequest(f"unsupported input field: {key}", code="unsupported_input_field")


def _parse_composite_count(raw_input: dict) -> int:
    """Normalize the composite ``count`` to an int in ``1..12`` (plan Behavior 1).

    Missing → default. Accepts ``int`` (not ``bool``) and decimal-digit strings
    like ``"3"`` / ``" 3 "``. Rejects booleans, floats, fractional/non-numeric
    strings, and out-of-range values with ``invalid_count``.
    """
    if "count" not in raw_input:
        return COMPOSITE_COUNT_DEFAULT
    raw = raw_input["count"]
    value: int | None = None
    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    if value is None or value < COMPOSITE_COUNT_MIN or value > COMPOSITE_COUNT_MAX:
        raise BadRequest(
            f"count must be an integer in {COMPOSITE_COUNT_MIN}..{COMPOSITE_COUNT_MAX}",
            code="invalid_count",
        )
    return value


def _reject_legacy_source_mismatch(raw_input: dict, normalized_url: str) -> None:
    """URL mode accepts a legacy duplicate ``source`` only when it equals the
    normalized ``url``; a mismatch is ``invalid_source`` (plan Submit Canonicalization)."""
    raw_source = raw_input.get("source")
    if raw_source is None:
        return
    if not isinstance(raw_source, str) or raw_source.strip() != normalized_url:
        raise BadRequest("legacy source must equal url", code="invalid_source")


def _canonical_params(
    target: str,
    *,
    source_mode: str | None = None,
    preset: str | None = None,
    count: int | None = None,
) -> dict:
    """Exact persisted ``submission.params`` — built from normalized values only."""
    params: dict = {"target": target}
    if source_mode is not None:
        params["source_mode"] = source_mode
    if preset is not None:
        params["preset"] = preset
    if count is not None:
        params["count"] = count
    return params


def build_submission(
    target: str, body: dict, source_research_run_id: uuid.UUID | None = None
) -> ReelSubmission:
    """Validate + canonicalize a submit body into a ``ReelSubmission`` (plan §6).

    Raises ``BadRequest`` for unsupported target, invalid JSON shape, missing
    ``input``, empty topic, invalid URL, missing upload handle, unsupported
    per-job fields, invalid count, legacy source mismatch, or any forbidden
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
        _reject_unsupported_fields(raw_input, TOPIC_ALLOWED_INPUT_KEYS)
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
            params=_canonical_params(target),
            cp_input={"topic": topic},
        )

    if target in (TARGET_TEXT_REEL, TARGET_TEXT_CAROUSEL):
        # Create-from-research text branch (Plan 5, ISC-35). Forward the caller's
        # text VERBATIM — no trim beyond the non-empty check — so an edited document
        # rides through byte-exact. Provenance stays on the DB field, never in
        # cp_input (reasoners should not see ownership/provenance metadata).
        _reject_unsupported_fields(raw_input, TEXT_ALLOWED_INPUT_KEYS)
        text = raw_input.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BadRequest("text must be a non-empty string", code="invalid_text")
        return ReelSubmission(
            target=target,
            title=text.strip()[:TITLE_MAX],
            source_url=None,
            topic=None,
            source_research_run_id=source_research_run_id,
            params=_canonical_params(target),
            cp_input={"text": text},
        )

    if target == TARGET_COMPOSITE:
        preset = raw_input.get("preset")
        if not isinstance(preset, str) or not preset.strip():
            raise BadRequest("preset must be a non-empty string", code="invalid_preset")
        preset = preset.strip()

        raw_url = raw_input.get("url")
        if raw_url is not None:
            # URL mode.
            _reject_unsupported_fields(raw_input, COMPOSITE_URL_ALLOWED_INPUT_KEYS)
            if not isinstance(raw_url, str) or not _is_valid_url(raw_url.strip()):
                raise BadRequest("url must be a valid http(s) URL", code="invalid_url")
            normalized = raw_url.strip()
            _reject_legacy_source_mismatch(raw_input, normalized)
            count = _parse_composite_count(raw_input)
            return ReelSubmission(
                target=target,
                title=preset[:TITLE_MAX],
                source_url=normalized,
                topic=None,
                source_research_run_id=source_research_run_id,
                params=_canonical_params(target, source_mode="url", preset=preset, count=count),
                cp_input={"url": normalized, "preset": preset, "count": count},
            )

        # File mode.
        _reject_unsupported_fields(raw_input, COMPOSITE_FILE_ALLOWED_INPUT_KEYS)
        handle = raw_input.get("source")
        if not isinstance(handle, str) or not handle.strip():
            raise BadRequest("file submit requires an upload handle", code="missing_source")
        handle = handle.strip()
        count = _parse_composite_count(raw_input)
        return ReelSubmission(
            target=target,
            title=preset[:TITLE_MAX],
            source_url=None,
            topic=None,
            source_research_run_id=source_research_run_id,
            params=_canonical_params(target, source_mode="file", preset=preset, count=count),
            cp_input={"source": handle, "preset": preset, "count": count},
            source_handle=handle,
        )

    # Unreachable: target is allowlisted above. Guard against a future target
    # slipping past the branches without a canonicalizer.
    raise BadRequest(f"unsupported target: {target}", code="unsupported_target")


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
