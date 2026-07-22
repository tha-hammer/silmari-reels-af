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


@dataclass(frozen=True)
class LocalRun:
    """reel-af's OWN local ``research_run`` row resolved from a foreign ``execution_id``
    (INT-02 C-Correlation). ``id`` is the local UUID stamped as provenance (never the
    text ``execution_id`` — C-Own); ``org_id`` is the sole tenant authority for the
    background write (no request ctx — C-Own tenancy)."""

    id: uuid.UUID
    org_id: uuid.UUID
    created_by: uuid.UUID


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
    # INT-04 lineage (read-only, reel-af-OWNED table): forward read-by-id + reverse provenance.
    def get(self, ctx, job_id): ...
    def reel_jobs_by_source_run(self, ctx, run_id): ...
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
    # INT-04 lineage (read-only, reel-af-OWNED table): reverse provenance lookup.
    def carousels_by_source_run(self, ctx, run_id): ...
    def slide_ref(self, ctx, carousel_id, slide_idx) -> str: ...
    def replace_slide(self, ctx, carousel_id, slide_idx, ref, prompt, status): ...
    def set_status(self, ctx, carousel_id, status): ...
    def draft_slide_refs(self, ctx, carousel_id) -> list[str]: ...
    def register_hq_recreate(self, ctx, carousel_id) -> int: ...
    def hq_recreate_count(self, ctx, carousel_id) -> int: ...


@runtime_checkable
class UploadStorePort(Protocol):
    def ensure_ready(self) -> None: ...
    def store(self, ctx: AuthContext, file_storage) -> dict: ...
    def presign(self, ctx: AuthContext, handle: str) -> str: ...  # ctx-owned handle → fetchable url (T7/Phase0)


@runtime_checkable
class ControlPlanePort(Protocol):
    def dispatch_async(self, target: str, body: dict) -> tuple[int, dict, dict]: ...
    def get_execution(self, execution_id: str) -> tuple[int, dict, dict]: ...


# ── INT-02 durable-cursor consumer ports (background-safe; NO request ctx) ──
# The hand-off rides the DURABLE bus only (A1); these ports abstract the durable
# read surface + reel-af's OWN dedup/cursor/resolution/stamp store. They are wired
# lazily in ``default_deps`` (no I/O at construction) so a background driver can use
# them off the request path.


@runtime_checkable
class EventReaderPort(Protocol):
    """Durable, cursor-based read of ``research.completed`` from the shipped ``/events``
    surface (``last_event_sequence``). NEVER the in-memory ``GlobalExecutionEventBus``
    (at-most-once/drop-on-full — A1). Returns CloudEvent records (each with a monotonic
    ``sequence``) having ``sequence > cursor`` and ``type == event_type``, in Seq order."""

    def read_since(self, cursor: int, event_type: str, limit: int) -> list: ...


@runtime_checkable
class ProcessedMessagesPort(Protocol):
    """reel-af's OWN dedup + atomic effect store, keyed on the CloudEvents ``id``.

    ``stamp_dedup_advance`` is the C5 one-transaction effect: insert
    ``processed_messages(id)`` ON CONFLICT DO NOTHING, (if fresh) resolve + idempotently
    stamp ``source_research_run_id`` = the resolved LOCAL UUID scoped to the resolved
    ``org_id``, and advance the cursor — all-or-nothing. Fusing these on one adapter is
    required by C5: three independent ports could not share one transaction."""

    def already_processed(self, cloudevents_id: str) -> bool: ...
    def mark(self, cloudevents_id: str, execution_id: str | None) -> None: ...
    def stamp_dedup_advance(self, event: dict, consumer: str): ...  # -> ConsumeResult (one TX)


@runtime_checkable
class EventCursorPort(Protocol):
    """reel-af's OWN per-consumer durable cursor (``event_cursor``). ``advance`` is used
    for the deduped/malformed/filtered fast paths (no stamp); the fresh-event advance
    happens INSIDE ``stamp_dedup_advance`` so it commits with the effect (C-AtLeastOnce)."""

    def get(self, consumer: str) -> int: ...
    def advance(self, consumer: str, seq: int) -> None: ...


@runtime_checkable
class LocalRunResolverPort(Protocol):
    """Resolve a foreign ``execution_id`` to reel-af's LOCAL ``research_run`` row, the
    UUID stamped as provenance and the tenant boundary for the background write."""

    def resolve(self, execution_id: str) -> LocalRun | None: ...


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
    # delete: idempotently remove a stored ref; blank ref -> BadRequest, unconfigured -> 503.
    def delete(self, ref: str) -> None: ...


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


@runtime_checkable
class ResearchRunReaderPort(Protocol):
    """Reads deep-research run detail through the OWNER's interface, keyed by
    ``execution_id`` (API Composition — INT Phase 0). reel-af never SQL-reads the
    owner table ``deepresearch.research_run``; it references foreign data by-id."""

    def read(self, ctx, execution_id: str) -> dict: ...   # owner interface, keyed by execution_id


