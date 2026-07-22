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
    BadRequest,
    LocalRun,
    NotFound,
    RoleAccessGuard,
)
from events import ConsumeResult  # noqa: E402
from reel_jobs import ReelJobRef, ResearchRunRef  # noqa: E402

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


class FakeMembershipReader:
    """In-memory ``MembershipReader`` seeded with SuperTokens id → identity, so the
    REAL ``ResolverIdentity`` can be driven end-to-end without Postgres. Injected
    one level BELOW ``deps.identity`` (unlike ``FakeIdentity``) to exercise the
    session-provider seam. ``ensure_ready`` is a no-op; an unseeded id resolves to
    ``None`` (→ ``Forbidden``), never synthesized."""

    def __init__(self, seed: dict | None = None):
        # supertokens_user_id -> (user_id, org_id, role)
        self._seed = dict(seed or {})
        self.calls: list = []

    def ensure_ready(self) -> None:
        pass

    def resolve_active(self, supertokens_user_id, email, claimed_org_id):
        self.calls.append((supertokens_user_id, email, claimed_org_id))
        return self._seed.get(supertokens_user_id)


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
        # Plan 4 (CI-2): research_run store + execution_id-keyed job index so the
        # provenance read-back (get_by_execution) genuinely threads the column.
        self.research_runs: dict = {}    # research_run_id -> ResearchRunRef
        self._runs_by_exec: dict = {}    # execution_id -> research_run_id
        self._jobs_by_exec: dict = {}    # execution_id -> ReelJobRef
        self._jobs_by_id: dict = {}      # INT-04 lineage: job_id -> ReelJobRef (read-by-id + reverse)

    def ensure_ready(self) -> None:
        pass

    def insert_or_get_queued(self, ctx, submission, job_id, now, client_request_id) -> ReelJobRef:
        key = (ctx.org_id, ctx.user_id, client_request_id)
        if key in self._by_key:
            existing = self._by_key[key]
            return ReelJobRef(
                job_id=existing.job_id, org_id=existing.org_id, created_by=existing.created_by,
                status=existing.status, execution_id=existing.execution_id,
                source_research_run_id=existing.source_research_run_id, created=False,
            )
        self.inserted.append((ctx, submission, job_id, now, client_request_id))
        # Thread provenance from the submission (Plan 4) so read-back surfaces it.
        srr = getattr(submission, "source_research_run_id", None)
        ref = ReelJobRef(job_id=job_id, org_id=ctx.org_id, created_by=ctx.user_id,
                         status="queued", source_research_run_id=srr)
        self._by_key[key] = ref
        return ref

    def set_existing(self, key, ref: ReelJobRef) -> None:
        self._by_key[key] = ref

    def attach_execution_id(self, ctx, job_id, execution_id) -> ReelJobRef:
        if self._attach_error is not None:
            raise self._attach_error
        self.attached.append((ctx, job_id, execution_id))
        attached_ref = None
        for k, ref in list(self._by_key.items()):
            if ref.job_id == job_id:
                attached_ref = ReelJobRef(
                    job_id=ref.job_id, org_id=ref.org_id, created_by=ref.created_by,
                    status=ref.status, execution_id=execution_id,
                    source_research_run_id=ref.source_research_run_id, created=False,
                )
                self._by_key[k] = attached_ref
        if attached_ref is None:
            attached_ref = ReelJobRef(job_id=job_id, org_id=ctx.org_id, created_by=ctx.user_id,
                                      status="queued", execution_id=execution_id)
        self._jobs_by_exec[execution_id] = attached_ref  # exec-keyed read path (Plan 4)
        return attached_ref

    def get_by_execution(self, ctx, execution_id):
        if self._get_error is not None:
            raise self._get_error
        job = self._jobs_by_exec.get(execution_id) or self._job
        if job is None or job.org_id != ctx.org_id:
            raise NotFound("job not found")
        return job                                          # carries source_research_run_id

    def mark_failed(self, ctx, job_id, reason, completed_at) -> None:
        self.failed.append((ctx, job_id, reason))

    def update_from_execution(self, ctx, execution_id, status, result_ref, completed_at):
        self.updates.append((execution_id, status, result_ref, completed_at))
        return self._job

    def mark_stale_queued(self, now) -> int:
        return 0

    # ─────────── research_run store (Plan 4, ISC-24; mirrors PgReelJobRepo) ───────────

    def _record_event(self, name: str) -> None:
        events = getattr(self, "events", None)
        if events is not None:
            events.append(name)

    def seed_research_run(self, execution_id, org_id, created_by, status="succeeded"):
        rid = uuid.uuid4()
        self.research_runs[rid] = ResearchRunRef(
            id=rid, org_id=org_id, created_by=created_by, status=status,
            execution_id=execution_id)
        if execution_id is not None:
            self._runs_by_exec[execution_id] = rid
        return rid                                          # tests OBSERVE via this id

    def insert_research_run(self, ctx, run_id, execution_id, status, now):
        self._record_event("insert_research_run")
        self.research_runs[run_id] = ResearchRunRef(
            id=run_id, org_id=ctx.org_id, created_by=ctx.user_id,
            status=status, execution_id=execution_id)
        if execution_id is not None:
            self._runs_by_exec[execution_id] = run_id

    def update_research_status(self, ctx, run_id, status=None, execution_id=None):
        self._record_event("update_research_status")
        r = self.research_runs.get(run_id)
        if r is None or r.org_id != ctx.org_id:
            return
        if r.status in ("succeeded", "failed", "cancelled"):   # terminal monotonicity
            return
        self.research_runs[run_id] = ResearchRunRef(
            id=r.id, org_id=r.org_id, created_by=r.created_by,
            status=status or r.status, execution_id=execution_id or r.execution_id)
        if execution_id is not None:
            self._runs_by_exec[execution_id] = run_id

    def get_research_run(self, ctx, run_id):
        r = self.research_runs.get(run_id)
        if r is None or r.org_id != ctx.org_id:
            raise NotFound("research run not found")        # conceal cross-org
        return r

    def get_research_by_execution(self, ctx, execution_id):
        rid = self._runs_by_exec.get(execution_id)
        r = self.research_runs.get(rid) if rid else None
        if r is None or r.org_id != ctx.org_id:
            raise NotFound("research run not found")        # conceal foreign/absent
        return r

    # ─────────────── INT-04 lineage: read-by-id + reverse provenance (own table) ───────────────

    def seed_reel_job(self, job_id, org_id, source_research_run_id=None,
                      created_by=None, status="queued") -> ReelJobRef:
        ref = ReelJobRef(job_id=job_id, org_id=org_id, created_by=created_by or USER_ID,
                         status=status, source_research_run_id=source_research_run_id)
        self._jobs_by_id[job_id] = ref
        return ref

    def get(self, ctx, job_id):
        ref = self._jobs_by_id.get(job_id)
        if ref is None or ref.org_id != ctx.org_id:
            raise NotFound("reel job not found")            # conceal cross-org/absent
        return ref

    def reel_jobs_by_source_run(self, ctx, run_id):
        return [r for r in self._jobs_by_id.values()
                if r.org_id == ctx.org_id and r.source_research_run_id == run_id]


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
        self.stored_bytes: list = []

    def ensure_ready(self) -> None:
        pass

    def store(self, ctx, file_storage) -> dict:
        self.calls += 1
        if self._error is not None:
            raise self._error
        # Contract-faithful to LocalUploadStore/BucketUploadStore: no file → the
        # canonical 400; the stored bytes are recorded so AF-02f can prove the
        # checksum pass left the stream intact.
        if file_storage is None or not getattr(file_storage, "filename", ""):
            raise BadRequest("no file in multipart field 'file'", code="no_file")
        self.stored_bytes.append(file_storage.stream.read())
        return self._handle

    def presign(self, ctx, handle: str) -> str:
        self.presign_calls.append((ctx.org_id, handle))
        if self._presign_error is not None:
            raise self._presign_error
        return self._presigned


