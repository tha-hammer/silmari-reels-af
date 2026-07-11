"""Submit-path behaviors: B3 (401), B5 (viewer 403), B6 (stamp), B7 (forged fields)."""

from __future__ import annotations

import server
from conftest import (
    ORG_ID,
    USER_ID,
    FakeControlPlane,
    FakeIdentity,
    FakeReelJobRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from deps import SchemaUnavailable, Unauthorized

TOPIC_URL = "/api/v1/execute/async/reel-af.reel_topic_to_reel"
COMPOSITE_URL = "/api/v1/execute/async/reel-af.reel_composite_to_reel"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


# B3 - unauthenticated submit is rejected, no row, no CP call.
def test_unauthenticated_submit_is_401_no_row_no_cp():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(
        identity=FakeIdentity(error=Unauthorized("no session")), reel_jobs=repo, control_plane=cp
    )
    resp = _client(deps).post(TOPIC_URL, json={"input": {"topic": "black holes"}})

    assert resp.status_code == 401
    assert repo.inserted == []
    assert cp.dispatch_calls == []


# B5 - viewer cannot create reels: 403, no row, no CP call.
def test_viewer_cannot_create_reel():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(
        identity=FakeIdentity(make_ctx(role="viewer")), reel_jobs=repo, control_plane=cp
    )
    resp = _client(deps).post(TOPIC_URL, json={"input": {"topic": "black holes"}})

    assert resp.status_code == 403
    assert repo.inserted == []
    assert cp.dispatch_calls == []


# B6 - authorized topic submit stamps server-derived ownership + dispatches once.
def test_topic_submit_stamps_owner_and_dispatches():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_777"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(TOPIC_URL, json={"input": {"topic": "  black holes  "}})

    assert resp.status_code == 202
    assert resp.get_json()["execution_id"] == "exec_777"
    # exactly one row stamped with server-derived org/user, queued
    assert len(repo.inserted) == 1
    ctx, submission, job_id, _now, _crid = repo.inserted[0]
    assert ctx.org_id == ORG_ID and ctx.user_id == USER_ID
    assert submission.topic == "black holes" and submission.title == "black holes"
    assert submission.source_url is None and submission.source_research_run_id is None
    # execution id attached exactly once; CP called exactly once, identity-free body
    assert repo.attached == [(ctx, job_id, "exec_777")]
    assert len(cp.dispatch_calls) == 1
    target, body = cp.dispatch_calls[0]
    assert target == "reel-af.reel_topic_to_reel"
    assert body == {"input": {"topic": "black holes"}}


# B6 - composite URL submit maps source_url and dispatches.
def test_composite_url_submit_maps_source_url():
    repo = FakeReelJobRepo()
    deps = make_deps(identity=FakeIdentity(make_ctx("admin")), reel_jobs=repo)
    body = {"input": {"url": "https://youtube.com/watch?v=abc", "source": "https://youtube.com/watch?v=abc", "preset": "middle-third-dynamic"}}

    resp = _client(deps).post(COMPOSITE_URL, json=body)

    assert resp.status_code == 202
    _ctx, submission, _job, _now, _crid = repo.inserted[0]
    assert submission.source_url == "https://youtube.com/watch?v=abc"
    assert submission.topic is None


# T7 - composite FILE submit presigns the upload handle → node-fetchable url, drops the raw handle.
def test_composite_file_submit_presigns_and_injects_url():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_file"}, {}))
    uploads = FakeUploadStore(presigned="https://bucket.example/signed/clip.mp4?sig=xyz")
    deps = make_deps(
        identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp, uploads=uploads
    )
    body = {"input": {"source": "11111111.../abc-clip.mp4", "preset": "middle-third-dynamic"}}

    resp = _client(deps).post(COMPOSITE_URL, json=body)

    assert resp.status_code == 202
    # handle presigned exactly once, using the client-supplied opaque key
    assert uploads.presign_calls == ["11111111.../abc-clip.mp4"]
    # dispatched body carries the presigned url + preset, and NOT the raw handle
    assert len(cp.dispatch_calls) == 1
    _target, dispatched = cp.dispatch_calls[0]
    assert dispatched["input"]["url"] == "https://bucket.example/signed/clip.mp4?sig=xyz"
    assert dispatched["input"]["preset"] == "middle-third-dynamic"
    assert "source" not in dispatched["input"]


# T7 - unconfigured object store fails closed BEFORE any row or CP call (presign precedes insert).
def test_composite_file_submit_presign_unconfigured_is_503_no_row_no_cp():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    uploads = FakeUploadStore(presign_error=SchemaUnavailable("bucket not configured"))
    deps = make_deps(
        identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp, uploads=uploads
    )
    body = {"input": {"source": "org/abc-clip.mp4", "preset": "middle-third-dynamic"}}

    resp = _client(deps).post(COMPOSITE_URL, json=body)

    assert resp.status_code == 503
    assert repo.inserted == []
    assert cp.dispatch_calls == []


# B7 - forged identity fields are rejected (top level and under input): 400, no row, no CP.
def test_forged_identity_field_top_level_rejected():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(
        TOPIC_URL, json={"org_id": "ATTACKER", "input": {"topic": "x"}}
    )
    assert resp.status_code == 400
    assert repo.inserted == [] and cp.dispatch_calls == []


def test_forged_identity_field_under_input_rejected():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(
        TOPIC_URL, json={"input": {"topic": "x", "created_by": USER_ID.hex}}
    )
    assert resp.status_code == 400
    assert repo.inserted == [] and cp.dispatch_calls == []


# §6/§9 - unsupported target after auth is 400, no row, no CP.
def test_unsupported_target_is_400():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(
        "/api/v1/execute/async/reel-af.reel_delete_everything", json={"input": {"x": 1}}
    )
    assert resp.status_code == 400
    assert repo.inserted == [] and cp.dispatch_calls == []


# B7 - empty topic is 400.
def test_empty_topic_is_400():
    deps = make_deps(identity=FakeIdentity(make_ctx()))
    resp = _client(deps).post(TOPIC_URL, json={"input": {"topic": "   "}})
    assert resp.status_code == 400