@runtime_checkable
class SourceAssetRepoPort(Protocol):
    """Durable upload records (AF-02f): org-scoped writes/reads of
    ``deepresearch.source_asset``. Identity stamps are server-derived from the
    resolved ctx, never client-supplied."""

    def create(self, ctx, *, asset_id, bucket_key, original_filename,
               content_type, size_bytes, checksum, now): ...

    def list_for_org(self, ctx): ...

    def get(self, ctx, asset_id): ...   # org-scoped; foreign/absent concealed as 404


@runtime_checkable
class ProjectRepoPort(Protocol):
    """Org-scoped, owner-stamped project CRUD (AF-4pz.4). Foreign/absent/
    soft-deleted projects concealed as 404."""

    def create(self, ctx, *, project_id, name, description, now): ...

    def list_for_org(self, ctx): ...

    def get(self, ctx, project_id): ...

    def update(self, ctx, project_id, *, name=None, description=None, now=None): ...

    def soft_delete(self, ctx, project_id, *, now=None): ...


@runtime_checkable
class ProjectAssetRepoPort(Protocol):
    """Project-scoped asset attachments (AF-4pz.5) — exactly one of
    {source_asset_id, bucket_key, url} per row (migration 115 constraint)."""

    def add(self, ctx, *, asset_id, project_id, asset_type, source_asset_id,
            bucket_key, url, title, now): ...

    def list_for_project(self, ctx, project_id): ...

    def soft_delete(self, ctx, project_id, asset_id, *, now=None): ...


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
    research_reader: ResearchRunReaderPort
    # INT-02 durable-cursor consumer ports (background-safe; no request ctx).
    events: EventReaderPort
    processed: ProcessedMessagesPort
    cursor: EventCursorPort
    local_runs: LocalRunResolverPort
    clock: Clock
    uuid_factory: UuidFactory
    logger: logging.Logger
    # INT-04: optional, read-only lineage view — self-composed over the org-scoped repos above.
    # Wired post-construction (it references this container); ``None`` until wired.
    lineage: "object | None" = None
    # AF-02f: durable upload records. Defaulted for construction-site compatibility;
    # ``default_deps``/test ``make_deps`` always wire a real/fake repo.
    source_assets: "SourceAssetRepoPort | None" = None
    # AF-4pz.4/.5: Projects + attached assets. Same wiring convention.
    projects: "ProjectRepoPort | None" = None
    project_assets: "ProjectAssetRepoPort | None" = None


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
    from pg import (
        PgCarouselRepo,
        PgEventConsumerStore,
        PgProjectAssetRepo,
        PgProjectRepo,
        PgReelJobRepo,
        PgSourceAssetRepo,
        build_identity,
    )
    from research_reader import OwnerInterfaceResearchRunReader
    from storage import ObjectStorage
    from uploads import BucketUploadStore, LocalUploadStore

    logger = logging.getLogger("reel_af_ui")
    # T7: prefer shared object storage (reachable by the reel-af node) when a
    # bucket is configured; else fall back to the local volume store (dev). Both
    # fail closed (503) until their backing store is configured.
    uploads = BucketUploadStore() if os.getenv("REEL_BUCKET_NAME") else LocalUploadStore()
    carousels = PgCarouselRepo()
    control_plane = HttpControlPlane()              # identity-free client, reused by the reader
    # INT-02: reel-af's OWN dedup/cursor/resolve/stamp store (one adapter, one tx for the
    # C5 effect). The event READER transport (durable /events poll vs SSE) stays an Open-Seam
    # behind EventReaderPort — fail-closed until wired, so the opt-in driver denies not leaks.
    consumer_store = PgEventConsumerStore()
    deps = AppDeps(
        identity=build_identity(),                 # SuperTokens session (fail-closed) + DB reader
        access_guard=RoleAccessGuard(),
        reel_jobs=PgReelJobRepo(),                  # shared deepresearch DB; 503 until applied
        carousels=carousels,                        # carousel read-model; 503 until applied
        uploads=uploads,                            # bucket (prod) or local volume (dev); 503 until configured
        control_plane=control_plane,
        storage=ObjectStorage(),                    # media object store; 503 until REEL_BUCKET_NAME configured
        slides=CarouselSlideRefResolver(carousels),
        # INT Phase 0: read owner run detail via the owner interface, never local SQL.
        research_reader=OwnerInterfaceResearchRunReader(control_plane),
        # INT-02 consumer: fail-closed reader transport (Open-Seam) + reel-af's own store.
        events=_Unconfigured(
            RepositoryUnavailable, "event reader transport not wired (INT-02 Open-Seam)"
        ),
        processed=consumer_store,
        cursor=consumer_store,
        local_runs=consumer_store,
        clock=SystemClock(),
        uuid_factory=lambda: uuid.uuid4(),
        logger=logger,
        source_assets=PgSourceAssetRepo(),          # AF-02f: shared deepresearch DB; 503 until applied
        projects=PgProjectRepo(),                   # AF-4pz.4: 503 until migration 115 applied
        project_assets=PgProjectAssetRepo(),        # AF-4pz.5: 503 until migration 115 applied
    )
    # INT-04: the read-only lineage view is self-composed over the org-scoped repos above
    # (no new external client, no I/O at construction).
    from lineage import LineageView

    deps.lineage = LineageView(deps)
    return deps


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)
