"""B9 idempotency + B10 dispatch state machine / failure recovery (fake-first)."""

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
from deps import BadGateway
from reel_jobs import ReelJobRef

TOPIC_URL = "/api/v1/execute/async/reel-af.reel_topic_to_reel"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _post(client, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(TOPIC_URL, json={"input": {"topic": "black holes"}}, headers=headers)


# ── B9 idempotency ──
def test_same_key_with_execution_id_does_not_redispatch():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_1"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    client = _client(deps)

    first = _post(client, key="K1")
    second = _post(client, key="K1")

    assert first.status_code == 202 and second.status_code == 202
    assert len(cp.dispatch_calls) == 1               # second did NOT dispatch
    assert second.get_json()["execution_id"] == "exec_1"


def test_same_key_still_queued_returns_409_pending():
    repo = FakeReelJobRepo()
    job_id = uuid.uuid4()
    repo.set_existing(
        (ORG_ID, USER_ID, "K2"),
        ReelJobRef(job_id=job_id, org_id=ORG_ID, created_by=USER_ID, status="queued"),
    )
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    resp = _post(_client(deps), key="K2")
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "idempotent_request_pending"
    assert resp.headers.get("Retry-After")
    assert cp.dispatch_calls == []


def test_different_keys_create_different_jobs():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    client = _client(deps)
    _post(client, key="A")
    _post(client, key="B")
    assert len(cp.dispatch_calls) == 2 and len(repo.inserted) == 2


# ── B10 failure matrix ──
def test_cp_semantic_400_passthrough_marks_failed():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(422, {"error": "bad input"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _post(_client(deps), key="K")
    assert resp.status_code == 422
    assert len(repo.failed) == 1


def test_cp_429_passthrough_marks_failed():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(429, {"error_category": "rate_limited"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _post(_client(deps), key="K")
    assert resp.status_code == 429
    assert resp.get_json()["error_category"] == "rate_limited"
    assert len(repo.failed) == 1


def test_cp_transport_error_is_502_marks_failed():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(error=BadGateway("transport blew up"))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _post(_client(deps), key="K")
    assert resp.status_code == 502
    assert len(repo.failed) == 1


def test_cp_success_without_execution_id_is_502_marks_failed():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"no": "id"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _post(_client(deps), key="K")
    assert resp.status_code == 502
    assert len(repo.failed) == 1


def test_attach_failure_after_acceptance_is_503_orphan():
    repo = FakeReelJobRepo()
    repo._attach_error = BadGateway("attach failed")  # any HttpError from attach
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_orphan"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _post(_client(deps), key="K")
    assert resp.status_code == 503