class FakeSourceAssetRepo:
    """AF-02f: captures created source-asset records; serves a canned list."""

    def __init__(self, assets: list | None = None, create_error: Exception | None = None):
        self._assets = assets or []
        self._create_error = create_error
        self.created: list = []
        self.list_calls: list = []
        self.get_calls: list = []
        self.deleted: set = set()   # asset ids modeled as soft-deleted (AF-4pz.2)

    def create(self, ctx, *, asset_id, bucket_key, original_filename,
               content_type, size_bytes, checksum, now):
        if self._create_error is not None:
            raise self._create_error
        from source_assets import SourceAssetRef

        self.created.append({
            "ctx": ctx, "asset_id": asset_id, "bucket_key": bucket_key,
            "original_filename": original_filename, "content_type": content_type,
            "size_bytes": size_bytes, "checksum": checksum, "now": now,
        })
        return SourceAssetRef(
            asset_id=asset_id, org_id=ctx.org_id, created_by=ctx.user_id,
            bucket_key=bucket_key, original_filename=original_filename,
            content_type=content_type, size_bytes=size_bytes, checksum=checksum,
            status="stored", created_at=now,
        )

    def list_for_org(self, ctx):
        self.list_calls.append(ctx)
        return list(self._assets)

    def get(self, ctx, asset_id):
        """Org-scoped read-by-id; foreign/absent/soft-deleted concealed as 404
        (mirrors PgSourceAssetRepo.get)."""
        self.get_calls.append((ctx, asset_id))
        for asset in self._assets:
            if (
                str(asset.asset_id) == str(asset_id)
                and asset.org_id == ctx.org_id
                and str(asset.asset_id) not in self.deleted
            ):
                return asset
        raise NotFound("source asset not found", code="source_asset_not_found")


