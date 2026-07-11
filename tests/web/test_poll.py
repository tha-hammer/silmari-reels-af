"""B11 owned-poll scoping + B13 result-ref reconciliation (fake-first)."""

from __future__ import annotations

import uuid

import server
from conftest import (
    ORG_ID,
    USER_ID,
    FakeControlPlane,
    FakeIdentity,
    FakeReelJobRepo,
    make_ctx,
    make_deps,
)
from deps import NotFound, Unauthorized
from reel_jobs import ReelJobRef

EXEC = "exec_1"
POLL_URL = f"/api/v1/executions/{EXEC}"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _owned_job(status="producing"):
    return ReelJobRef(job_id=uuid.uuid4(), org_id=ORG_ID, created_by=USER_ID,
                      status=status, execution_id=EXEC)


# ── B11 scoping ──
def test_poll_without_session_is_401_no_cp():
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(error=Unauthorized("no session")), control_plane=cp)
    assert _client(deps).get(POLL_URL).status_code == 401
    assert cp.get_calls == []


def test_poll_absent_or_foreign_row_is_404_no_cp():
    repo = FakeReelJobRepo(get_error=NotFound("absent/foreign"))
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    assert _client(deps).get(POLL_URL).status_code == 404
    assert cp.get_calls == []


def test_poll_same_org_read_denied_is_403_no_cp():
    # member viewing another user's job in the same org → read policy denies.
    other = ReelJobRef(job_id=uuid.uuid4(), org_id=ORG_ID, created_by=uuid.uuid4(),
                       status="producing", execution_id=EXEC)
    repo = FakeReelJobRepo(job=other)
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp)
    assert _client(deps).get(POLL_URL).status_code == 403
    assert cp.get_calls == []


def test_poll_allowed_reconciles_and_returns_cp_shape():
    repo = FakeReelJobRepo(job=_owned_job())
    cp = FakeControlPlane(
        response=(200, {"status": "succeeded", "result": {"video_path": "/out/x.mp4"}}, {})
    )
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp)
    resp = _client(deps).get(POLL_URL)

    assert resp.status_code == 200
    assert resp.get_json()["result"]["video_path"] == "/out/x.mp4"
    assert cp.get_calls == [EXEC]
    # B13: reconciled with normalized status + cp-execution result_ref + completed_at
    assert len(repo.updates) == 1
    execution_id, status, result_ref, completed_at = repo.updates[0]
    assert execution_id == EXEC and status == "succeeded"
    assert result_ref == "cp-execution://exec_1/result/video_path"
    assert completed_at is not None


# ── B13 result-ref namespace ──
def test_poll_prefers_download_url_for_result_ref():
    repo = FakeReelJobRepo(job=_owned_job())
    cp = FakeControlPlane(
        response=(200, {"status": "completed",
                        "result": {"download_url": "https://cdn/x.mp4", "video_path": "/out/x.mp4"}}, {})
    )
    deps = make_deps(identity=FakeIdentity(make_ctx("owner")), reel_jobs=repo, control_plane=cp)
    _client(deps).get(POLL_URL)
    _e, status, result_ref, _c = repo.updates[0]
    assert status == "succeeded" and result_ref == "https://cdn/x.mp4"


def test_poll_failure_sets_no_success_result_ref():
    repo = FakeReelJobRepo(job=_owned_job())
    cp = FakeControlPlane(response=(200, {"status": "error", "error": "boom"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx("owner")), reel_jobs=repo, control_plane=cp)
    _client(deps).get(POLL_URL)
    _e, status, result_ref, completed_at = repo.updates[0]
    assert status == "failed" and result_ref is None and completed_at is not None
