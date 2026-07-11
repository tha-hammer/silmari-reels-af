"""Fake-first test harness for the reel-af-ui backend (plan §7).

Puts ``web/`` on ``sys.path`` (the service runs as ``server:app`` with ``web/``
as its working dir, so modules import each other by bare name), and provides
fakes for every external port so route behavior is testable without SuperTokens,
Postgres, or the control plane.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest

WEB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web"))
if WEB not in sys.path:
    sys.path.insert(0, WEB)

from deps import (  # noqa: E402
    AppDeps,
    AuthContext,
    NotFound,
    RoleAccessGuard,
)
from reel_jobs import ReelJobRef  # noqa: E402

FIXED_JOB_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
# Foreign tenancy for cross-org concealment tests (Plan 4, CI-2).
OTHER_ORG = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_USER = uuid.UUID("44444444-4444-4444-4444-444444444444")


def make_ctx(role: str = "member") -> AuthContext:
    return AuthContext(user_id=USER_ID, org_id=ORG_ID, role=role, supertokens_user_id="st-user-1")


class FakeIdentity:
    def __init__(self, ctx: AuthContext | None = None, error: Exception | None = None):
        self._ctx, self._error = ctx, error
        self.calls = 0

    def resolve(self, _request) -> AuthContext:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._ctx is not None
        return self._ctx


class FakeReelJobRepo:
    def __init__(self, job: ReelJobRef | None = None, get_error: Exception | None = None):
        self.inserted: list = []
        self.attached: list = []
        self.failed: list = []
        self.updates: list = []
        self._job = job
        self._get_error = get_error
        self._by_key: dict = {}          # (org_id, created_by, crid) -> ReelJobRef (idempotency)
        self._attach_error: Exception | None = None

    def ensure_ready(self) -> None:
        pass

    def insert_or_get_queued(self, ctx, submission, job_id, now, client_request_id) -> ReelJobRef:
        key = (ctx.org_id, ctx.user_id, client_request_id)
        if key in self._by_key:
            existing = self._by_key[key]
            return ReelJobRef(
                job_id=existing.job_id, org_id=existing.org_id, created_by=existing.created_by,
                status=existing.status, execution_id=existing.execution_id, created=False,
            )
        self.inserted.append((ctx, submission, job_id, now, client_request_id))
        ref = ReelJobRef(job_id=job_id, org_id=ctx.org_id, created_by=ctx.user_id, status="queued")
        self._by_key[key] = ref
        return ref

    def set_existing(self, key, ref: ReelJobRef) -> None:
        self._by_key[key] = ref

    def attach_execution_id(self, ctx, job_id, execution_id) -> ReelJobRef:
        if self._attach_error is not None:
            raise self._attach_error
        self.attached.append((ctx, job_id, execution_id))
        for k, ref in list(self._by_key.items()):
            if ref.job_id == job_id:
                self._by_key[k] = ReelJobRef(
                    job_id=ref.job_id, org_id=ref.org_id, created_by=ref.created_by,
                    status=ref.status, execution_id=execution_id, created=False,
                )
        return ReelJobRef(job_id=job_id, org_id=ctx.org_id, created_by=ctx.user_id,
                          status="queued", execution_id=execution_id)

    def get_by_execution(self, ctx, execution_id):
        if self._get_error is not None:
            raise self._get_error
        if self._job is None:
            raise NotFound("job not found")
        return self._job

    def mark_failed(self, ctx, job_id, reason, completed_at) -> None:
        self.failed.append((ctx, job_id, reason))

    def update_from_execution(self, ctx, execution_id, status, result_ref, completed_at):
        self.updates.append((execution_id, status, result_ref, completed_at))
        return self._job

    def mark_stale_queued(self, now) -> int:
        return 0


class FakeUploadStore:
    def __init__(
        self,
        handle: dict | None = None,
        error: Exception | None = None,
        presigned: str = "https://bucket.example/signed/object.mp4?sig=abc",
        presign_error: Exception | None = None,
    ):
        self._handle = handle or {"path": "uploads/fake.mp4"}
        self._error = error
        self._presigned = presigned
        self._presign_error = presign_error
        self.calls = 0
        self.presign_calls: list = []

    def ensure_ready(self) -> None:
        pass

    def store(self, ctx, file_storage) -> dict:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._handle

    def presign(self, ctx, handle: str) -> str:
        self.presign_calls.append((ctx.org_id, handle))
        if self._presign_error is not None:
            raise self._presign_error
        return self._presigned


class FakeStorage:
    """In-memory StoragePort for unit tests. Plans 1 and 6 reuse this in their tests."""

    def __init__(self, objects: dict | None = None):
        self._objects = dict(objects or {})
        self.presign_calls: list = []

    def put(self, org_id, key, data) -> str:
        ref = f"{org_id}/{key.lstrip('/')}"
        self._objects[ref] = data if isinstance(data, (bytes, bytearray)) else data.read()
        return ref

    def presigned_url(self, ref, ttl=None) -> str:
        self.presign_calls.append((ref, ttl))
        return self.presigned_for(ref)

    def presigned_for(self, ref) -> str:
        return f"https://fake-store/{ref}?sig=test"

    def exists(self, ref) -> bool:
        return ref in self._objects


class FakeSlideRefResolver:
    """Stub SlideRefResolverPort; real impl is Plan 6's carousel-backed resolver."""

    def __init__(self, refs: dict | None = None):  # {(org_id, cid, idx): ref}
        self._refs = dict(refs or {})

    def resolve(self, ctx, carousel_id, slide_idx) -> str:
        ref = self._refs.get((ctx.org_id, carousel_id, slide_idx))
        if ref is None:
            raise NotFound("slide not found")  # conceal cross-org + absent (404)
        return ref