class FakeProjectRepo:
    """AF-4pz.4: org-scoped project CRUD fake (mirrors PgProjectRepo)."""

    def __init__(self, projects: list | None = None):
        self._projects = list(projects or [])
        self.created: list = []
        self.list_calls: list = []
        self.updated: list = []
        self.soft_deleted: list = []

    def create(self, ctx, *, project_id, name, description, now):
        from projects import ProjectRef

        self.created.append({
            "ctx": ctx, "project_id": project_id, "name": name,
            "description": description, "now": now,
        })
        ref = ProjectRef(
            project_id=project_id, org_id=ctx.org_id, created_by=ctx.user_id,
            name=name, description=description, created_at=now, updated_at=now,
        )
        self._projects.append(ref)
        return ref

    def list_for_org(self, ctx):
        self.list_calls.append(ctx)
        return [
            p for p in self._projects
            if p.org_id == ctx.org_id and p.project_id not in self.soft_deleted
        ]

    def get(self, ctx, project_id):
        for project in self._projects:
            if (
                str(project.project_id) == str(project_id)
                and project.org_id == ctx.org_id
                and project.project_id not in self.soft_deleted
            ):
                return project
        raise NotFound("project not found", code="project_not_found")

    def update(self, ctx, project_id, *, name=None, description=None, now=None):
        project = self.get(ctx, project_id)
        self.updated.append((ctx, project.project_id, name, description))
        updated = type(project)(
            project_id=project.project_id, org_id=project.org_id,
            created_by=project.created_by,
            name=name if name is not None else project.name,
            description=description if description is not None else project.description,
            created_at=project.created_at, updated_at=now,
        )
        self._projects = [
            updated if p.project_id == project.project_id else p for p in self._projects
        ]
        return updated

    def soft_delete(self, ctx, project_id, *, now=None):
        project = self.get(ctx, project_id)
        self.soft_deleted.append(project.project_id)


