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

import os
import re
import uuid

from carousels import HqRecreateCapError, build_carousel_create
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
from reel_jobs import (
    TERMINAL_STATUSES,
    ReelJobStatus,
    build_research_dispatch,
    build_submission,
    normalize_reel_status,
)

HERE = os.path.dirname(os.path.abspath(__file__))
IDEMPOTENCY_RETRY_AFTER_S = 3

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

# Pure route predicates — inspect method/subpath ONLY (no I/O, no body parse).
_SUBMIT_RE = re.compile(r"^v1/execute/async/([^/]+)$")
_POLL_RE = re.compile(r"^v1/executions/([^/]+)$")
_CAROUSEL_GET_RE = re.compile(r"^v1/carousels/([^/]+)$")
_CAROUSEL_RECREATE_RE = re.compile(r"^v1/carousels/([^/]+)/slides/(\d+)/recreate$")
_CAROUSEL_CANCEL_RE = re.compile(r"^v1/carousels/([^/]+)/cancel$")
_CAROUSEL_FINALIZE_RE = re.compile(r"^v1/carousels/([^/]+)/finalize$")
_SLIDE_RE = re.compile(r"^v1/carousels/([^/]+)/slides/(\d+)$")
_RESEARCH_POLL_RE = re.compile(r"^v1/research/([^/]+)$")


def _is_carousel_create(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/carousels"


def _is_upload(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/uploads"


def _is_research_run(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/research/run"


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


def _poll_response_body(cp_body: dict, normalized: ReelJobStatus, job=None) -> dict:
    payload = dict(cp_body)
    payload["status"] = normalized
    source_run_id = getattr(job, "source_research_run_id", None)
    if source_run_id is not None:
        payload["source_research_run_id"] = str(source_run_id)
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
    if submission.source_handle:
        if not _belongs_to_org(ctx, submission.source_handle):
            raise NotFound("upload handle not found", code="upload_not_found")  # no presign, no row, no CP
        cp_input.pop("source", None)
        cp_input["url"] = deps.uploads.presign(ctx, submission.source_handle)  # 503 if store unconfigured
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


def _handle_submit(deps: AppDeps, target: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)            # 401 / 403 / 503, before any CP call
    deps.access_guard.authorize_create(ctx)         # 403 fail-closed
    body = request.get_json(silent=True)
    source_research_run_id = _source_research_run_id(deps, ctx, body)
    submission = build_submission(
        target, body, source_research_run_id=source_research_run_id
    )                                               # 400 (incl. forbidden identity fields)
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
        deps.logger.error(
            "orphaned_dispatch job_id=%s execution_id=%s org_id=%s created_by=%s "
            "client_request_id=%s err=%s",
            ref.job_id, cp_body["execution_id"], ctx.org_id, ctx.user_id, crid, exc,
        )
        raise RepositoryUnavailable("dispatch accepted but ownership attach failed") from exc
    payload = dict(cp_body)
    payload.setdefault("job_id", str(ref.job_id))
    return jsonify(payload), status


def _handle_research_run(deps: AppDeps) -> tuple[Response, int]:
    """Dispatch a deep-research run and record it as an owned ``research_run`` row.

    ROW-FIRST ordering (CI-3, mirrors ``_handle_submit``): mint ``run_id`` → insert a
    ``queued`` row (execution_id=None) → dispatch → attach the CP execution_id. A crash
    mid-request leaves a recoverable ``queued`` row, never an orphan CP execution. CP
    failure / missing execution_id marks the row ``failed`` and passes the CP body through.
    """
    ctx = deps.identity.resolve(request)                     # 401 / 403 / 503
    deps.access_guard.authorize_create(ctx)                  # 403 fail-closed
    body = request.get_json(silent=True)
    target, cp_body_out = build_research_dispatch(body)      # 400 empty/forbidden
    run_id = deps.uuid_factory()
    now = deps.clock.now()
    deps.reel_jobs.insert_research_run(ctx, run_id, None, "queued", now)   # ROW FIRST
    try:
        status, cp_body, _headers = deps.control_plane.dispatch_async(target, cp_body_out)
    except HttpError:
        deps.reel_jobs.update_research_status(ctx, run_id, status="failed")
        raise                                                # 502 etc
    if status >= 400:
        deps.reel_jobs.update_research_status(ctx, run_id, status="failed")
        return jsonify(cp_body), status                      # passthrough, no orphan claim
    if "execution_id" not in cp_body:
        deps.reel_jobs.update_research_status(ctx, run_id, status="failed")
        raise BadGateway("control plane returned no execution_id")
    deps.reel_jobs.update_research_status(                    # attach execution_id
        ctx, run_id, execution_id=cp_body["execution_id"])
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
    """Poll a research run the caller owns (org-scoped; 404 conceals foreign/absent),
    reconcile status terminal-monotonically by run_id, and surface {status, markdown,
    html, sources}. Authorization is org-scope-only by design (no per-run role gate)."""
    ctx = deps.identity.resolve(request)
    run = deps.reel_jobs.get_research_by_execution(ctx, execution_id)  # 404 foreign/absent
    status, cp_body, _headers = deps.control_plane.get_execution(execution_id)
    if status >= 400:
        return jsonify(cp_body), status                      # transient CP error passthrough
    normalized = _normalize_execution_status(cp_body)
    deps.reel_jobs.update_research_status(ctx, run.id, status=normalized)  # terminal-monotonic
    return jsonify(_research_result_body(cp_body, normalized)), status


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


def _handle_carousel_recreate(deps: AppDeps, carousel_id: str, slide_idx: int, recreate_fn) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)
    deps.access_guard.authorize_create(ctx)
    deps.carousels.get(ctx, carousel_id)  # 404 before paid work / cross-org spend
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SchemaUnavailable("OPENROUTER_API_KEY is required for carousel recreate")
    if recreate_fn is None:
        raise SchemaUnavailable("carousel recreate is not configured")
    body = request.get_json(silent=True) or {}
    note = body.get("note", "")
    note = note if isinstance(note, str) else str(note)
    try:
        slide = recreate_fn(
            ctx,
            carousel_id,
            slide_idx,
            note,
            provider=deps.control_plane,
            storage=deps.storage,
            guard=deps.carousels,
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
    handle = deps.uploads.store(ctx, request.files.get("file"))
    return jsonify(handle), HTTP_CREATED


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
    completed_at = deps.clock.now() if normalized in TERMINAL_STATUSES else None
    deps.reel_jobs.update_from_execution(ctx, execution_id, normalized, result_ref, completed_at)
    return jsonify(_poll_response_body(cp_body, normalized, job)), status


def _not_found() -> tuple[Response, int]:
    # No auth, no CP call, no API-key forwarding for unknown /api/* routes.
    return jsonify({"error": "not found", "code": "not_found"}), HTTP_NOT_FOUND


def _api_router(deps: AppDeps, subpath: str, *, recreate_fn=None) -> tuple[Response, int]:
    method = request.method
    if _is_upload(method, subpath):
        return _handle_upload(deps)
    if _is_carousel_create(method, subpath):
        return _handle_carousel_create(deps)
    if _is_research_run(method, subpath):
        return _handle_research_run(deps)
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
            session.init(),
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
        except HttpError:
            return redirect("/login")
        return send_from_directory(HERE, "index.html")

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

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8899")))


if __name__ == "__main__":  # pragma: no cover
    main()
