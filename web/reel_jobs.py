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
from urllib.parse import parse_qs, urlparse

from deps import BadRequest
from tunables import validate_overrides

ReelJobStatus = Literal["queued", "producing", "succeeded", "failed", "cancelled"]

# Named target constants (plan §6) — no open-ended ``reel-af.*`` match.
TARGET_TOPIC = "reel-af.reel_topic_to_reel"
TARGET_COMPOSITE = "reel-af.reel_composite_to_reel"
TARGET_ARTICLE = "reel-af.reel_article_to_reel"  # future; not visible/allowlisted yet
# A1 DSL-hooks target. The reasoner is app.dsl_hooks_to_reels — the SDK derives
# the id as "<node>.<router prefix>_<func name>", so this string and that function
# name are coupled. Externally owned: do not rename without migration notes.
TARGET_DSL_HOOKS = "reel-af.reel_dsl_hooks_to_reels"
DSL_HOOKS_SOURCE_MODE = "dsl_hooks"
# A1 transcript-to-plan target (AF-a8o) — leg 1 of the browser A1 chain. Same
# SDK-derived naming coupling as TARGET_DSL_HOOKS (app.transcript_to_plan).
TARGET_TRANSCRIPT = "reel-af.reel_transcript_to_plan"
TRANSCRIPT_SOURCE_MODE = "a1_transcript"
# Query keys that mark the historical deterministic clip-plan article seed
# (plan_clips.py emitted `?t=<start>&reel_end=<end>`). Not a DSL-hooks input.
_ARTICLE_SEED_QUERY_KEYS = frozenset({"t", "reel_end"})
# Artifact refs must be opaque a1://-style or http(s) refs — never a filesystem
# path a worker could be coerced into reading.
_ARTIFACT_REF_SCHEMES = ("a1://", "http://", "https://")
DSL_HOOKS_CLIP_IDX_DEFAULT = 1

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
# AF-a8o adds TARGET_TRANSCRIPT: the a1 preset submits leg 1 directly and the
# browser chains leg 2 (TARGET_DSL_HOOKS) from its returned artifact refs.
ALLOWLISTED_TARGETS = frozenset(
    {
        TARGET_TOPIC,
        TARGET_COMPOSITE,
        TARGET_TEXT_REEL,
        TARGET_TEXT_CAROUSEL,
        TARGET_DSL_HOOKS,
        TARGET_TRANSCRIPT,
    }
)

# Where _resolve_cp_input injects the presigned file-mode upload URL. The
# composite reasoner's param is ``url``; both A1 reasoners take ``source_url``.
# Default (absent target) stays ``url`` so composite behavior is byte-identical.
PRESIGN_CP_KEY_BY_TARGET = {
    TARGET_TRANSCRIPT: "source_url",
    TARGET_DSL_HOOKS: "source_url",
}
PRESIGN_CP_KEY_DEFAULT = "url"

TITLE_MAX = 120

# ── Delivery-required policy (Slice A, B14) ────────────────────────
# Targets whose result MUST carry a browser-deliverable http(s) URL to count as
# delivered. Scoped to the DSL-hooks target ON PURPOSE: composite/topic/research
# keep today's fail-soft behavior (they succeed with only a node-local
# video_path), so this adds no regression to live targets.
DELIVERY_REQUIRED_TARGETS = frozenset({TARGET_DSL_HOOKS})

