"""B14 - route allowlist: unknown /api/* + cancellation paths are 404, no CP call."""

from __future__ import annotations

import server
from conftest import FakeControlPlane, FakeIdentity, make_ctx, make_deps


def _client_with_cp():
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), control_plane=cp)
    return server.create_app(deps, enable_supertokens=False).test_client(), cp


def test_unknown_api_path_is_404_no_cp_no_apikey():
    client, cp = _client_with_cp()
    resp = client.get("/api/v1/whoami")
    assert resp.status_code == 404
    assert cp.dispatch_calls == [] and cp.get_calls == []


def test_cancellation_path_is_not_allowlisted():
    client, cp = _client_with_cp()
    # /executions/<id>/cancel has an extra segment → not the poll route → 404, no CP.
    resp = client.post("/api/v1/executions/exec_1/cancel")
    assert resp.status_code == 404
    assert cp.dispatch_calls == [] and cp.get_calls == []


def test_bare_execute_prefix_is_404():
    client, cp = _client_with_cp()
    # Missing target segment → not a submit route.
    resp = client.post("/api/v1/execute/async/")
    assert resp.status_code == 404
    assert cp.dispatch_calls == []


def test_health_needs_no_session():
    client, _cp = _client_with_cp()
    assert client.get("/health").status_code == 200


def test_index_redirects_to_login_when_unauthenticated():
    from deps import Unauthorized

    deps = make_deps(identity=FakeIdentity(error=Unauthorized("no session")))
    client = server.create_app(deps, enable_supertokens=False).test_client()
    resp = client.get("/")
    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers["Location"]
