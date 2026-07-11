"""Plan 6 — carousel routes and review seams."""

from __future__ import annotations

import uuid

import server
from carousels import CarouselSlideRefResolver
from conftest import FakeCarouselRepo, FakeControlPlane, FakeIdentity, make_ctx, make_deps
from deps import (
    AppDeps,
    CarouselRepoPort,
    SlideRefResolverPort,
    Unauthorized,
    _Unconfigured,
    default_deps,
)

CREATE = "/api/v1/carousels"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def test_repo_and_resolver_satisfy_ports():
    assert isinstance(FakeCarouselRepo(), CarouselRepoPort)
    assert isinstance(CarouselSlideRefResolver(FakeCarouselRepo()), SlideRefResolverPort)


def test_default_deps_wires_carousels_and_real_slide_resolver():
    deps = default_deps()
    assert isinstance(deps, AppDeps)
    assert isinstance(deps.carousels, CarouselRepoPort)
    assert isinstance(deps.slides, CarouselSlideRefResolver)
    assert not isinstance(deps.slides, _Unconfigured)


def test_create_no_session_is_401_before_work():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane()
    deps = make_deps(
        identity=FakeIdentity(error=Unauthorized("no session")),
        carousels=repo,
        control_plane=cp,
    )

    resp = _client(deps).post(
        CREATE,
        json={"source_text": "doc", "preset": "carousel-default"},
    )

    assert resp.status_code == 401
    assert repo.inserted == []
    assert cp.dispatch_calls == []


def test_create_rejects_identity_field():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)

    resp = _client(deps).post(
        CREATE,
        json={
            "source_text": "doc",
            "preset": "carousel-default",
            "input": {"org_id": str(uuid.uuid4())},
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "forbidden_field"
    assert repo.inserted == []
    assert cp.dispatch_calls == []


def test_create_viewer_is_403_before_work():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx("viewer")), carousels=repo, control_plane=cp)

    resp = _client(deps).post(
        CREATE,
        json={"source_text": "doc", "preset": "carousel-default"},
    )

    assert resp.status_code == 403
    assert repo.inserted == []
    assert cp.dispatch_calls == []


def test_create_missing_source_text_is_400_before_work():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)

    resp = _client(deps).post(CREATE, json={"preset": "carousel-default"})

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_source_text"
    assert repo.inserted == []
    assert cp.dispatch_calls == []
