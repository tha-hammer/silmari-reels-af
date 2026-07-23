"""reel-af · Cutting Room — authenticated UI + ownership boundary.

Replaces the former open ``/api/*`` passthrough. Every control-plane-bound call
now goes through this service's auth + ownership chain: verify the SuperTokens
session, resolve server-trusted ``AuthContext``, authorize, validate/canonicalize
the body, stamp a ``deepresearch.reel_job`` row, then dispatch an identity-free
body to the control plane and attach the returned ``execution_id``.

The control plane and the ``reel-af`` node stay identity-free; this UI service is
the tenancy boundary.

``create_app(deps, enable_supertokens=...)`` is the seam for tests (inject fakes;
disable SuperTokens). The module-level ``app`` uses ``default_deps()`` and does
**no** DB/network I/O at import (plan B0.1 / B1). Container entrypoint is
``gunicorn ... server:app``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import os
import re
import tempfile
import uuid
from importlib import import_module
from pathlib import Path

from carousels import CarouselHqRecreateGuard, HqRecreateCapError, build_carousel_create
from deps import (
    AppDeps,
    BadGateway,
    BadRequest,
    Conflict,
    HttpError,
    NotFound,
    RepositoryUnavailable,
    SchemaUnavailable,
    default_deps,
)
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from projects import (
    project_asset_view,
    project_view,
    validate_asset_title,
    validate_asset_type,
    validate_link_url,
    validate_project_description,
    validate_project_name,
    validate_source_asset_ref,
)
from reel_jobs import (
    A1_DELIVERY_UNAVAILABLE,
    DELIVERY_REQUIRED_TARGETS,
    FORBIDDEN_IDENTITY_FIELDS,
    PRESIGN_CP_KEY_BY_TARGET,
    PRESIGN_CP_KEY_DEFAULT,
    TERMINAL_STATUSES,
    TEXT_TARGET_BY_OUTPUT,
    ReelJobStatus,
    _is_valid_url,
    build_research_dispatch,
    build_submission,
    normalize_reel_status,
)
from source_assets import asset_view, describe_upload

HERE = os.path.dirname(os.path.abspath(__file__))
IDEMPOTENCY_RETRY_AFTER_S = 3
# INT-02: the durable-cursor consumer driver is opt-in (off by default) so tests and
# request-only deployments never spawn the background thread. Prod sets this to start it.
ENV_CONSUMER_ENABLED = "REEL_CONSUMER_ENABLED"
API_MESSAGES_PATH = os.path.join(HERE, "api_messages.json")

# Named literals introduced by this plan (§10 CodeCleanup gate) — headers, the
# idempotency-pending code, and the handful of HTTP statuses set directly here
# (error statuses come from the typed exceptions in deps.py).
HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"
HEADER_RETRY_AFTER = "Retry-After"
IDEMPOTENT_PENDING_CODE = "idempotent_request_pending"
HTTP_CREATED = 201
HTTP_ACCEPTED = 202
HTTP_CONFLICT = 409
HTTP_NOT_FOUND = 404
TARGET_CAROUSEL = "reel-af.reel_research_to_carousel"
RECREATE_OUTPUT_DIR = "reel-af-carousel-recreate"

# Pure route predicates — inspect method/subpath ONLY (no I/O, no body parse).
_SUBMIT_RE = re.compile(r"^v1/execute/async/([^/]+)$")
_POLL_RE = re.compile(r"^v1/executions/([^/]+)$")
_CAROUSEL_GET_RE = re.compile(r"^v1/carousels/([^/]+)$")
_CAROUSEL_RECREATE_RE = re.compile(r"^v1/carousels/([^/]+)/slides/(\d+)/recreate$")
_CAROUSEL_CANCEL_RE = re.compile(r"^v1/carousels/([^/]+)/cancel$")
_CAROUSEL_FINALIZE_RE = re.compile(r"^v1/carousels/([^/]+)/finalize$")
_SLIDE_RE = re.compile(r"^v1/carousels/([^/]+)/slides/(\d+)$")
_RESEARCH_POLL_RE = re.compile(r"^v1/research/([^/]+)$")
# INT-04: read-only, ORG-SCOPED lineage surface (optional dashboard). GET-only.
_LINEAGE_ENTITY_RE = re.compile(r"^v1/lineage/entity/([^/]+)$")
_LINEAGE_RUN_RE = re.compile(r"^v1/lineage/run/([^/]+)$")


def _load_api_messages() -> dict[str, str]:
    with open(API_MESSAGES_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise RuntimeError("api_messages.json must contain a flat object")
    return {str(key): str(value) for key, value in raw.items()}


API_MESSAGES = _load_api_messages()


def _is_carousel_create(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/carousels"


def _is_upload(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/uploads"


def _is_source_asset_list(method: str, sub: str) -> bool:
    return method == "GET" and sub == "v1/source-assets"


# AF-4pz.4/.5 — Projects CRUD + attached assets. Pure predicates like siblings.
_PROJECT_RE = re.compile(r"^v1/projects/([^/]+)$")
_PROJECT_ASSETS_RE = re.compile(r"^v1/projects/([^/]+)/assets$")
_PROJECT_ASSET_RE = re.compile(r"^v1/projects/([^/]+)/assets/([^/]+)$")
_PROJECT_ASSET_DOWNLOAD_RE = re.compile(r"^v1/projects/([^/]+)/assets/([^/]+)/download$")
_PROJECT_REELS_RE = re.compile(r"^v1/projects/([^/]+)/reels$")


def _is_projects_collection(method: str, sub: str) -> bool:
    return method in ("GET", "POST") and sub == "v1/projects"


def _project_target(method: str, sub: str) -> str | None:
    if method not in ("GET", "PATCH", "DELETE"):
        return None
    m = _PROJECT_RE.match(sub)
    return m.group(1) if m else None


def _project_assets_target(method: str, sub: str) -> str | None:
    if method not in ("GET", "POST"):
        return None
    m = _PROJECT_ASSETS_RE.match(sub)
    return m.group(1) if m else None


def _project_asset_target(method: str, sub: str) -> tuple[str, str] | None:
    if method != "DELETE":
        return None
    m = _PROJECT_ASSET_RE.match(sub)
    return (m.group(1), m.group(2)) if m else None


def _project_asset_download_target(method: str, sub: str) -> tuple[str, str] | None:
    if method != "GET":
        return None
    m = _PROJECT_ASSET_DOWNLOAD_RE.match(sub)
    return (m.group(1), m.group(2)) if m else None


def _project_reels_target(method: str, sub: str) -> str | None:
    if method != "GET":
        return None
    m = _PROJECT_REELS_RE.match(sub)
    return m.group(1) if m else None


def _is_research_run(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/research/run"


def _is_create_from_research(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/research/create"


def _slide_target(method: str, sub: str) -> tuple[str, int] | None:
    if method != "GET":
        return None
    m = _SLIDE_RE.match(sub)
    return (m.group(1), int(m.group(2))) if m else None


def _submit_target(method: str, sub: str) -> str | None:
    if method != "POST":
        return None
    m = _SUBMIT_RE.match(sub)
    return m.group(1) if m else None


def _poll_id(method: str, sub: str) -> str | None:
    if method != "GET":
        return None
    m = _POLL_RE.match(sub)
    return m.group(1) if m else None


def _carousel_id(method: str, sub: str) -> str | None:
    if method != "GET":
        return None
    m = _CAROUSEL_GET_RE.match(sub)
    return m.group(1) if m else None


def _carousel_recreate_target(method: str, sub: str) -> tuple[str, int] | None:
    if method != "POST":
        return None
    m = _CAROUSEL_RECREATE_RE.match(sub)
    return (m.group(1), int(m.group(2))) if m else None


def _carousel_cancel_id(method: str, sub: str) -> str | None:
    if method != "POST":
        return None
    m = _CAROUSEL_CANCEL_RE.match(sub)
    return m.group(1) if m else None


def _carousel_finalize_id(method: str, sub: str) -> str | None:
    if method != "POST":
        return None
    m = _CAROUSEL_FINALIZE_RE.match(sub)
    return m.group(1) if m else None


def _research_poll_id(method: str, sub: str) -> str | None:
    if method != "GET":
        return None
    m = _RESEARCH_POLL_RE.match(sub)
    return m.group(1) if m else None


def _lineage_entity_id(method: str, sub: str) -> str | None:
    if method != "GET":                                   # POST/PUT/DELETE -> not routed (no write)
        return None
    m = _LINEAGE_ENTITY_RE.match(sub)
    return m.group(1) if m else None


def _lineage_run_id(method: str, sub: str) -> str | None:
    if method != "GET":
        return None
    m = _LINEAGE_RUN_RE.match(sub)
    return m.group(1) if m else None


# ─────────────────────────── handlers ───────────────────────────


def _client_request_id(deps: AppDeps, body: dict | None) -> str:
    """Idempotency key: `Idempotency-Key` header, else `input.client_request_id`
    fallback, else a server-generated key (no dedup). Never ownership identity."""
    header = request.headers.get(HEADER_IDEMPOTENCY_KEY)
    if header:
        return header
    fallback = (body or {}).get("input", {}).get("client_request_id")
    return str(fallback) if fallback else deps.uuid_factory().hex


def _idempotent_response(ref) -> tuple[Response, int]:
    """Response for a returning (already-seen) idempotency key — no CP call."""
    if ref.execution_id:
        return jsonify({"execution_id": ref.execution_id, "job_id": str(ref.job_id),
                        "status": ref.status}), HTTP_ACCEPTED
    if ref.status in TERMINAL_STATUSES:
        return jsonify({"job_id": str(ref.job_id), "status": ref.status}), HTTP_ACCEPTED
    resp = jsonify({"code": IDEMPOTENT_PENDING_CODE, "job_id": str(ref.job_id)})
    resp.headers[HEADER_RETRY_AFTER] = str(IDEMPOTENCY_RETRY_AFTER_S)
    return resp, HTTP_CONFLICT


def _idempotent_carousel_response(ref) -> tuple[Response, int]:
    """Carousel create replay response; mirrors reel idempotency but never emits job_id."""
    if ref.execution_id:
        return jsonify({
            "execution_id": ref.execution_id,
            "carousel_id": str(ref.job_id),
            "status": ref.status,
        }), HTTP_ACCEPTED
    if ref.status in TERMINAL_STATUSES:
        return jsonify({"carousel_id": str(ref.job_id), "status": ref.status}), HTTP_ACCEPTED
    resp = jsonify({"code": IDEMPOTENT_PENDING_CODE, "carousel_id": str(ref.job_id)})
    resp.headers[HEADER_RETRY_AFTER] = str(IDEMPOTENCY_RETRY_AFTER_S)
    return resp, HTTP_CONFLICT


def _resolve_result_ref(execution_id: str, cp_body: dict) -> str | None:
    """B0.5 result-ref namespace: a fetchable URL/URI if present, else a stable
    ``cp-execution://`` reference to the CP's local ``video_path``."""
    result = cp_body.get("result") or {}
    for key in ("download_url", "url"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("object_uri", "uri", "path"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://", "s3://", "gs://")):
            return value
    if result.get("video_path"):
        return f"cp-execution://{execution_id}/result/video_path"
    return None


def _execution_error(cp_body: dict) -> object | None:
    error = cp_body.get("error")
    if error:
        return error
    result = cp_body.get("result")
    if isinstance(result, dict):
        error = result.get("error")
        if error:
            return error
    return None


def _normalize_execution_status(cp_body: dict) -> ReelJobStatus:
    normalized = normalize_reel_status(cp_body.get("status"))
    if normalized == "succeeded" and _execution_error(cp_body) is not None:
        return "failed"
    return normalized


def _is_browser_deliverable(ref: str | None) -> bool:
    """Pure question: is this ref a browser-fetchable http(s) URL?

    Delegates to reel_jobs._is_valid_url (scheme AND netloc) rather than a
    startswith check, which would accept "https://" with no host.
    """

    return isinstance(ref, str) and _is_valid_url(ref)


def _delivery_error(job, normalized: ReelJobStatus, result_ref: str | None) -> str | None:
    """The locally-derived delivery error for this job, or None.

    Pure question — no writes. Scoped to DELIVERY_REQUIRED_TARGETS so
    composite/topic/research keep today's fail-soft behavior unchanged.
    """

    target = (getattr(job, "params", None) or {}).get("target")
    if target not in DELIVERY_REQUIRED_TARGETS:
        return None
    if normalized != "succeeded":
        return None
    if _is_browser_deliverable(result_ref):
        return None
    return A1_DELIVERY_UNAVAILABLE


def _poll_response_body(
    cp_body: dict,
    normalized: ReelJobStatus,
    job=None,
    *,
    local_error: str | None = None,
) -> dict:
    payload = dict(cp_body)
    payload["status"] = normalized
    source_run_id = getattr(job, "source_research_run_id", None)
    if source_run_id is not None:
        payload["source_research_run_id"] = str(source_run_id)
    if local_error is not None:
        # A locally-derived terminal failure (e.g. delivery_unavailable). The CP
        # reported success, so `error` is absent from cp_body and _execution_error
        # cannot supply it. Strip the whole `result` dict: `dict(cp_body)` above
        # copies it wholesale, which would leak the node-local video_path.
        payload.pop("result", None)
        payload["error"] = local_error
        return payload
    if normalized == "failed" and "error" not in payload:
        error = _execution_error(cp_body)
        if error is not None:
            payload["error"] = error
    return payload


def _belongs_to_org(ctx, handle: str) -> bool:
    """True iff the upload key is under the caller's org prefix (Phase 0 ownership)."""
    return isinstance(handle, str) and handle.strip().startswith(f"{ctx.org_id}/")


def _resolve_cp_input(deps: AppDeps, ctx, submission) -> dict:
    """The dispatched, identity-free input. For file-mode composites, resolve the
    ctx-owned upload handle to a fresh presigned URL the reel-af node can fetch and
    drop the raw handle — the node consumes ``url`` (T7). A handle not under the
    caller's org is concealed as 404 BEFORE presign/insert/CP (Phase 0 ownership).
    Presigning here, before the DB insert, fails closed (503) with no orphan row
    when the store is unconfigured, and keeps the ephemeral signed URL unpersisted."""
    cp_input = dict(submission.cp_input)
    if submission.source_asset_id is not None:
        # AF-4pz.2 asset mode: resolve the persisted upload org-scoped (404
        # conceals foreign/absent/soft-deleted BEFORE presign/insert/CP), then
        # presign the STORED bucket key exactly like the handle path below.
        asset = deps.source_assets.get(ctx, submission.source_asset_id)
        cp_key = PRESIGN_CP_KEY_BY_TARGET.get(submission.target, PRESIGN_CP_KEY_DEFAULT)
        cp_input[cp_key] = deps.uploads.presign(ctx, asset.bucket_key)  # 503 if store unconfigured
        return cp_input
    if submission.source_handle:
        if not _belongs_to_org(ctx, submission.source_handle):
            raise NotFound("upload handle not found", code="upload_not_found")  # no presign, no row, no CP
        cp_input.pop("source", None)
        # Target-aware param name (AF-a8o): composite consumes ``url``; the A1
        # reasoners (transcript_to_plan / dsl_hooks_to_reels) consume ``source_url``.
        cp_key = PRESIGN_CP_KEY_BY_TARGET.get(submission.target, PRESIGN_CP_KEY_DEFAULT)
        cp_input[cp_key] = deps.uploads.presign(ctx, submission.source_handle)  # 503 if store unconfigured
    return cp_input


def _source_research_run_id(deps: AppDeps, ctx, body: dict | None) -> uuid.UUID | None:
    if not isinstance(body, dict):
        return None
    raw = body.get("research_run_id")
    if raw is None:
        raw = body.get("source_research_run_id")
    if raw in (None, ""):
        return None
    try:
        run_id = uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise BadRequest("research_run_id must be a UUID", code="invalid_research_run_id") from exc
    deps.reel_jobs.get_research_run(ctx, run_id)  # 404 conceals absent/cross-org
    return run_id


def _log_orphaned_dispatch(deps: AppDeps, ctx, *, job_id, execution_id, crid, target, exc) -> None:
    """Operator-visible record of a CP dispatch that was accepted but not attached.

    Shared by BOTH dispatch paths (_handle_submit and the fan-out _dispatch_one) —
    one helper, not two copies. Carries `target` so an operator can tell which
    reasoner owns the orphaned execution.

    This is log-only ON PURPOSE (B15a). The durable record + repair path (B15b) is
    deferred: the trigger IS database unavailability, so a Postgres row would be
    unreachable exactly when it matters, and this repo owns no migrations. The
    recommended repair is a CP-reconciling sweep over mark_stale_queued's existing
    `status='queued' AND execution_id IS NULL` predicate — the reel_job row already
    IS the durable record.
    """

    deps.logger.error(
        "orphaned_dispatch job_id=%s execution_id=%s org_id=%s created_by=%s "
        "client_request_id=%s target=%s err=%s",
        job_id, execution_id, ctx.org_id, ctx.user_id, crid, target, exc,
    )


def _project_ref_id(deps: AppDeps, ctx, body: dict | None) -> uuid.UUID | None:
    """AF-8bk: optional top-level ``project_id`` — a reference, never trusted
    for ownership. UUID-validated (400), then resolved org-scoped through the
    projects repo so a foreign/absent project is concealed as 404 BEFORE any
    row or CP dispatch (mirrors ``_source_research_run_id``)."""
    if not isinstance(body, dict):
        return None
    raw = body.get("project_id")
    if raw in (None, ""):
        return None
    try:
        project_id = uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise BadRequest("project_id must be a UUID", code="invalid_project_id") from exc
    return deps.projects.get(ctx, project_id).project_id   # 404 conceals


def _handle_submit(deps: AppDeps, target: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)            # 401 / 403 / 503, before any CP call
    deps.access_guard.authorize_create(ctx)         # 403 fail-closed
    body = request.get_json(silent=True)
    source_research_run_id = _source_research_run_id(deps, ctx, body)
    project_id = _project_ref_id(deps, ctx, body)   # 400 malformed / 404 foreign / None
    submission = build_submission(
        target, body, source_research_run_id=source_research_run_id
    )                                               # 400 (incl. forbidden identity fields)
    if project_id is not None:
        # Single-point stamp (frozen dataclass): reels roll under their project.
        submission = dataclasses.replace(submission, project_id=project_id)
    cp_input = _resolve_cp_input(deps, ctx, submission)  # file-mode: ctx-owned handle → url (404/503)

    job_id = deps.uuid_factory()
    now = deps.clock.now()
    crid = _client_request_id(deps, body)
    ref = deps.reel_jobs.insert_or_get_queued(ctx, submission, job_id, now, crid)  # 503 until DB
    if not ref.created:
        return _idempotent_response(ref)            # returning key → no second CP dispatch

    cp_body_out = {"input": cp_input}                # canonical, identity-free
    try:
        status, cp_body, _headers = deps.control_plane.dispatch_async(target, cp_body_out)
    except HttpError:
        deps.reel_jobs.mark_failed(ctx, ref.job_id, "dispatch_error", now)
        raise                                        # BadGateway(502) etc
    if status >= 400:
        deps.reel_jobs.mark_failed(ctx, ref.job_id, f"cp_status_{status}", now)
        return jsonify(cp_body), status              # passthrough 400/422/429/503
    if "execution_id" not in cp_body:
        deps.reel_jobs.mark_failed(ctx, ref.job_id, "no_execution_id", now)
        raise BadGateway("control plane returned no execution_id")
    try:
        deps.reel_jobs.attach_execution_id(ctx, ref.job_id, cp_body["execution_id"])
    except HttpError as exc:
        _log_orphaned_dispatch(
            deps, ctx, job_id=ref.job_id, execution_id=cp_body["execution_id"],
            crid=crid, target=target, exc=exc,
        )
        raise RepositoryUnavailable("dispatch accepted but ownership attach failed") from exc
    payload = dict(cp_body)
    payload.setdefault("job_id", str(ref.job_id))
    return jsonify(payload), status


def _handle_research_run(deps: AppDeps) -> tuple[Response, int]:
    """Dispatch a deep-research run. The deep-research node OWNS and writes
    ``research_run`` (ARCHITECTURE §11) — reel-af issues ZERO INSERT/UPDATE against
    it (INT Phase 0). reel-af returns its OWN handle (``research_run_id``) alongside
    the owner-minted ``execution_id``; on CP failure it passes the body through with
    NO owner-table write.
    """
    ctx = deps.identity.resolve(request)                     # 401 / 403 / 503
    deps.access_guard.authorize_create(ctx)                  # 403 fail-closed
    target, cp_body_out = build_research_dispatch(request.get_json(silent=True))  # 400 empty/forbidden
    run_id = deps.uuid_factory()                             # reel-af's OWN handle (not an owner row)
    status, cp_body, _headers = deps.control_plane.dispatch_async(target, cp_body_out)
    if status >= 400 or "execution_id" not in cp_body:
        return jsonify(cp_body), status                      # passthrough; NO owner-table write on failure
    return jsonify({"research_run_id": str(run_id),
                    "execution_id": cp_body["execution_id"]}), status


def _research_result_body(cp_body: dict, normalized: ReelJobStatus) -> dict:
    """Surface the three research document keys from the CP result (empty while running)."""
    result = cp_body.get("result") or {}
    return {
        "status": normalized,
        "markdown": result.get("markdown"),
        "html": result.get("html"),
        "sources": result.get("sources", []),
    }


def _handle_research_poll(deps: AppDeps, execution_id: str) -> tuple[Response, int]:
    """Poll a research run by ``execution_id`` and surface {status, markdown, html,
    sources} from the control plane. INT Phase 0: reel-af issues NO write to the
    owner's ``research_run`` table — it does not reconcile status into it.

    NOTE (deferred, coupled to claude-alpha's pg.py migration): the ownership 404
    concealment still goes through ``get_research_by_execution`` (a read of the owner
    table in prod). Replacing it with the identity-free owner-interface read
    (``deps.research_reader``) drops reel-af-side cross-org concealment — a
    tenancy-semantics decision left to the Phase 0/1 handoff, not guessed here (ISC-4 poll)."""
    ctx = deps.identity.resolve(request)
    deps.reel_jobs.get_research_by_execution(ctx, execution_id)  # 404 conceals foreign/absent
    status, cp_body, _headers = deps.control_plane.get_execution(execution_id)
    if status >= 400:
        return jsonify(cp_body), status                      # transient CP error passthrough
    normalized = _normalize_execution_status(cp_body)
    return jsonify(_research_result_body(cp_body, normalized)), status


# ─────────────── create-from-research fan-out (Plan 5, ISC-30/35) ───────────────


class _DispatchOutcome:
    """Result of enqueuing one fan-out leg (no exception on CP failure — the caller
    applies the partial-failure contract: 502 only when ZERO legs enqueued)."""

    def __init__(self, ok: bool, execution_id: str | None, outcome: str):
        self.ok, self.execution_id, self.outcome = ok, execution_id, outcome


def _validate_outputs(body: dict) -> list[str]:
    """Sorted, de-duplicated output list. Empty → 400; unknown type → 400 (review C2)."""
    raw = body.get("outputs")
    if not isinstance(raw, list) or not raw:
        raise BadRequest("outputs must be a non-empty list", code="invalid_outputs")
    outputs = sorted(set(raw))
    for output in outputs:
        if output not in TEXT_TARGET_BY_OUTPUT:  # single source of truth for valid outputs
            raise BadRequest(f"unknown output type: {output}", code="unknown_output")
    return outputs


def _dispatch_one(deps: AppDeps, ctx, target, submission, job_id, crid, now) -> _DispatchOutcome:
    """Enqueue one submission: insert queued row → dispatch → attach execution_id.
    Returns a disposition instead of raising on CP failure, so the fan-out can honor
    'no cross-output rollback; 502 only when zero enqueued' (Plan 5 review C2)."""
    cp_input = _resolve_cp_input(deps, ctx, submission)      # text-mode: identity/provenance-free
    ref = deps.reel_jobs.insert_or_get_queued(ctx, submission, job_id, now, crid)
    if not ref.created:
        return _DispatchOutcome(True, ref.execution_id, "idempotent")  # returning key, already enqueued
    try:
        status, cp_body, _headers = deps.control_plane.dispatch_async(target, {"input": cp_input})
    except HttpError:
        deps.reel_jobs.mark_failed(ctx, ref.job_id, "dispatch_error", now)
        return _DispatchOutcome(False, None, "cp_error")
    if status >= 400:
        deps.reel_jobs.mark_failed(ctx, ref.job_id, f"cp_status_{status}", now)
        return _DispatchOutcome(False, None, "cp_error")
    if "execution_id" not in cp_body:
        deps.reel_jobs.mark_failed(ctx, ref.job_id, "no_execution_id", now)
        return _DispatchOutcome(False, None, "no_execution_id")
    try:
        deps.reel_jobs.attach_execution_id(ctx, ref.job_id, cp_body["execution_id"])
    except HttpError as exc:
        # Was unguarded: an HttpError here escaped the function entirely, breaching
        # this function's own "returns a disposition instead of raising" contract
        # and aborting every sibling leg of the fan-out. The CP has already
        # accepted the work, so record the orphan and report the disposition.
        _log_orphaned_dispatch(
            deps, ctx, job_id=ref.job_id, execution_id=cp_body["execution_id"],
            crid=crid, target=target, exc=exc,
        )
        return _DispatchOutcome(False, None, "attach_failed")
    return _DispatchOutcome(True, cp_body["execution_id"], "enqueued")


def _handle_create_from_research(deps: AppDeps) -> tuple[Response, int]:
    """Create-from-text fan-out: for each selected output build a text submission and
    dispatch it, preserving provenance on the DB row (never the reasoner input). One
    idempotency sub-key per output; distinct job_id per output; multi-job response."""
    ctx = deps.identity.resolve(request)                     # 401
    deps.access_guard.authorize_create(ctx)                  # 403
    body = request.get_json(silent=True) or {}
    for key in body:                                         # forbidden identity on the create body
        if key in FORBIDDEN_IDENTITY_FIELDS:
            raise BadRequest(f"forbidden identity field: {key}", code="forbidden_field")
    outputs = _validate_outputs(body)                        # 400 empty/unknown, sorted+deduped
    run_id = _source_research_run_id(deps, ctx, body)        # 400 malformed / 404 cross-org / None
    crid = _client_request_id(deps, body)
    now = deps.clock.now()

    jobs, enqueued = [], 0
    for output in outputs:                                   # deterministic sorted order (C2)
        target = TEXT_TARGET_BY_OUTPUT[output]
        submission = build_submission(  # 400 invalid_text before any dispatch on the first leg
            target, {"input": {"text": body.get("text")}}, source_research_run_id=run_id)
        job_id = deps.uuid_factory()                         # DISTINCT per output (review C3)
        result = _dispatch_one(deps, ctx, target, submission, job_id, f"{crid}:{output}", now)
        jobs.append({"output": output, "job_id": str(job_id),
                     "execution_id": result.execution_id, "outcome": result.outcome})
        enqueued += 1 if result.ok else 0

    if enqueued == 0:                                        # partial-failure contract (C2)
        raise BadGateway("all create-from-research dispatches failed")
    return jsonify({"jobs": jobs}), HTTP_ACCEPTED


def _handle_carousel_create(deps: AppDeps) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    deps.access_guard.authorize_create(ctx)
    body = request.get_json(silent=True)
    create = build_carousel_create(body)
    if create.source_research_run_id is not None:
        deps.reel_jobs.get_research_run(ctx, create.source_research_run_id)

    carousel_id = deps.uuid_factory()
    now = deps.clock.now()
    crid = _client_request_id(deps, body)
    ref = deps.carousels.insert_or_get_draft(ctx, create, carousel_id, now, crid)
    if not ref.created:
        return _idempotent_carousel_response(ref)

    try:
        status, cp_body, _headers = deps.control_plane.dispatch_async(
            TARGET_CAROUSEL, {"input": create.cp_input()}
        )
    except HttpError:
        deps.carousels.set_status(ctx, ref.job_id, "failed")
        raise
    if status >= 400:
        deps.carousels.set_status(ctx, ref.job_id, "failed")
        return jsonify(cp_body), status
    if "execution_id" not in cp_body:
        deps.carousels.set_status(ctx, ref.job_id, "failed")
        raise BadGateway("control plane returned no execution_id")
    deps.carousels.attach_execution_id(ctx, ref.job_id, cp_body["execution_id"])
    payload = dict(cp_body)
    payload.setdefault("carousel_id", str(ref.job_id))
    payload.setdefault("status", ref.status)
    return jsonify(payload), status


def _handle_carousel_get(deps: AppDeps, carousel_id: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    view = deps.carousels.get(ctx, carousel_id)
    return jsonify({"status": view.status, "slides": view.slides}), 200


# ─────────── INT-04 read-only, ORG-SCOPED lineage endpoints (optional dashboard) ───────────
# Resolve the caller's AuthContext; delegate to LineageView; NO write route. Another org's id
# conceals to a 200 empty list (never a cross-org leak) via the org-scoped repos.


def _handle_lineage_entity(deps: AppDeps, entity_id: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    return jsonify([u.to_json() for u in deps.lineage.what_produced(ctx, entity_id)]), 200


def _handle_lineage_run(deps: AppDeps, run_id: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    return jsonify([d.to_json() for d in deps.lineage.what_came_from(ctx, run_id)]), 200


def _openrouter_provider():
    import_module("reel_af.sdk_patches")
    from agentfield.media_providers import OpenRouterProvider

    return OpenRouterProvider()


async def _call_plan2_recreate(
    *,
    carousel: dict,
    idx: int,
    note: str,
    out_dir: str,
    provider,
    storage,
    guard,
) -> dict:
    from reel_af.recreate import recreate_slide

    return await recreate_slide(
        carousel=carousel,
        idx=idx,
        note=note,
        out_dir=out_dir,
        provider=provider,
        storage=storage,
        guard=guard,
        acknowledge_premium=True,
    )


def _recreate_out_dir() -> str:
    root = Path(os.getenv("REEL_CAROUSEL_RECREATE_DIR", tempfile.gettempdir()))
    return str(root / RECREATE_OUTPUT_DIR)


def _carousel_manifest(carousel_id: str, view) -> dict:
    slides = []
    for slide in view.slides:
        prompt = slide.get("image_prompt") or slide.get("prompt") or ""
        slides.append({**slide, "image_prompt": prompt})
    return {"carousel_id": carousel_id, "run_id": carousel_id, "slides": slides}


def _resolve_recreate_result(result):
    return asyncio.run(result) if inspect.isawaitable(result) else result


def _handle_carousel_recreate(deps: AppDeps, carousel_id: str, slide_idx: int, recreate_fn) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    deps.access_guard.authorize_create(ctx)
    view = deps.carousels.get(ctx, carousel_id)  # 404 before paid work / cross-org spend
    has_openrouter_key = "OPENROUTER_API_KEY" in os.environ
    if not has_openrouter_key:
        raise SchemaUnavailable(API_MESSAGES["CAROUSEL_RECREATE_OPENROUTER_REQUIRED"])
    body = request.get_json(silent=True) or {}
    note = body.get("note", "")
    note = note if isinstance(note, str) else str(note)
    note_is_blank = not note.strip()
    if note_is_blank:
        raise BadRequest(API_MESSAGES["CAROUSEL_RECREATE_NOTE_REQUIRED"], code="invalid_note")
    slide_count = len(view.slides)
    slide_out_of_range = slide_idx < 0 or slide_idx >= slide_count
    if slide_out_of_range:
        raise NotFound(API_MESSAGES["CAROUSEL_SLIDE_NOT_FOUND"])
    provider = _openrouter_provider()
    guard = CarouselHqRecreateGuard(deps.carousels, ctx)
    carousel = _carousel_manifest(carousel_id, view)
    out_dir = _recreate_out_dir()
    try:
        if recreate_fn is None:
            slide = _resolve_recreate_result(
                _call_plan2_recreate(
                    carousel=carousel,
                    idx=slide_idx,
                    note=note,
                    out_dir=out_dir,
                    provider=provider,
                    storage=deps.storage,
                    guard=guard,
                )
            )
        else:
            slide = _resolve_recreate_result(
                recreate_fn(
                    ctx,
                    carousel_id,
                    slide_idx,
                    note,
                    provider=provider,
                    storage=deps.storage,
                    guard=guard,
                    carousel=carousel,
                    out_dir=out_dir,
                )
            )
    except HqRecreateCapError as exc:
        raise Conflict(str(exc), code="hq_recreate_cap_exceeded") from exc
    deps.carousels.replace_slide(
        ctx,
        carousel_id,
        slide_idx,
        slide.get("image_ref"),
        slide.get("prompt") or slide.get("image_prompt"),
        slide.get("status", "ok"),
    )
    return jsonify(slide), 200


def _handle_carousel_cancel(deps: AppDeps, carousel_id: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    deps.carousels.get(ctx, carousel_id)  # 404 before any destructive work
    refs = deps.carousels.draft_slide_refs(ctx, carousel_id)
    for ref in refs:
        try:
            deps.storage.delete(ref)
        except HttpError:
            deps.logger.warning("carousel_cancel_delete_failed carousel_id=%s ref=%s", carousel_id, ref)
    deps.carousels.set_status(ctx, carousel_id, "cancelled")
    return jsonify({"status": "cancelled"}), 200


def _handle_carousel_finalize(deps: AppDeps, carousel_id: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    deps.carousels.get(ctx, carousel_id)  # 404 conceals absent/foreign rows before write
    deps.carousels.set_status(ctx, carousel_id, "succeeded")
    view = deps.carousels.get(ctx, carousel_id)
    return jsonify({"status": view.status}), 200


def _handle_slide(deps: AppDeps, carousel_id: str, slide_idx: int) -> tuple[Response, int]:
    """Serve a carousel slide image: auth → resolve org-scoped ref → confirm the
    object exists → 302-redirect to a presigned object-storage URL. Concealment
    (cross-org → 404) lives in ``deps.slides.resolve`` (Plan 6 real impl), not here."""
    ctx = deps.identity.resolve(request)                       # 401 / 403 / 503 first
    ref = deps.slides.resolve(ctx, carousel_id, slide_idx)     # 404 conceals cross-org/absent
    if not deps.storage.exists(ref):
        raise NotFound("slide image not found")                # 404, fail-closed (ISC-48)
    url = deps.storage.presigned_url(ref)
    return redirect(url), 302   # single 302: tuple status, matching sibling (Response,int) handlers


def _handle_upload(deps: AppDeps) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    deps.access_guard.authorize_create(ctx)
    file = request.files.get("file")
    # AF-02f: checksum/size BEFORE the store consumes the stream (pure; seeks
    # back). None → the store raises the canonical no-file 400 below.
    meta = describe_upload(file)
    handle = deps.uploads.store(ctx, file)
    # Durable, org-scoped upload record (server-derived identity stamps). The
    # record is REQUIRED — a repo failure here is a fail-closed 503; the stored
    # object without a row is an acceptable orphan, never a silent success.
    asset = deps.source_assets.create(
        ctx,
        asset_id=deps.uuid_factory(),
        bucket_key=handle["path"],
        now=deps.clock.now(),
        **meta,
    )
    return jsonify({**handle, "asset_id": str(asset.asset_id)}), HTTP_CREATED


def _handle_projects_collection(deps: AppDeps) -> tuple[Response, int]:
    """AF-4pz.4: POST create / GET list — org-scoped, owner-stamped."""
    ctx = deps.identity.resolve(request)
    if request.method == "GET":
        return jsonify(
            {"projects": [project_view(p) for p in deps.projects.list_for_org(ctx)]}
        ), 200
    deps.access_guard.authorize_create(ctx)
    body = request.get_json(silent=True) or {}
    project = deps.projects.create(
        ctx,
        project_id=deps.uuid_factory(),
        name=validate_project_name(body.get("name")),
        description=validate_project_description(body.get("description")),
        now=deps.clock.now(),
    )
    return jsonify(project_view(project)), HTTP_CREATED


def _handle_project(deps: AppDeps, project_id: str) -> tuple[Response, int]:
    """AF-4pz.4: GET one / PATCH rename / DELETE soft — foreign concealed 404."""
    ctx = deps.identity.resolve(request)
    if request.method == "GET":
        return jsonify(project_view(deps.projects.get(ctx, project_id))), 200
    deps.access_guard.authorize_create(ctx)
    if request.method == "DELETE":
        deps.projects.soft_delete(ctx, project_id, now=deps.clock.now())
        return Response(status=204), 204
    body = request.get_json(silent=True) or {}
    name = validate_project_name(body["name"]) if "name" in body else None
    description = (
        validate_project_description(body["description"]) if "description" in body else None
    )
    updated = deps.projects.update(
        ctx, project_id, name=name, description=description, now=deps.clock.now()
    )
    return jsonify(project_view(updated)), 200


def _handle_project_assets(deps: AppDeps, project_id: str) -> tuple[Response, int]:
    """AF-4pz.5: POST add / GET list assets. The project resolves first (404
    conceals foreign/absent) BEFORE any upload or row write."""
    ctx = deps.identity.resolve(request)
    project = deps.projects.get(ctx, project_id)
    if request.method == "GET":
        assets = deps.project_assets.list_for_project(ctx, project.project_id)
        return jsonify({"assets": [project_asset_view(a) for a in assets]}), 200

    deps.access_guard.authorize_create(ctx)
    is_multipart = request.content_type and request.content_type.startswith("multipart/")
    body = request.form if is_multipart else (request.get_json(silent=True) or {})
    asset_type = validate_asset_type(body.get("asset_type"))
    title = validate_asset_title(body.get("title"))

    source_asset_id = bucket_key = url = None
    if asset_type == "link":
        url = validate_link_url(body.get("url"))
    elif asset_type == "video" and not is_multipart:
        # Reuse of a persisted upload — org-scoped resolve, 404 conceals foreign.
        ref = validate_source_asset_ref(body.get("source_asset_id"))
        source_asset_id = deps.source_assets.get(ctx, ref).asset_id
    else:
        # image/document (and fresh video) uploads ride the existing store —
        # same validation, org-prefixed key, canonical 400/413/503 guards.
        handle = deps.uploads.store(ctx, request.files.get("file"))
        bucket_key = handle["path"]

    asset = deps.project_assets.add(
        ctx,
        asset_id=deps.uuid_factory(),
        project_id=project.project_id,
        asset_type=asset_type,
        source_asset_id=source_asset_id,
        bucket_key=bucket_key,
        url=url,
        title=title,
        now=deps.clock.now(),
    )
    return jsonify(project_asset_view(asset)), HTTP_CREATED


def _handle_project_asset_delete(
    deps: AppDeps, project_id: str, asset_id: str
) -> tuple[Response, int]:
    """AF-4pz.5: soft-remove one attached asset (project resolves first)."""
    ctx = deps.identity.resolve(request)
    project = deps.projects.get(ctx, project_id)
    deps.access_guard.authorize_create(ctx)
    deps.project_assets.soft_delete(ctx, project.project_id, asset_id, now=deps.clock.now())
    return Response(status=204), 204


def _handle_project_reels(deps: AppDeps, project_id: str) -> tuple[Response, int]:
    """AF-8bk: the project's durable reels (org-scoped; project resolves first).
    ``download_url`` carries result_ref only when it is browser-deliverable
    (T10 discipline — never a node-local path)."""
    ctx = deps.identity.resolve(request)
    project = deps.projects.get(ctx, project_id)
    reels = deps.reel_jobs.list_for_project(ctx, project.project_id)
    return jsonify({"reels": [
        {
            "job_id": str(ref.job_id),
            "status": ref.status,
            "execution_id": ref.execution_id,
            "download_url": ref.result_ref if _is_browser_deliverable_url_str(ref.result_ref) else None,
            "created_at": ref.created_at.isoformat() if getattr(ref, "created_at", None) else None,
        }
        for ref in reels
    ]}), 200


def _is_browser_deliverable_url_str(ref) -> bool:
    return isinstance(ref, str) and (ref.startswith("https://") or ref.startswith("http://"))


def _handle_project_asset_download(
    deps: AppDeps, project_id: str, asset_id: str
) -> tuple[Response, int]:
    """AF-4pz.6: 302 to a fetchable URL for one attached asset (T10 discipline:
    the browser only ever gets server-provided URLs). Project resolves first —
    foreign/absent anything is a 404 before any presign."""
    ctx = deps.identity.resolve(request)
    project = deps.projects.get(ctx, project_id)
    asset = deps.project_assets.get(ctx, project.project_id, asset_id)
    if asset.url:
        return redirect(asset.url), 302
    bucket_key = asset.bucket_key
    if bucket_key is None and asset.source_asset_id is not None:
        # video reuse: the bytes live under the SOURCE asset's key.
        bucket_key = deps.source_assets.get(ctx, asset.source_asset_id).bucket_key
    if not bucket_key:
        raise NotFound("project asset has no downloadable content")
    return redirect(deps.uploads.presign(ctx, bucket_key)), 302


def _handle_source_asset_list(deps: AppDeps) -> tuple[Response, int]:
    # AF-02f: the caller's durable uploads. Org-scoping lives in the repo SQL
    # (rows are filtered by the resolved ctx.org_id; soft-deleted excluded).
    ctx = deps.identity.resolve(request)
    assets = deps.source_assets.list_for_org(ctx)
    return jsonify({"assets": [asset_view(a) for a in assets]}), 200


def _handle_poll(deps: AppDeps, execution_id: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)                       # 401 / 403 / 503
    job = deps.reel_jobs.get_by_execution(ctx, execution_id)   # 404 if absent/foreign
    deps.access_guard.authorize_reel_read(ctx, job)            # 403 / 404
    status, cp_body, headers = deps.control_plane.get_execution(execution_id)
    if status >= 400:
        # Transient CP backpressure/outage (429/5xx) is NOT terminal — pass it through
        # without a durable reel_job reconcile; preserve Retry-After (Phase 0 / S2).
        resp = jsonify(cp_body)
        retry_after = headers.get(HEADER_RETRY_AFTER) if headers else None
        if retry_after:
            resp.headers[HEADER_RETRY_AFTER] = retry_after
        return resp, status
    normalized = _normalize_execution_status(cp_body)          # 2xx only: reconcile (T6)
    result_ref = _resolve_result_ref(execution_id, cp_body) if normalized == "succeeded" else None
    # Delivery-required policy (B14): for DELIVERY_REQUIRED_TARGETS a "succeeded"
    # execution whose result is not browser-deliverable is TERMINAL FAILED with a
    # delivery_unavailable error code — a node-local path is never success. Pure
    # question first; the writes below stay outside the condition.
    local_error = _delivery_error(job, normalized, result_ref)
    if local_error is not None:
        normalized, result_ref = "failed", None
    completed_at = deps.clock.now() if normalized in TERMINAL_STATUSES else None
    deps.reel_jobs.update_from_execution(ctx, execution_id, normalized, result_ref, completed_at)
    return jsonify(
        _poll_response_body(cp_body, normalized, job, local_error=local_error)
    ), status


def _not_found() -> tuple[Response, int]:
    # No auth, no CP call, no API-key forwarding for unknown /api/* routes.
    return jsonify({"error": "not found", "code": "not_found"}), HTTP_NOT_FOUND


def _api_router(deps: AppDeps, subpath: str, *, recreate_fn=None) -> tuple[Response, int]:
    method = request.method
    if _is_upload(method, subpath):
        return _handle_upload(deps)
    if _is_source_asset_list(method, subpath):
        return _handle_source_asset_list(deps)
    if _is_projects_collection(method, subpath):
        return _handle_projects_collection(deps)
    project_assets_id = _project_assets_target(method, subpath)
    if project_assets_id is not None:
        return _handle_project_assets(deps, project_assets_id)
    download_ids = _project_asset_download_target(method, subpath)
    if download_ids is not None:
        return _handle_project_asset_download(deps, *download_ids)
    project_reels_id = _project_reels_target(method, subpath)
    if project_reels_id is not None:
        return _handle_project_reels(deps, project_reels_id)
    project_asset_ids = _project_asset_target(method, subpath)
    if project_asset_ids is not None:
        return _handle_project_asset_delete(deps, *project_asset_ids)
    project_id = _project_target(method, subpath)
    if project_id is not None:
        return _handle_project(deps, project_id)
    if _is_carousel_create(method, subpath):
        return _handle_carousel_create(deps)
    if _is_research_run(method, subpath):
        return _handle_research_run(deps)
    if _is_create_from_research(method, subpath):
        return _handle_create_from_research(deps)
    target = _submit_target(method, subpath)
    if target is not None:
        return _handle_submit(deps, target)
    execution_id = _poll_id(method, subpath)
    if execution_id is not None:
        return _handle_poll(deps, execution_id)
    research_execution_id = _research_poll_id(method, subpath)
    if research_execution_id is not None:
        return _handle_research_poll(deps, research_execution_id)
    carousel_id = _carousel_id(method, subpath)
    if carousel_id is not None:
        return _handle_carousel_get(deps, carousel_id)
    recreate = _carousel_recreate_target(method, subpath)
    if recreate is not None:
        return _handle_carousel_recreate(deps, *recreate, recreate_fn)
    finalize_id = _carousel_finalize_id(method, subpath)
    if finalize_id is not None:
        return _handle_carousel_finalize(deps, finalize_id)
    cancel_id = _carousel_cancel_id(method, subpath)
    if cancel_id is not None:
        return _handle_carousel_cancel(deps, cancel_id)
    slide = _slide_target(method, subpath)
    if slide is not None:
        return _handle_slide(deps, *slide)
    lineage_entity = _lineage_entity_id(method, subpath)
    if lineage_entity is not None:
        return _handle_lineage_entity(deps, lineage_entity)
    lineage_run = _lineage_run_id(method, subpath)
    if lineage_run is not None:
        return _handle_lineage_run(deps, lineage_run)
    return _not_found()


# ─────────────────────────── app factory ───────────────────────────


def _noop_auth(*_a, **_k):
    """No-op ``verify_session``-shaped decorator for tests / SuperTokens disabled."""

    def deco(fn):
        return fn

    return deco


def _resolve_auth_decorator(enable_supertokens: bool):
    if enable_supertokens:
        try:
            from supertokens_python.recipe.session.framework.flask import verify_session

            return verify_session
        except ImportError:
            pass
    return _noop_auth


def _configure_supertokens(app: Flask, deps: AppDeps) -> None:
    """Initialize SuperTokens (emailpassword + session), Flask middleware, and
    CORS — mirroring the deep-research recipe, pointed at the shared core. Guarded
    so tests run with ``enable_supertokens=False`` and never import the SDK."""
    try:
        from flask_cors import CORS
        from supertokens_python import (
            InputAppInfo,
            SupertokensConfig,
            get_all_cors_headers,
            init,
        )
        from supertokens_python.framework.flask import Middleware
        from supertokens_python.recipe import emailpassword, session
        from supertokens_python.recipe.emailpassword.interfaces import (
            APIInterface,
            GeneralErrorResponse,
        )
    except ImportError:
        deps.logger.warning("supertokens-python not installed; /auth/* not mounted")
        return

    website_domain = os.getenv("UI_WEBSITE_DOMAIN", "http://localhost:8899").rstrip("/")
    # Unified login: set to a shared parent domain (e.g. ".silmari.ai") so the session
    # cookie is shared across sibling services (tools.*, research.*, reels.*). Unset keeps
    # the host-scoped cookie. Mirrors deep-research/silmari-tools SESSION_COOKIE_DOMAIN.
    cookie_domain = os.getenv("SESSION_COOKIE_DOMAIN") or None
    conn_uri = os.getenv("SUPERTOKENS_CONNECTION_URI", "http://localhost:3567").rstrip("/")
    api_key = os.getenv("SUPERTOKENS_API_KEY") or None
    allowed = {
        e.strip().lower()
        for e in os.getenv("REEL_ALLOWED_EMAILS", os.getenv("REEL_OWNER_EMAILS", "")).split(",")
        if e.strip()
    }

    def _restrict_signups(original: APIInterface) -> APIInterface:
        orig = original.sign_up_post

        async def sign_up_post(form_fields, tenant_id, session, should_try_linking_with_session_user,
                               api_options, user_context):  # noqa: A002
            email = next((f.value or "" for f in form_fields if f.id == "email"), "").strip().lower()
            if allowed and email not in allowed:
                return GeneralErrorResponse(message="Sign-up is restricted. Contact the owner.")
            return await orig(form_fields, tenant_id, session,
                              should_try_linking_with_session_user, api_options, user_context)

        original.sign_up_post = sign_up_post
        return original

    init(
        app_info=InputAppInfo(
            app_name="reel-af",
            api_domain=website_domain,
            website_domain=website_domain,
            api_base_path="/auth",
            website_base_path="/login",
        ),
        supertokens_config=SupertokensConfig(connection_uri=conn_uri, api_key=api_key),
        framework="flask",
        recipe_list=[
            session.init(cookie_domain=cookie_domain),
            emailpassword.init(
                override=emailpassword.InputOverrideConfig(apis=_restrict_signups)
            ),
        ],
        mode="wsgi",
    )
    Middleware(app)
    CORS(
        app,
        supports_credentials=True,
        origins=[website_domain],
        allow_headers=["Content-Type", "Idempotency-Key"] + get_all_cors_headers(),
    )


def create_app(
    deps: AppDeps | None = None,
    *,
    enable_supertokens: bool = True,
    auth_decorator=None,
    recreate_fn=None,
) -> Flask:
    deps = deps or default_deps()
    app = Flask(__name__, static_folder=None)
    if enable_supertokens:
        _configure_supertokens(app, deps)
    auth = auth_decorator or _resolve_auth_decorator(enable_supertokens)

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    @app.get("/")
    @auth(session_required=False)
    def index():
        try:
            deps.identity.resolve(request)
        except SchemaUnavailable:
            # Shared user-data schema not ready — surface 503, never loop to /login
            # (masking a schema 503 as a redirect is what caused the sign-in loop).
            return jsonify({"error": "user-data schema unavailable"}), 503
        except HttpError:
            return redirect("/login")
        return send_from_directory(HERE, "index.html")

    @app.get("/projects")
    @auth(session_required=False)
    def projects_page():
        # AF-4pz.6: same auth-or-login gate as the index page.
        try:
            deps.identity.resolve(request)
        except SchemaUnavailable:
            return jsonify({"error": "user-data schema unavailable"}), 503
        except HttpError:
            return redirect("/login")
        return send_from_directory(HERE, "projects.html")

    @app.get("/carousel_ui_config.json")
    @auth(session_required=False)
    def carousel_ui_config():
        return send_from_directory(HERE, "carousel_ui_config.json")

    @app.get("/login")
    @app.get("/login/reset-password")
    def login() -> Response:
        return send_from_directory(HERE, "login.html")

    @app.route(
        "/api/<path:subpath>",
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    )
    @auth(session_required=False)
    def api(subpath: str):
        return _api_router(deps, subpath, recreate_fn=recreate_fn)

    @app.errorhandler(HttpError)
    def _on_http_error(err: HttpError):
        return jsonify({"error": err.message, "code": err.code}), err.status

    _maybe_start_consumer(deps)
    return app


def _maybe_start_consumer(deps: AppDeps):
    """INT-02 B5 lifecycle (Phase 2): start the middleware-routed consumer driver on app
    startup and stop it at process exit — but ONLY when opt-in via ``REEL_CONSUMER_ENABLED``
    (off in tests and request-only deploys). Fails closed if the contract registry is
    empty or the middleware cannot subscribe."""
    if not os.getenv(ENV_CONSUMER_ENABLED):
        return None

    try:
        import atexit

        from agentfield.handoff import HandoffMiddleware, registry
        from events import DEFAULT_CONSUMER, _build_research_handler

        mw = HandoffMiddleware(
            cp_base_url=os.getenv("AGENTFIELD_SERVER_URL", ""),
            cp_api_key=os.getenv("AGENTFIELD_API_KEY", ""),
            cursor_store=deps.cursor,
            registry=registry,
        )
        handle = mw.subscribe(
            "com.silmari.research.completed.v1",
            handler=_build_research_handler(deps),
            consumer_name=DEFAULT_CONSUMER,
        )
    except Exception:
        deps.logger.exception("handoff consumer failed to start (fail-closed)")
        return None

    atexit.register(handle.stop)
    return handle


app = create_app()


def main() -> None:  # pragma: no cover
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8899")))


if __name__ == "__main__":  # pragma: no cover
    main()