class FakeProjectAssetRepo:
    """AF-4pz.5: project asset fake (mirrors PgProjectAssetRepo)."""

    def __init__(self):
        self._assets: list = []
        self.added: list = []
        self.list_calls: list = []
        self.soft_deleted: list = []

    def add(self, ctx, *, asset_id, project_id, asset_type, source_asset_id,
            bucket_key, url, title, now):
        from projects import ProjectAssetRef

        self.added.append({
            "ctx": ctx, "asset_id": asset_id, "project_id": project_id,
            "asset_type": asset_type, "source_asset_id": source_asset_id,
            "bucket_key": bucket_key, "url": url, "title": title, "now": now,
        })
        ref = ProjectAssetRef(
            asset_id=asset_id, project_id=project_id, org_id=ctx.org_id,
            asset_type=asset_type, source_asset_id=source_asset_id,
            bucket_key=bucket_key, url=url, title=title, created_at=now,
        )
        self._assets.append(ref)
        return ref

    def list_for_project(self, ctx, project_id):
        self.list_calls.append((ctx, project_id))
        return [
            a for a in self._assets
            if str(a.project_id) == str(project_id)
            and a.org_id == ctx.org_id
            and a.asset_id not in self.soft_deleted
        ]

    def get(self, ctx, project_id, asset_id):
        for asset in self.list_for_project(ctx, project_id):
            if str(asset.asset_id) == str(asset_id):
                return asset
        raise NotFound("project asset not found", code="project_asset_not_found")

    def soft_delete(self, ctx, project_id, asset_id, *, now=None):
        for asset in self._assets:
            if (
                str(asset.asset_id) == str(asset_id)
                and str(asset.project_id) == str(project_id)
                and asset.org_id == ctx.org_id
                and asset.asset_id not in self.soft_deleted
            ):
                self.soft_deleted.append(asset.asset_id)
                return
        raise NotFound("project asset not found", code="project_asset_not_found")