# An ERROR CODE, never a DB status. The terminal status set is hardcoded in SQL
# (pg.update_from_execution: `status not in ('succeeded','failed','cancelled')`),
# so a new status would need a root-owned schema change. Status stays "failed".
A1_DELIVERY_UNAVAILABLE = "delivery_unavailable"

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
# A1 DSL-hooks target (Slice A). Accepts ONLY the A1 artifact refs + source_url +
# clip_idx, plus explicitly-allowlisted finish render overrides. Article/topic/
# clip-plan shapes (topic/url/text/preset/count/source) are deliberately absent —
# the DSL-hooks target fails closed against them before any row or CP dispatch.
# ``source`` (AF-a8o) is the DROP-FILE alternative to ``source_url``: an
# org-owned upload handle, presigned server-side into ``source_url`` at dispatch.
DSL_HOOKS_ALLOWED_INPUT_KEYS = (
    frozenset(
        {"source_url", "source", "composite_ref", "words_ref", "hook_ref", "clip_idx", "overrides"}
    )
    | _METADATA_INPUT_KEYS
)
# A1 transcript-to-plan (AF-a8o, leg 1): exactly one of a public source_url or
# an org-owned upload handle. register/clip_count stay server-side defaults —
# the browser cannot tune the planner (follow-up bead if ever needed).
TRANSCRIPT_ALLOWED_INPUT_KEYS = frozenset({"source_url", "source"}) | _METADATA_INPUT_KEYS
# Finish/render overrides allowlisted for this workflow — a SUBSET of web
# tunables.TUNABLES (the single source of truth for override validation), so the
# two can never disagree about a key's type/bounds. `raw`/`fast` are deliberately
# absent: the A1 DSL hook workflow has NO raw opt-out (research: finish runs by
# default with hook banner, captions and cut-ins).
DSL_HOOKS_FINISH_OVERRIDE_KEYS = frozenset(
    {
        "font_scale",
        "box_opacity",
        "overlay_accent",
        "phrase_uppercase",
        "phrase_max_words",
        "accent_bar_px",
        "corner_radius",
    }
)
# URL mode carries a legacy duplicate ``source`` (compat-only; must equal ``url``).
# ``overrides`` is the per-job tuning object (plan Behavior 5) — composite-only;
# the topic set above deliberately excludes it (Behavior 6).
COMPOSITE_URL_ALLOWED_INPUT_KEYS = (
    frozenset({"url", "source", "preset", "count", "overrides"}) | _METADATA_INPUT_KEYS
)
COMPOSITE_FILE_ALLOWED_INPUT_KEYS = (
    frozenset({"source", "source_asset_id", "preset", "count", "overrides"})
    | _METADATA_INPUT_KEYS
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
    # AF-4pz.2: reuse of a persisted upload — resolved org-scoped to its stored
    # bucket key at dispatch (404 conceals foreign/absent), then presigned like
    # the handle path. A reference, never trusted for ownership.
    source_asset_id: uuid.UUID | None = None
    # AF-8bk: optional project membership — validated against the caller's org
    # by the route (mirrors source_research_run_id), stamped onto reel_job.
    project_id: uuid.UUID | None = None


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
    created_at: datetime | None = None  # AF-8bk: surfaced by list_for_project


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


def _reject_article_seed_url(url: str) -> None:
    """Guard: the historical `?t=&reel_end=` clip-plan seed is not a DSL-hooks input.

    Pure question then raise — no side effects in the condition.
    """

    query = parse_qs(urlparse(url).query)
    seed_keys = _ARTICLE_SEED_QUERY_KEYS & set(query)
    if seed_keys:
        raise BadRequest(
            f"source_url carries article-seed query keys {sorted(seed_keys)}; "
            f"the DSL hooks target takes an unscoped source URL",
            code="unsupported_input_field",
        )


def _validated_artifact_ref(raw_input: dict, key: str) -> str:
    """Guard: artifact refs are opaque owned refs, never client filesystem paths."""

    value = raw_input.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(f"{key} is required", code="missing_input")
    ref = value.strip()
    if not ref.startswith(_ARTIFACT_REF_SCHEMES):
        raise BadRequest(
            f"{key} must be an a1:// or http(s) ref, not a filesystem path",
            code="invalid_artifact_ref",
        )
    return ref


def _validated_source_asset_id(raw) -> uuid.UUID:
    """AF-4pz.2: a persisted-asset reference must be a UUID (reference only —
    ownership is checked org-scoped at resolve time, never trusted here)."""
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise BadRequest(
            "source_asset_id must be a UUID", code="invalid_source_asset_id"
        ) from exc


def _validated_upload_handle(raw_handle) -> str:
    """Guard: a file-mode upload handle is a non-empty string (bool is not str)."""
    if not isinstance(raw_handle, str) or not raw_handle.strip():
        raise BadRequest("source must be a non-empty upload handle", code="invalid_source")
    return raw_handle.strip()


def _reject_both_sources(raw_input: dict) -> None:
    """Guard: URL mode and FILE mode are exclusive (explicit ``null`` means absent)."""
    if raw_input.get("source_url") is not None and raw_input.get("source") is not None:
        raise BadRequest(
            "provide either source_url or source, not both", code="invalid_source"
        )


def _validated_a1_source_url(raw_url) -> str:
    """Guard: A1 source URLs are unscoped http(s) — no article-seed query keys."""
    if not isinstance(raw_url, str) or not _is_valid_url(raw_url.strip()):
        raise BadRequest("source_url must be a non-empty http(s) URL", code="invalid_url")
    source_url = raw_url.strip()
    _reject_article_seed_url(source_url)
    return source_url


def _parse_clip_idx(raw_input: dict) -> int:
    value = raw_input.get("clip_idx", DSL_HOOKS_CLIP_IDX_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BadRequest("clip_idx must be an integer >= 1", code="invalid_clip_idx")
    return value


def _validated_dsl_hooks_overrides(raw_overrides) -> dict:
    """Finish overrides allowlisted for this workflow (no raw/fast opt-out)."""

    if raw_overrides is None:
        return {}
    if not isinstance(raw_overrides, dict):
        raise BadRequest("overrides must be an object", code="invalid_override")
    unknown = sorted(set(raw_overrides) - DSL_HOOKS_FINISH_OVERRIDE_KEYS)
    if unknown:
        raise BadRequest(
            f"unsupported override field(s): {unknown}", code="unsupported_override_field"
        )
    return validate_overrides(raw_overrides)


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
    overrides: dict | None = None,
    source_asset_id: uuid.UUID | None = None,
) -> dict:
    """Exact persisted ``submission.params`` — built from normalized values only.

    ``overrides`` is recorded only when non-empty so an un-tuned submit is
    byte-identical to before (plan Behavior 6). ``source_asset_id`` is recorded
    only for asset-mode submits (AF-4pz.2 audit trail)."""
    params: dict = {"target": target}
    if source_mode is not None:
        params["source_mode"] = source_mode
    if preset is not None:
        params["preset"] = preset
    if count is not None:
        params["count"] = count
    if overrides:
        params["overrides"] = overrides
    if source_asset_id is not None:
        params["source_asset_id"] = str(source_asset_id)
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
            overrides = validate_overrides(raw_input.get("overrides"))
            cp_input = {"url": normalized, "preset": preset, "count": count}
            if overrides:
                cp_input["overrides"] = overrides
            return ReelSubmission(
                target=target,
                title=preset[:TITLE_MAX],
                source_url=normalized,
                topic=None,
                source_research_run_id=source_research_run_id,
                params=_canonical_params(
                    target, source_mode="url", preset=preset, count=count, overrides=overrides
                ),
                cp_input=cp_input,
            )

        # File mode — a fresh upload handle OR a persisted source_asset ref
        # (AF-4pz.2: one upload feeds many reels; mutually exclusive).
        _reject_unsupported_fields(raw_input, COMPOSITE_FILE_ALLOWED_INPUT_KEYS)
        handle = raw_input.get("source")
        raw_asset_id = raw_input.get("source_asset_id")
        if handle is not None and raw_asset_id is not None:
            raise BadRequest(
                "provide either 'source' or 'source_asset_id', not both",
                code="conflicting_sources",
            )
        count = _parse_composite_count(raw_input)
        overrides = validate_overrides(raw_input.get("overrides"))

        if raw_asset_id is not None:
            # Asset mode: the stored bucket key is resolved org-scoped and
            # presigned at dispatch (_resolve_cp_input) — never client-supplied.
            source_asset_id = _validated_source_asset_id(raw_asset_id)
            cp_input = {"preset": preset, "count": count}
            if overrides:
                cp_input["overrides"] = overrides
            return ReelSubmission(
                target=target,
                title=preset[:TITLE_MAX],
                source_url=None,
                topic=None,
                source_research_run_id=source_research_run_id,
                params=_canonical_params(
                    target, source_mode="asset", preset=preset, count=count,
                    overrides=overrides, source_asset_id=source_asset_id,
                ),
                cp_input=cp_input,
                source_asset_id=source_asset_id,
            )

        if not isinstance(handle, str) or not handle.strip():
            raise BadRequest("file submit requires an upload handle", code="missing_source")
        handle = handle.strip()
        cp_input = {"source": handle, "preset": preset, "count": count}
        if overrides:
            cp_input["overrides"] = overrides
        return ReelSubmission(
            target=target,
            title=preset[:TITLE_MAX],
            source_url=None,
            topic=None,
            source_research_run_id=source_research_run_id,
            params=_canonical_params(
                target, source_mode="file", preset=preset, count=count, overrides=overrides
            ),
            cp_input=cp_input,
            source_handle=handle,
        )

    if target == TARGET_DSL_HOOKS:
        # A1 DSL-hooks: artifact refs + exactly one source (URL or upload handle,
        # AF-a8o). Every guard below runs before the caller inserts a row or
        # dispatches (server.py ordering).
        _reject_unsupported_fields(raw_input, DSL_HOOKS_ALLOWED_INPUT_KEYS)
        _reject_both_sources(raw_input)

        source_url: str | None = None
        source_handle: str | None = None
        if raw_input.get("source") is not None:
            # FILE mode: _resolve_cp_input presigns the handle into source_url.
            source_handle = _validated_upload_handle(raw_input["source"])
        else:
            source_url = _validated_a1_source_url(raw_input.get("source_url"))

        refs = {
            key: _validated_artifact_ref(raw_input, key)
            for key in ("composite_ref", "words_ref", "hook_ref")
        }
        clip_idx = _parse_clip_idx(raw_input)
        overrides = _validated_dsl_hooks_overrides(raw_input.get("overrides"))

        cp_input = {**refs, "clip_idx": clip_idx}
        if source_url is not None:
            cp_input["source_url"] = source_url
        if overrides:
            cp_input["overrides"] = overrides
        return ReelSubmission(
            target=target,
            title=f"dsl-hooks clip {clip_idx}"[:TITLE_MAX],
            source_url=source_url,
            topic=None,
            source_research_run_id=source_research_run_id,
            params={
                "target": target,
                "source_mode": DSL_HOOKS_SOURCE_MODE,
                "clip_idx": clip_idx,
            },
            cp_input=cp_input,
            source_handle=source_handle,
        )

    if target == TARGET_TRANSCRIPT:
        # A1 transcript-to-plan (AF-a8o, leg 1): exactly one source; delivers
        # DATA (the artifact-ref triple) that the browser feeds into leg 2.
        _reject_unsupported_fields(raw_input, TRANSCRIPT_ALLOWED_INPUT_KEYS)
        _reject_both_sources(raw_input)

        if raw_input.get("source") is not None:
            handle = _validated_upload_handle(raw_input["source"])
            return ReelSubmission(
                target=target,
                title="a1 plan"[:TITLE_MAX],
                source_url=None,
                topic=None,
                source_research_run_id=source_research_run_id,
                params={
                    "target": target,
                    "source_mode": TRANSCRIPT_SOURCE_MODE,
                    "source_input": "file",
                },
                cp_input={},  # _resolve_cp_input presigns the handle into source_url
                source_handle=handle,
            )
        if raw_input.get("source_url") is None:
            raise BadRequest("source_url or source is required", code="missing_source")
        source_url = _validated_a1_source_url(raw_input["source_url"])
        return ReelSubmission(
            target=target,
            title="a1 plan"[:TITLE_MAX],
            source_url=source_url,
            topic=None,
            source_research_run_id=source_research_run_id,
            params={
                "target": target,
                "source_mode": TRANSCRIPT_SOURCE_MODE,
                "source_input": "url",
            },
            cp_input={"source_url": source_url},
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
