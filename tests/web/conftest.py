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

    def presign(self, handle: str) -> str:
        self.presign_calls.append(handle)
        if self._presign_error is not None:
            raise self._presign_error
        return self._presigned


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
) -> AppDeps:
    return AppDeps(
        identity=identity or FakeIdentity(make_ctx()),
        access_guard=RoleAccessGuard(),
        reel_jobs=reel_jobs or FakeReelJobRepo(),
        uploads=uploads or FakeUploadStore(),
        control_plane=control_plane or FakeControlPlane(),
        clock=FixedClock(),
        uuid_factory=lambda: FIXED_JOB_ID,
        logger=logging.getLogger("test.reel_af_ui"),
    )


@pytest.fixture
def deps_factory():
    return make_deps