class FakeStorage:
    """In-memory StoragePort for unit tests. Plans 1 and 6 reuse this in their tests."""

    def __init__(self, objects: dict | None = None):
        self._objects = dict(objects or {})
        self.presign_calls: list = []
        self.deleted: list = []

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

    def delete(self, ref) -> None:
        self.deleted.append(ref)
        self._objects.pop(ref, None)


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

    def __init__(self, hq_cap: int = 5):
        self._rows: dict = {}
        self.inserted: list = []
        self.replaced: list = []
        self._by_key: dict = {}
        self._hq_cap = hq_cap

    def ensure_ready(self) -> None:
        pass

    def seed(self, org, cid, *, status="draft", slides=None, source_research_run_id=None):
        self._rows[(org, cid)] = {
            "status": status,
            "slides": list(slides or []),
            "execution_id": None,
            "hq_recreate_count": 0,
            "source_research_run_id": source_research_run_id,   # INT-04 provenance FK
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
        return SimpleNamespace(
            status=row["status"],
            slides=list(row["slides"]),
            source_research_run_id=row.get("source_research_run_id"),   # INT-04 provenance
        )

    def carousels_by_source_run(self, ctx, run_id):
        # INT-04 reverse provenance lookup over reel-af's OWN carousel rows, org-scoped.
        return [
            ReelJobRef(job_id=cid, org_id=org, created_by=USER_ID, status=row["status"],
                       source_research_run_id=row.get("source_research_run_id"))
            for (org, cid), row in self._rows.items()
            if org == ctx.org_id and row.get("source_research_run_id") == run_id
        ]

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
        if row["hq_recreate_count"] >= self._hq_cap:
            from carousels import HqRecreateCapError

            raise HqRecreateCapError(f"HQ recreate cap reached for {carousel_id}")
        row["hq_recreate_count"] += 1
        return row["hq_recreate_count"]

    def register(self, carousel_id: str) -> None:
        self.register_hq_recreate(make_ctx(), carousel_id)

    def count(self, carousel_id: str) -> int:
        return self.hq_recreate_count(make_ctx(), carousel_id)

    def hq_recreate_count(self, ctx, carousel_id) -> int:
        return self._own(ctx, carousel_id)["hq_recreate_count"]


class FakeControlPlane:
    def __init__(self, response=(202, {"execution_id": "exec_123"}, {}), error=None, get_error=None):
        self._response, self._error, self._get_error = response, error, get_error
        self.dispatch_calls: list = []
        self.get_calls: list = []
        self.get_execution_calls: list = []          # INT Phase 0: owner-interface read spy

    def dispatch_async(self, target, body):
        self.dispatch_calls.append((target, body))
        if self._error is not None:
            raise self._error
        return self._response

    def get_execution(self, execution_id):
        if self._get_error is not None:              # owner unreachable → fail closed
            raise self._get_error
        self.get_calls.append(execution_id)
        self.get_execution_calls.append(execution_id)
        return self._response


class FakeResearchRunReader:
    """In-memory ResearchRunReaderPort for unit tests. Resolves by execution_id;
    fails closed (NotFound) for an unknown id — never synthesizes a row."""

    def __init__(self, details: dict | None = None):
        self._details = dict(details or {})          # execution_id -> detail dict
        self.read_calls: list = []

    def read(self, ctx, execution_id: str) -> dict:
        self.read_calls.append(execution_id)
        detail = self._details.get(execution_id)
        if detail is None:
            raise NotFound("research run not found")
        return detail


# ─────────────── INT-02 durable-cursor consumer fakes (B2/B4/B5) ───────────────


def make_event(seq, *, id="ce-1", subject="exec-1", type="com.silmari.research.completed.v1",
               research_prompt="prompt", **data):
    """Build a CloudEvent record as the durable read surface yields it (envelope +
    monotonic ``sequence``). ``subject`` is the execution_id (C-Correlation)."""
    payload = {"run_id": str(uuid.uuid4()), "status": "succeeded",
               "research_prompt": research_prompt, "research_document_id": subject}
    payload.update(data)
    return {"id": id, "type": type, "subject": subject, "sequence": seq,
            "time": "2026-07-12T18:00:00Z", "data": payload}


class _ConsumerState:
    """Shared in-memory backing store for the three consumer-store fakes, so the C5
    one-transaction effect (dedup insert + stamp + cursor advance) mutates ONE state
    all-or-nothing (a faithful single-tx simulation)."""

    def __init__(self, cursor_start=0):
        self.processed: set = set()               # CloudEvents ids
        self.processed_rows: list = []            # (id, execution_id)
        self.cursors: dict = {"reel-af": cursor_start}
        self.local_runs: dict = {}                # execution_id -> LocalRun
        self.reel_rows: dict = {}                 # (org_id, execution_id) -> {"source_research_run_id": ...}

    def seed_local_run(self, execution_id, org_id, created_by=None) -> LocalRun:
        run = LocalRun(id=uuid.uuid4(), org_id=org_id, created_by=created_by or USER_ID)
        self.local_runs[execution_id] = run
        return run

    def seed_reel_row(self, org_id, execution_id):
        self.reel_rows[(org_id, execution_id)] = {"source_research_run_id": None}

    def stamp_of(self, org_id, execution_id):
        row = self.reel_rows.get((org_id, execution_id))
        return row["source_research_run_id"] if row else None


class FakeEventReader:
    """Durable read-surface fake. Records are read by cursor+type filter, in Seq order.
    ``subscribed_bus`` stays False — a test asserts the consumer NEVER rides the in-memory
    ``GlobalExecutionEventBus`` (A1)."""

    def __init__(self, records=None):
        self.records = list(records or [])
        self.read_calls: list = []
        self.subscribed_bus = False

    def read_since(self, cursor, event_type, limit):
        self.read_calls.append((cursor, event_type, limit))
        out = [r for r in self.records
               if r.get("sequence", 0) > cursor and r.get("type") == event_type]
        out.sort(key=lambda r: r["sequence"])
        return out[:limit]


class FakeProcessedMessages:
    """Dedup + the C5 one-tx effect over the shared ``_ConsumerState``."""

    def __init__(self, state: _ConsumerState):
        self.state = state

    def already_processed(self, cloudevents_id) -> bool:
        return cloudevents_id in self.state.processed

    def mark(self, cloudevents_id, execution_id) -> None:
        if cloudevents_id not in self.state.processed:      # ON CONFLICT DO NOTHING
            self.state.processed.add(cloudevents_id)
            self.state.processed_rows.append((cloudevents_id, execution_id))

    def stamp_dedup_advance(self, event, consumer) -> ConsumeResult:
        seq, cid, execution_id = event["sequence"], event["id"], event["subject"]
        first_seen = cid not in self.state.processed
        local_run_found = False
        if first_seen:
            self.state.processed.add(cid)
            self.state.processed_rows.append((cid, execution_id))
            run = self.state.local_runs.get(execution_id)
            if run is not None:
                local_run_found = True
                row = self.state.reel_rows.get((run.org_id, execution_id))   # org-scoped
                if row is not None and row["source_research_run_id"] is None:  # null-guard
                    row["source_research_run_id"] = run.id                     # local UUID
        else:
            local_run_found = self.state.local_runs.get(execution_id) is not None
        self.state.cursors[consumer] = seq                  # advance in the SAME (fake) tx
        return ConsumeResult(first_seen=first_seen, local_run_found=local_run_found)


class FakeEventCursor:
    def __init__(self, state: _ConsumerState):
        self.state = state

    def get(self, consumer) -> int:
        return self.state.cursors.get(consumer, 0)

    def advance(self, consumer, seq) -> None:
        self.state.cursors[consumer] = seq


class FakeLocalRunResolver:
    def __init__(self, state: _ConsumerState):
        self.state = state

    def resolve(self, execution_id):
        return self.state.local_runs.get(execution_id)


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
    research_reader: FakeResearchRunReader | None = None,
    events: FakeEventReader | None = None,
    consumer_state: _ConsumerState | None = None,
    uuid_factory=None,
    source_assets: FakeSourceAssetRepo | None = None,
    projects: "FakeProjectRepo | None" = None,
    project_assets: "FakeProjectAssetRepo | None" = None,
) -> AppDeps:
    # INT-02: the three consumer-store fakes share ONE state so the C5 effect is atomic.
    state = consumer_state or _ConsumerState()
    deps = AppDeps(
        identity=identity or FakeIdentity(make_ctx()),
        access_guard=RoleAccessGuard(),
        reel_jobs=reel_jobs or FakeReelJobRepo(),
        carousels=carousels or FakeCarouselRepo(),
        uploads=uploads or FakeUploadStore(),
        control_plane=control_plane or FakeControlPlane(),
        storage=storage or FakeStorage(),
        slides=slides or FakeSlideRefResolver(),
        research_reader=research_reader or FakeResearchRunReader(),
        events=events or FakeEventReader(),
        processed=FakeProcessedMessages(state),
        cursor=FakeEventCursor(state),
        local_runs=FakeLocalRunResolver(state),
        clock=FixedClock(),
        uuid_factory=uuid_factory or (lambda: FIXED_JOB_ID),
        logger=logging.getLogger("test.reel_af_ui"),
        source_assets=source_assets or FakeSourceAssetRepo(),
        projects=projects or FakeProjectRepo(),
        project_assets=project_assets or FakeProjectAssetRepo(),
    )
    # INT-04: the lineage read model is self-composed over the same org-scoped repos.
    from lineage import LineageView  # noqa: E402 - lazy: avoids import cycle at module load

    deps.lineage = LineageView(deps)
    return deps


@pytest.fixture
def deps_factory():
    return make_deps
