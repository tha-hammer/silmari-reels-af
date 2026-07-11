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

from deps import AppDeps, BadGateway, HttpError, NotFound, RepositoryUnavailable, default_deps
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from reel_jobs import TERMINAL_STATUSES, ReelJobStatus, build_submission, normalize_reel_status

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

# Pure route predicates — inspect method/subpath ONLY (no I/O, no body parse).
_SUBMIT_RE = re.compile(r"^v1/execute/async/([^/]+)$")
_POLL_RE = re.compile(r"^v1/executions/([^/]+)$")


def _is_upload(method: str, sub: str) -> bool:
    return method == "POST" and sub == "v1/uploads"


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


def _poll_response_body(cp_body: dict, normalized: ReelJobStatus) -> dict:
    payload = dict(cp_body)
    payload["status"] = normalized
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


def _handle_submit(deps: AppDeps, target: str) -> tuple[Response, int]:
    ctx = deps.identity.resolve(request)            # 401 / 403 / 503, before any CP call
    deps.access_guard.authorize_create(ctx)         # 403 fail-closed
    body = request.get_json(silent=True)
    submission = build_submission(target, body)     # 400 (incl. forbidden identity fields)
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
    return jsonify(_poll_response_body(cp_body, normalized)), status


def _not_found() -> tuple[Response, int]:
    # No auth, no CP call, no API-key forwarding for unknown /api/* routes.
    return jsonify({"error": "not found", "code": "not_found"}), HTTP_NOT_FOUND


def _api_router(deps: AppDeps, subpath: str) -> tuple[Response, int]:
    method = request.method
    if _is_upload(method, subpath):
        return _handle_upload(deps)
    target = _submit_target(method, subpath)
    if target is not None:
        return _handle_submit(deps, target)
    execution_id = _poll_id(method, subpath)
    if execution_id is not None:
        return _handle_poll(deps, execution_id)
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
    deps: AppDeps | None = None, *, enable_supertokens: bool = True, auth_decorator=None
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
        return _api_router(deps, subpath)

    @app.errorhandler(HttpError)
    def _on_http_error(err: HttpError):
        return jsonify({"error": err.message, "code": err.code}), err.status

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8899")))


if __name__ == "__main__":  # pragma: no cover
    main()
