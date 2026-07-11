"""Plan 6 — carousel routes and review seams."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import server
from carousels import CarouselSlideRefResolver
from conftest import (
    ORG_ID,
    FakeCarouselRepo,
    FakeControlPlane,
    FakeIdentity,
    make_ctx,
    make_deps,
)
from deps import (
    AppDeps,
    CarouselRepoPort,
    SlideRefResolverPort,
    Unauthorized,
    _Unconfigured,
    default_deps,
)

CREATE = "/api/v1/carousels"
CID = "car_1"
TARGET_CAROUSEL = "reel-af.reel_research_to_carousel"
WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _post(client, key=None, json=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        CREATE,
        json=json or {"source_text": "doc", "preset": "carousel-default"},
        headers=headers,
    )


def _seed(repo, org=ORG_ID, cid=CID, status="draft"):
    repo.seed(
        org,
        cid,
        status=status,
        slides=[
            {"idx": idx, "image_ref": f"ref-{idx}", "prompt": f"p{idx}", "status": "ok"}
            for idx in range(3)
        ],
    )


def test_repo_and_resolver_satisfy_ports():
    assert isinstance(FakeCarouselRepo(), CarouselRepoPort)
    assert isinstance(CarouselSlideRefResolver(FakeCarouselRepo()), SlideRefResolverPort)


def test_default_deps_wires_carousels_and_real_slide_resolver():
    deps = default_deps()
    assert isinstance(deps, AppDeps)
    assert isinstance(deps.carousels, CarouselRepoPort)
    assert isinstance(deps.slides, CarouselSlideRefResolver)
    assert not isinstance(deps.slides, _Unconfigured)


def test_index_has_carousel_review_ui_contract():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    config_match = re.search(
        r'<script type="application/json" id="config">\s*(.*?)\s*</script>',
        html,
        re.S,
    )
    assert config_match is not None
    config = json.loads(config_match.group(1))

    assert config["api"]["carouselCreatePath"] == "/api/v1/carousels"
    assert config["api"]["carouselPath"] == "/api/v1/carousels/{id}"
    assert config["api"]["carouselSlidePath"] == "/api/v1/carousels/{id}/slides/{idx}"
    assert config["api"]["carouselRecreatePath"] == (
        "/api/v1/carousels/{id}/slides/{idx}/recreate"
    )
    assert config["api"]["carouselFinalizePath"] == "/api/v1/carousels/{id}/finalize"
    assert config["api"]["carouselCancelPath"] == "/api/v1/carousels/{id}/cancel"
    assert html.count('id="carouselReview"') == 1
    assert html.count('id="carouselSlides"') == 1
    assert html.count('id="carouselCancel"') == 1
    assert html.count('id="carouselFinalize"') == 1
    assert 'data-carousel-action="recreate"' in html
    assert "async function createCarouselReview" in html
    assert "async function loadCarousel" in html
    assert "async function recreateCarouselSlide" in html
    assert "async function cancelCarousel" in html
    assert "async function finalizeCarousel" in html


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


def test_same_key_dispatches_research_to_carousel_once():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_1"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)
    client = _client(deps)

    first = _post(client, key="K1")
    second = _post(client, key="K1")

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(cp.dispatch_calls) == 1
    target, body = cp.dispatch_calls[0]
    assert target == TARGET_CAROUSEL
    assert "input" in body
    assert not (set(body) & {"org_id", "created_by", "user_id"})


def test_dispatch_body_is_identity_free():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_2"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)

    _post(_client(deps), key="A")

    _, body = cp.dispatch_calls[0]
    flat = str(body)
    assert "Cookie" not in flat
    assert "Authorization" not in flat
    assert str(ORG_ID) not in flat


def test_research_run_id_wire_key_coerced_stripped_and_tenancy_checked():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_3"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)
    research_run_id = deps.reel_jobs.seed_research_run(
        execution_id="exec_r1",
        org_id=make_ctx().org_id,
        created_by=make_ctx().user_id,
    )

    resp = _post(
        _client(deps),
        key="RR",
        json={
            "source_text": "doc",
            "preset": "carousel-default",
            "research_run_id": str(research_run_id),
        },
    )

    assert resp.status_code == 202
    assert repo.inserted[0][1].source_research_run_id == research_run_id
    _, body = cp.dispatch_calls[0]
    flat = str(body)
    assert str(research_run_id) not in flat
    assert "research_run_id" not in flat
    assert "source_research_run_id" not in flat


def test_malformed_research_run_id_is_400():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_bad"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)

    resp = _post(
        _client(deps),
        key="BAD",
        json={
            "source_text": "doc",
            "preset": "carousel-default",
            "research_run_id": "not-a-uuid",
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_research_run_id"
    assert repo.inserted == []
    assert cp.dispatch_calls == []


def test_cross_org_research_run_id_is_404():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_cross"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)
    research_run_id = deps.reel_jobs.seed_research_run(
        execution_id="exec_foreign",
        org_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
    )

    resp = _post(
        _client(deps),
        key="CROSS",
        json={
            "source_text": "doc",
            "preset": "carousel-default",
            "research_run_id": str(research_run_id),
        },
    )

    assert resp.status_code == 404
    assert repo.inserted == []
    assert cp.dispatch_calls == []


def test_idempotent_replay_returns_carousel_id_not_job_id():
    repo = FakeCarouselRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_4"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, control_plane=cp)
    client = _client(deps)

    first = _post(client, key="R1")
    second = _post(client, key="R1")

    assert "carousel_id" in second.get_json()
    assert "job_id" not in second.get_json()
    assert first.get_json()["carousel_id"] == second.get_json()["carousel_id"]


def test_owner_reads_ordered_slides():
    repo = FakeCarouselRepo()
    _seed(repo)
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)

    resp = _client(deps).get(f"/api/v1/carousels/{CID}")

    assert resp.status_code == 200
    slides = resp.get_json()["slides"]
    assert [slide["idx"] for slide in slides] == [0, 1, 2]
    assert all({"idx", "image_ref", "prompt", "status"} <= set(slide) for slide in slides)


def test_cross_org_get_is_404():
    repo = FakeCarouselRepo()
    _seed(repo)
    other = make_ctx()
    object.__setattr__(other, "org_id", uuid.uuid4())
    deps = make_deps(identity=FakeIdentity(other), carousels=repo)

    resp = _client(deps).get(f"/api/v1/carousels/{CID}")

    assert resp.status_code == 404


def test_recreate_replaces_only_that_slide(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    repo = FakeCarouselRepo()
    _seed(repo)
    recreated = {"idx": 1, "image_ref": "ref-1-new", "prompt": "p1", "status": "ok"}

    def fake_recreate(ctx, cid, idx, note, **kwargs):
        assert kwargs["provider"] is not None
        assert kwargs["storage"] is not None
        assert hasattr(kwargs["guard"], "register")
        return recreated

    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = server.create_app(
        deps, enable_supertokens=False, recreate_fn=fake_recreate
    ).test_client()

    resp = client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={"note": "brighter"})

    assert resp.status_code == 200
    assert resp.get_json()["image_ref"] == "ref-1-new"
    assert repo.replaced == [(ORG_ID, CID, 1)]


def test_cross_org_recreate_is_404(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    repo = FakeCarouselRepo()
    _seed(repo)
    calls = []
    other = make_ctx()
    object.__setattr__(other, "org_id", uuid.uuid4())
    deps = make_deps(identity=FakeIdentity(other), carousels=repo)

    def fake_recreate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"idx": 1, "image_ref": "new", "prompt": "p1", "status": "ok"}

    client = server.create_app(
        deps, enable_supertokens=False, recreate_fn=fake_recreate
    ).test_client()
    resp = client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={"note": "x"})

    assert resp.status_code == 404
    assert calls == []
    assert repo.replaced == []


def test_recreate_without_openrouter_key_is_503_no_spend(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    repo = FakeCarouselRepo()
    _seed(repo)
    calls = []
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)

    def fake_recreate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"idx": 1, "image_ref": "new", "prompt": "p1", "status": "ok"}

    client = server.create_app(
        deps, enable_supertokens=False, recreate_fn=fake_recreate
    ).test_client()
    resp = client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={"note": "x"})

    assert resp.status_code == 503
    assert calls == []
    assert repo.replaced == []


def test_default_recreate_uses_plan2_with_resolved_deps(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("REEL_CAROUSEL_RECREATE_DIR", str(tmp_path))
    repo = FakeCarouselRepo()
    _seed(repo)
    provider = object()
    calls = []

    async def fake_plan2_recreate(**kwargs):
        calls.append(kwargs)
        kwargs["guard"].register(kwargs["carousel"]["carousel_id"])
        return {
            "idx": kwargs["idx"],
            "image_ref": "ref-1-default",
            "image_prompt": "p1 with note",
            "status": "ok",
        }

    monkeypatch.setattr(server, "_openrouter_provider", lambda: provider, raising=False)
    monkeypatch.setattr(server, "_call_plan2_recreate", fake_plan2_recreate, raising=False)
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = _client(deps)

    resp = client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={"note": "brighter"})

    assert resp.status_code == 200
    assert calls
    call = calls[0]
    assert call["provider"] is provider
    assert call["storage"] is deps.storage
    assert call["carousel"]["carousel_id"] == CID
    assert call["carousel"]["run_id"] == CID
    assert call["carousel"]["slides"][1]["image_prompt"] == "p1"
    assert call["note"] == "brighter"
    assert repo.hq_recreate_count(make_ctx(), CID) == 1
    assert repo.replaced == [(ORG_ID, CID, 1)]


def test_hq_cap_persists_across_requests(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    repo = FakeCarouselRepo(hq_cap=2)
    _seed(repo)

    def fake_recreate(ctx, cid, idx, note, *, guard, **kwargs):
        guard.register(cid)
        return {"idx": idx, "image_ref": f"ref-{idx}-hq", "prompt": f"p{idx}", "status": "ok"}

    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = server.create_app(
        deps, enable_supertokens=False, recreate_fn=fake_recreate
    ).test_client()

    assert client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={}).status_code == 200
    assert client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={}).status_code == 200
    over = client.post(f"/api/v1/carousels/{CID}/slides/1/recreate", json={})

    assert over.status_code in (402, 409)
    assert repo.hq_recreate_count(make_ctx(), CID) == 2


def test_cancel_sets_cancelled_and_deletes_objects():
    repo = FakeCarouselRepo()
    _seed(repo)
    storage = make_deps().storage
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo, storage=storage)
    client = _client(deps)

    resp = client.post(f"/api/v1/carousels/{CID}/cancel")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"
    assert client.get(f"/api/v1/carousels/{CID}").get_json()["status"] == "cancelled"
    assert storage.deleted == ["ref-0", "ref-1", "ref-2"]


def test_cross_org_cancel_is_404_no_delete():
    repo = FakeCarouselRepo()
    _seed(repo)
    other = make_ctx()
    object.__setattr__(other, "org_id", uuid.uuid4())
    storage = make_deps().storage
    deps = make_deps(identity=FakeIdentity(other), carousels=repo, storage=storage)

    resp = _client(deps).post(f"/api/v1/carousels/{CID}/cancel")

    assert resp.status_code == 404
    assert storage.deleted == []
    assert repo.get(make_ctx(), CID).status == "draft"


def test_finalize_makes_succeeded_and_is_idempotent():
    repo = FakeCarouselRepo()
    _seed(repo)
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = _client(deps)

    first = client.post(f"/api/v1/carousels/{CID}/finalize")
    second = client.post(f"/api/v1/carousels/{CID}/finalize")

    assert first.status_code == 200
    assert second.status_code == 200
    assert client.get(f"/api/v1/carousels/{CID}").get_json()["status"] == "succeeded"


def test_cancelled_cannot_be_finalized():
    repo = FakeCarouselRepo()
    _seed(repo, status="cancelled")
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = _client(deps)

    resp = client.post(f"/api/v1/carousels/{CID}/finalize")

    assert resp.status_code == 200
    assert client.get(f"/api/v1/carousels/{CID}").get_json()["status"] == "cancelled"


def test_cross_org_finalize_is_404():
    repo = FakeCarouselRepo()
    _seed(repo)
    other = make_ctx()
    object.__setattr__(other, "org_id", uuid.uuid4())
    deps = make_deps(identity=FakeIdentity(other), carousels=repo)

    resp = _client(deps).post(f"/api/v1/carousels/{CID}/finalize")

    assert resp.status_code == 404
    assert repo.get(make_ctx(), CID).status == "draft"


def test_finalize_sets_succeeded_and_is_idempotent():
    repo = FakeCarouselRepo()
    _seed(repo)
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = _client(deps)

    first = client.post(f"/api/v1/carousels/{CID}/finalize")
    second = client.post(f"/api/v1/carousels/{CID}/finalize")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["status"] == "succeeded"
    assert client.get(f"/api/v1/carousels/{CID}").get_json()["status"] == "succeeded"


def test_cancelled_carousel_cannot_be_finalized():
    repo = FakeCarouselRepo()
    _seed(repo, status="cancelled")
    deps = make_deps(identity=FakeIdentity(make_ctx()), carousels=repo)
    client = _client(deps)

    resp = client.post(f"/api/v1/carousels/{CID}/finalize")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"
    assert client.get(f"/api/v1/carousels/{CID}").get_json()["status"] == "cancelled"


def test_cross_org_finalize_is_404_no_change():
    repo = FakeCarouselRepo()
    _seed(repo)
    other = make_ctx()
    object.__setattr__(other, "org_id", uuid.uuid4())
    deps = make_deps(identity=FakeIdentity(other), carousels=repo)

    resp = _client(deps).post(f"/api/v1/carousels/{CID}/finalize")

    assert resp.status_code == 404
    assert repo.get(make_ctx(), CID).status == "draft"
