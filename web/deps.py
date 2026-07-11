"""Dependency seams for the reel-af-ui backend (auth + ownership boundary).

The UI service is the tenancy boundary: it verifies the SuperTokens session,
resolves server-trusted ownership, stamps a ``deepresearch.reel_job`` row, and
only then dispatches an identity-free body to the control plane.

This module defines the *ports* (typed protocols) and the ``AppDeps`` container
so route handlers depend on interfaces, not concrete DB/network clients. Tests
inject fakes for every port; ``default_deps()`` builds import-safe real adapters
that perform **no** I/O at construction time (B0.1 / B1).

Unwired ports fail closed: until SuperTokens and the user-data DB are wired, the
default identity/repository/upload ports raise, so the service denies rather
than leaks.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable

Role = Literal["owner", "admin", "member", "viewer"]

# App action name (plan §1). Maps to the canonical DB permission key
# ``role_definition.permissions.can_create_reel`` — never invent new perms here.
ACTION_CREATE_REEL = "reel:create"
PERMISSION_CREATE_REEL = "can_create_reel"


# ─────────────────────────── typed errors ───────────────────────────
# Each carries the HTTP status the route layer must return. Ordering/precedence
# of these is asserted by the behavior tests (plan §9).


class HttpError(Exception):
    """Base for errors that map to a specific HTTP status with no CP call."""

    status: int = 500
    code: str = "error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code


class BadRequest(HttpError):
    status, code = 400, "bad_request"


class Unauthorized(HttpError):
    status, code = 401, "unauthenticated"


class Forbidden(HttpError):
    status, code = 403, "forbidden"


class Denied(Forbidden):
    """Authorization denial (fail-closed permission check)."""

    code = "denied"


class NotFound(HttpError):
    status, code = 404, "not_found"


class Conflict(HttpError):
    status, code = 409, "conflict"


class PayloadTooLarge(HttpError):
    status, code = 413, "payload_too_large"


class BadGateway(HttpError):
    status, code = 502, "bad_gateway"


class SchemaUnavailable(HttpError):
    status, code = 503, "schema_unavailable"


class RepositoryUnavailable(HttpError):
    status, code = 503, "service_unavailable"


# ─────────────────────────── data ───────────────────────────


@dataclass(frozen=True)
class AuthContext:
    """Server-trusted identity. Never sourced from the request body (P0 §3.1)."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    supertokens_user_id: str


# ─────────────────────────── ports ───────────────────────────


@runtime_checkable
class IdentityProvider(Protocol):
    """Verifies the session and resolves ``AuthContext`` from live DB state."""

    def resolve(self, request) -> AuthContext:  # raises Unauthorized/Forbidden/503
        ...


@runtime_checkable
class AccessGuardPort(Protocol):
    def authorize_create(self, ctx: AuthContext) -> None:  # raises Denied
        ...

    def authorize_reel_read(self, ctx: AuthContext, job) -> None:  # raises Denied/NotFound
        ...


@runtime_checkable
class ReelJobRepoPort(Protocol):
    def ensure_ready(self) -> None: ...
    def insert_or_get_queued(self, ctx, submission, job_id, now, client_request_id): ...
    def attach_execution_id(self, ctx, job_id, execution_id): ...
    def get_by_execution(self, ctx, execution_id): ...
    def mark_failed(self, ctx, job_id, reason, completed_at): ...
    def update_from_execution(self, ctx, execution_id, status, result_ref, completed_at): ...
    def mark_stale_queued(self, now) -> int: ...
    # research_run persistence (Plan 4, ISC-24); consumed by Plans 5/6 — do not redefine.
    def insert_research_run(self, ctx, run_id, execution_id, status, now): ...
    def update_research_status(self, ctx, run_id, status=None, execution_id=None): ...
    def get_research_run(self, ctx, run_id): ...
    def get_research_by_execution(self, ctx, execution_id): ...


@runtime_checkable
class CarouselRepoPort(Protocol):
    def ensure_ready(self) -> None: ...
    def insert_or_get_draft(self, ctx, create, carousel_id, now, client_request_id): ...
    def attach_execution_id(self, ctx, carousel_id, execution_id): ...
    def get(self, ctx, carousel_id): ...
    def slide_ref(self, ctx, carousel_id, slide_idx) -> str: ...
    def replace_slide(self, ctx, carousel_id, slide_idx, ref, prompt, status): ...
    def set_status(self, ctx, carousel_id, status): ...
    def draft_slide_refs(self, ctx, carousel_id) -> list[str]: ...
    def register_hq_recreate(self, ctx, carousel_id) -> int: ...


@runtime_checkable
class UploadStorePort(Protocol):
    def ensure_ready(self) -> None: ...
    def store(self, ctx: AuthContext, file_storage) -> dict: ...
    def presign(self, ctx: AuthContext, handle: str) -> str: ...  # ctx-owned handle → fetchable url (T7/Phase0)


@runtime_checkable
class ControlPlanePort(Protocol):
    def dispatch_async(self, target: str, body: dict) -> tuple[int, dict, dict]: ...
    def get_execution(self, execution_id: str) -> tuple[int, dict, dict]: ...