class FakeCarouselRepo:
    """In-memory CarouselRepoPort. Org-scoped; absent/foreign rows are concealed."""

    def __init__(self):
        self._rows: dict = {}
        self.inserted: list = []
        self.replaced: list = []
        self._by_key: dict = {}

    def ensure_ready(self) -> None:
        pass

    def seed(self, org, cid, *, status="draft", slides=None):
        self._rows[(org, cid)] = {
            "status": status,
            "slides": list(slides or []),
            "execution_id": None,
            "hq_recreate_count": 0,
        }

    def _own(self, ctx, cid):
        row = self._rows.get((ctx.org_id, cid))
        if row is None:
            raise NotFound("carousel not found")
        return row

    def insert_or_get_draft(self, ctx, create, carousel_id, now, client_request_id):
        key = (ctx.org_id, ctx.user_id, client_request_id)
        if key in self._by_key:
            cid = self._by_key[key]
            row = self._rows[(ctx.org_id, cid)]
            return ReelJobRef(
                job_id=cid,
                org_id=ctx.org_id,
                created_by=ctx.user_id,
                status=row["status"],
                execution_id=row["execution_id"],
                created=False,
            )
        self.inserted.append((ctx, create, carousel_id, now, client_request_id))
        self.seed(ctx.org_id, carousel_id)
        self._by_key[key] = carousel_id
        return ReelJobRef(
            job_id=carousel_id,
            org_id=ctx.org_id,
            created_by=ctx.user_id,
            status="draft",
            created=True,
        )

    def attach_execution_id(self, ctx, carousel_id, execution_id):
        row = self._own(ctx, carousel_id)
        row["execution_id"] = execution_id
        return ReelJobRef(
            job_id=carousel_id,
            org_id=ctx.org_id,
            created_by=ctx.user_id,
            status=row["status"],
            execution_id=execution_id,
        )

    def get(self, ctx, carousel_id):
        from types import SimpleNamespace

        row = self._own(ctx, carousel_id)
        return SimpleNamespace(status=row["status"], slides=list(row["slides"]))

    def slide_ref(self, ctx, carousel_id, slide_idx) -> str:
        row = self._own(ctx, carousel_id)
        for slide in row["slides"]:
            if slide.get("idx") == slide_idx and slide.get("image_ref"):
                return slide["image_ref"]
        raise NotFound("slide not found")

    def replace_slide(self, ctx, carousel_id, slide_idx, ref, prompt, status):
        row = self._own(ctx, carousel_id)
        row["slides"] = [
            {"idx": slide_idx, "image_ref": ref, "prompt": prompt, "status": status}
            if slide.get("idx") == slide_idx
            else slide
            for slide in row["slides"]
        ]
        self.replaced.append((ctx.org_id, carousel_id, slide_idx))

    def set_status(self, ctx, carousel_id, status):
        row = self._own(ctx, carousel_id)
        if row["status"] not in ("succeeded", "failed", "cancelled"):
            row["status"] = status

    def draft_slide_refs(self, ctx, carousel_id) -> list[str]:
        row = self._own(ctx, carousel_id)
        return [slide["image_ref"] for slide in row["slides"] if slide.get("image_ref")]

    def register_hq_recreate(self, ctx, carousel_id) -> int:
        row = self._own(ctx, carousel_id)
        row["hq_recreate_count"] += 1
        return row["hq_recreate_count"]


class FakeControlPlane:
    def __init__(self, response=(202, {"execution_id": "exec_123"}, {}), error=None):
        self._response, self._error = response, error
        self.dispatch_calls: list = []
        self.get_calls: list = []

    def dispatch_async(self, target, body):
        self.dispatch_calls.append((target, body))
        if self._error is not None:
            raise self._error
        return self._response

    def get_execution(self, execution_id):
        self.get_calls.append(execution_id)
        return self._response


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def make_deps(
    *,
    identity: FakeIdentity | None = None,
    reel_jobs: FakeReelJobRepo | None = None,
    uploads: FakeUploadStore | None = None,
    control_plane: FakeControlPlane | None = None,
    carousels: FakeCarouselRepo | None = None,
    storage: FakeStorage | None = None,
    slides: FakeSlideRefResolver | None = None,
) -> AppDeps:
    return AppDeps(
        identity=identity or FakeIdentity(make_ctx()),
        access_guard=RoleAccessGuard(),
        reel_jobs=reel_jobs or FakeReelJobRepo(),
        carousels=carousels or FakeCarouselRepo(),
        uploads=uploads or FakeUploadStore(),
        control_plane=control_plane or FakeControlPlane(),
        storage=storage or FakeStorage(),
        slides=slides or FakeSlideRefResolver(),
        clock=FixedClock(),
        uuid_factory=lambda: FIXED_JOB_ID,
        logger=logging.getLogger("test.reel_af_ui"),
    )


@pytest.fixture
def deps_factory():
    return make_deps
