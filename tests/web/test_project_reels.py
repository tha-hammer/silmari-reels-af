"""AF-8bk — durable project reels: reel_job.project_id wired end-to-end.

A submit may carry a top-level ``project_id`` (mirrors the ``research_run_id``
provenance pattern): UUID-validated, ownership-checked via the projects repo
(foreign/absent concealed as 404 BEFORE any row/CP), stamped server-side onto
the reel_job. ``GET /api/v1/projects/<id>/reels`` lists a project's jobs.
"""

from __future__ import annotations

import uuid

import server
from conftest import (
    FakeControlPlane,
    FakeIdentity,
    FakeProjectRepo,
    FakeReelJobRepo,
    FakeSourceAssetRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from deps import SchemaUnavailable
from projects import ProjectRef
from source_assets import SourceAssetRef

COMPOSITE_URL = "/api/v1/execute/async/reel-af.reel_composite_to_reel"
ORG_ID = make_ctx().org_id
USER_ID = make_ctx().user_id
PROJECT_ID = uuid.UUID("aaaa0000-0000-4000-8000-000000000001")
SOURCE_ASSET_ID = uuid.UUID("bbbb0000-0000-4000-8000-000000000002")


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _project():
    return ProjectRef(project_id=PROJECT_ID, org_id=ORG_ID, created_by=USER_ID, name="P")


def _source_asset():
    return SourceAssetRef(
        asset_id=SOURCE_ASSET_ID, org_id=ORG_ID, created_by=USER_ID,
        bucket_key=f"{ORG_ID}/clip.mp4", original_filename="clip.mp4",
        content_type="video/mp4", size_bytes=9, checksum="sha256:x", status="stored",
    )


def _deps(*, repo=None, projects=None):
    return make_deps(
        identity=FakeIdentity(make_ctx("member")),
        reel_jobs=repo or FakeReelJobRepo(),
        control_plane=FakeControlPlane(response=(202, {"execution_id": "exec_p"}, {})),
        uploads=FakeUploadStore(),
        source_assets=FakeSourceAssetRepo(assets=[_source_asset()]),
        projects=projects or FakeProjectRepo(projects=[_project()]),
    )


def _submit(deps, *, project_id=str(PROJECT_ID)):
    body = {
        "input": {"source_asset_id": str(SOURCE_ASSET_ID), "preset": "middle-third-dynamic"},
    }
    if project_id is not None:
        body["project_id"] = project_id
    return _client(deps).post(COMPOSITE_URL, json=body)


def test_submit_with_project_id_stamps_reel_job():
    repo = FakeReelJobRepo()
    deps = _deps(repo=repo)

    resp = _submit(deps)

    assert resp.status_code == 202
    _ctx, submission, _job, _now, _crid = repo.inserted[0]
    assert submission.project_id == PROJECT_ID


def test_submit_without_project_id_stays_unstamped():
    repo = FakeReelJobRepo()
    deps = _deps(repo=repo)

    resp = _submit(deps, project_id=None)

    assert resp.status_code == 202
    _ctx, submission, _job, _now, _crid = repo.inserted[0]
    assert submission.project_id is None


def test_submit_with_foreign_project_is_404_before_row_and_cp():
    repo = FakeReelJobRepo()
    deps = _deps(repo=repo, projects=FakeProjectRepo())    # owns no project

    resp = _submit(deps)

    assert resp.status_code == 404
    assert repo.inserted == []
    assert deps.control_plane.dispatch_calls == []


def test_submit_with_invalid_project_id_is_400():
    resp = _submit(_deps(), project_id="not-a-uuid")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_project_id"


def test_project_reels_route_lists_project_jobs():
    repo = FakeReelJobRepo()
    deps = _deps(repo=repo)
    assert _submit(deps).status_code == 202

    resp = _client(deps).get(f"/api/v1/projects/{PROJECT_ID}/reels")

    assert resp.status_code == 200
    reels = resp.get_json()["reels"]
    assert len(reels) == 1
    assert reels[0]["status"] == "queued"
    assert reels[0]["execution_id"] == "exec_p"
    assert set(reels[0]) >= {"job_id", "status", "execution_id", "download_url", "created_at"}


def test_project_reels_route_foreign_project_is_404():
    deps = _deps(projects=FakeProjectRepo())
    resp = _client(deps).get(f"/api/v1/projects/{PROJECT_ID}/reels")
    assert resp.status_code == 404


def test_feature_schema_reel_job_now_requires_project_id():
    from pg import FEATURE_SCHEMA

    assert "project_id" in FEATURE_SCHEMA["reel_job"]


def test_pg_list_for_project_fails_closed_without_database_url(monkeypatch):
    import pytest
    from pg import PgReelJobRepo

    monkeypatch.delenv("DEEPRESEARCH_DATABASE_URL", raising=False)
    with pytest.raises(SchemaUnavailable):
        PgReelJobRepo().list_for_project(make_ctx(), PROJECT_ID)