@runtime_checkable
class StoragePort(Protocol):
    """Object-storage media seam (P0). Real adapter is ``storage.ObjectStorage``;
    tests inject an in-memory ``FakeStorage``. Plans 1/6 consume this port."""

    # put: idempotent-addressed — same (org_id, key) always returns the same
    #   org-prefixed ref (``<org_id>/<key>``). last-write-wins on bytes.
    def put(self, org_id, key: str, data) -> str: ...
    # presigned_url: ttl None -> REEL_PRESIGN_TTL_S (default 3600). blank ref -> BadRequest(400).
    def presigned_url(self, ref: str, ttl: int | None = None) -> str: ...
    # exists: True IFF the object is present; boundary errors collapse to False (never raise).
    def exists(self, ref: str) -> bool: ...
    # NOTE: `delete(ref)` is intentionally absent — Plan 6 forward-extension (cleanup).


@runtime_checkable
class SlideRefResolverPort(Protocol):
    """Resolves a carousel slide to its stored media ref, org-scoped. Plan 6 provides
    the real (carousel-backed) resolver; here it is a port + fake. Returns the stored
    ref for ``(carousel_id, slide_idx)`` IFF it belongs to ``ctx.org_id``; raises
    ``NotFound`` (404) to conceal cross-org / absent, mirroring ``authorize_reel_read``."""

    def resolve(self, ctx: AuthContext, carousel_id: str, slide_idx: int) -> str: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class UuidFactory(Protocol):
    def __call__(self) -> uuid.UUID: ...


# ─────────────────────────── real, import-safe adapters ───────────────────────────


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class RoleAccessGuard:
    """Pure authorization logic (no I/O). Role→permission comes from the resolved
    context; the identity resolver is responsible for loading role_definition."""

    def authorize_create(self, ctx: AuthContext) -> None:
        if ctx.role not in ("owner", "admin", "member"):
            raise Denied(f"role {ctx.role!r} cannot create reels")

    def authorize_reel_read(self, ctx: AuthContext, job) -> None:
        if getattr(job, "org_id", None) != ctx.org_id:
            raise NotFound("job not found")  # conceal cross-org existence
        if job.created_by == ctx.user_id or ctx.role in ("owner", "admin"):
            return
        raise Denied("not permitted to read this reel job")


class _Unconfigured:
    """Fail-closed placeholder for ports that need external wiring not yet done."""

    def __init__(self, error: type[HttpError], message: str) -> None:
        self._error, self._message = error, message

    def _fail(self, *_args, **_kwargs):
        raise self._error(self._message)

    def __getattr__(self, _name):
        return self._fail


# ─────────────────────────── container ───────────────────────────


@dataclass
class AppDeps:
    identity: IdentityProvider
    access_guard: AccessGuardPort
    reel_jobs: ReelJobRepoPort
    carousels: CarouselRepoPort
    uploads: UploadStorePort
    control_plane: ControlPlanePort
    storage: StoragePort
    slides: SlideRefResolverPort
    clock: Clock
    uuid_factory: UuidFactory
    logger: logging.Logger


def default_deps() -> AppDeps:
    """Build real, import-safe deps. No DB connection or network call here — the
    adapters are constructed lazily and only touch the DB/CP when a request path
    calls them, so import and app construction stay side-effect-free (B1).

    Identity + repo point at the SHARED user-data Postgres and fail closed
    (503) until the root ``migrations/deepresearch`` schema is applied and the
    SuperTokens recipe is wired. Upload storage stays unconfigured until B8.
    """
    # Lazy imports avoid an import cycle (pg/auth/control_plane import this module).
    from carousels import CarouselSlideRefResolver
    from control_plane import HttpControlPlane
    from pg import PgCarouselRepo, PgReelJobRepo, build_identity
    from storage import ObjectStorage
    from uploads import BucketUploadStore, LocalUploadStore

    logger = logging.getLogger("reel_af_ui")
    # T7: prefer shared object storage (reachable by the reel-af node) when a
    # bucket is configured; else fall back to the local volume store (dev). Both
    # fail closed (503) until their backing store is configured.
    uploads = BucketUploadStore() if os.getenv("REEL_BUCKET_NAME") else LocalUploadStore()
    carousels = PgCarouselRepo()
    return AppDeps(
        identity=build_identity(),                 # SuperTokens session (fail-closed) + DB reader
        access_guard=RoleAccessGuard(),
        reel_jobs=PgReelJobRepo(),                  # shared deepresearch DB; 503 until applied
        carousels=carousels,                        # carousel read-model; 503 until applied
        uploads=uploads,                            # bucket (prod) or local volume (dev); 503 until configured
        control_plane=HttpControlPlane(),
        storage=ObjectStorage(),                    # media object store; 503 until REEL_BUCKET_NAME configured
        slides=CarouselSlideRefResolver(carousels),
        clock=SystemClock(),
        uuid_factory=lambda: uuid.uuid4(),
        logger=logger,
    )


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)
