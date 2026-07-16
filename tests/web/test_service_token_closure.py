"""Behavior 5 — Workflow Closure (BLOCKING).

Drives the submit route through the REAL identity stack (``ResolverIdentity`` +
``CompositeSessions`` + ``ServiceTokenSessions``) with only the membership reader
faked and the env token seeded. No ``FakeIdentity``; nothing on the auth span is
mocked. ``UnauthenticatedSessions`` stands in for SuperTokens (no cookie in the
test client) so the fall-through-to-401 path is exercised for real.

Closure statement: "a request with a valid service token submits a reel job as
the service member; without it, 401."
"""

from __future__ import annotations

import auth
import server
from conftest import ORG_ID, USER_ID, FakeControlPlane, FakeReelJobRepo, make_deps

TOPIC_URL = "/api/v1/execute/async/reel-af.reel_topic_to_reel"
TOKEN = "secret-123"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _service_deps(token, seed, *, include_service_provider=True):
    """Build deps whose ``identity`` is the REAL composite resolver over a seeded
    fake membership reader. ``include_service_provider=False`` drops the service
    provider from the composite (RED-AT-SEAM proof)."""
    from conftest import FakeMembershipReader

    providers = []
    if include_service_provider:
        providers.append(auth.ServiceTokenSessions(token, auth.SERVICE_USER_ID, auth.SERVICE_EMAIL))
    providers.append(auth.UnauthenticatedSessions())  # stands in for SuperTokens (no cookie)
    ident = auth.ResolverIdentity(auth.CompositeSessions(providers), FakeMembershipReader(seed=seed))
    return make_deps(
        identity=ident,
        reel_jobs=FakeReelJobRepo(),
        control_plane=FakeControlPlane(response=(202, {"execution_id": "exec_svc"}, {})),
    )


def _seed():
    return {auth.SERVICE_USER_ID: (USER_ID, ORG_ID, "member")}


def test_valid_service_token_submits_as_member():
    deps = _service_deps(TOKEN, _seed())
    resp = _client(deps).post(
        TOPIC_URL,
        json={"input": {"topic": "black holes"}},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 202
    assert resp.get_json()["execution_id"] == "exec_svc"
    assert len(deps.reel_jobs.inserted) == 1
    ctx, *_ = deps.reel_jobs.inserted[0]
    assert ctx.user_id == USER_ID and ctx.org_id == ORG_ID and ctx.role == "member"
    assert ctx.supertokens_user_id == auth.SERVICE_USER_ID
    assert len(deps.control_plane.dispatch_calls) == 1


def test_absent_token_is_401_no_row_no_cp():
    deps = _service_deps(TOKEN, _seed())
    resp = _client(deps).post(TOPIC_URL, json={"input": {"topic": "x"}})  # no header
    assert resp.status_code == 401
    assert deps.reel_jobs.inserted == []
    assert deps.control_plane.dispatch_calls == []


def test_wrong_token_is_401_no_row_no_cp():
    deps = _service_deps(TOKEN, _seed())
    resp = _client(deps).post(
        TOPIC_URL,
        json={"input": {"topic": "x"}},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401
    assert deps.reel_jobs.inserted == []
    assert deps.control_plane.dispatch_calls == []


def test_red_at_seam_without_service_provider_valid_token_is_401():
    """Drop ``ServiceTokenSessions`` from the composite → the valid-token request
    goes 401 instead of 202, proving the seam is load-bearing. Re-including it
    (the tests above) restores 202."""
    deps = _service_deps(TOKEN, _seed(), include_service_provider=False)
    resp = _client(deps).post(
        TOPIC_URL,
        json={"input": {"topic": "black holes"}},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 401
    assert deps.reel_jobs.inserted == []
    assert deps.control_plane.dispatch_calls == []
