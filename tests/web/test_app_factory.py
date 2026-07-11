"""B1 - app factory has no import-time or construction-time side effects."""

from __future__ import annotations

from conftest import FakeControlPlane, FakeIdentity, FakeReelJobRepo, make_ctx, make_deps


def test_import_server_module_does_no_io():
    import server  # noqa: F401 - import must not connect to DB/CP/SuperTokens

    assert hasattr(server, "create_app")


def test_create_app_and_health_touch_no_external_ports():
    import server

    identity = FakeIdentity(make_ctx())
    repo = FakeReelJobRepo()
    cp = FakeControlPlane()
    deps = make_deps(identity=identity, reel_jobs=repo, control_plane=cp)

    app = server.create_app(deps, enable_supertokens=False)
    client = app.test_client()
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    # No identity/repo/CP interaction happened just creating the app or hitting health.
    assert identity.calls == 0
    assert repo.inserted == [] and repo.attached == []
    assert cp.dispatch_calls == [] and cp.get_calls == []
