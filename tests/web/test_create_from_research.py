"""Plan 5 — Create-from-research fan-out route (server seams, ISC-30/35 + guards).

The DOM/interaction behaviors (ISC-28/29/31/32/33/34/36) have no JS harness in this
repo and are MANUAL/E2E; a lightweight HTML-contract presence test guards the wiring.
The load-bearing automated coverage is the create-from-text fan-out route:
``POST /api/v1/research/create {text, outputs, research_run_id?}``.
"""

from __future__ import annotations

import itertools
import uuid
from pathlib import Path

import reel_jobs
import server
from conftest import (
    OTHER_ORG,
    OTHER_USER,
    FakeControlPlane,
    FakeIdentity,
    FakeReelJobRepo,
    make_ctx,
    make_deps,
)
from deps import AuthContext, Unauthorized

CREATE_URL = "/api/v1/research/create"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _counting_uuid_factory():
    # FIXED_JOB_ID (conftest) is constant → per-output job_ids collide silently.
    # A counter makes the distinct-job_id assertion meaningful (review C3).
    counter = itertools.count(1)
    return lambda: uuid.UUID(int=next(counter))


# ─────────────────────── Behavior 1: Automatic fan-out (ISC-30) ───────────────────────


def test_automatic_enqueues_both_video_and_carousel():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_x"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo,
                     control_plane=cp, uuid_factory=_counting_uuid_factory())
    body = {"text": "research doc body", "outputs": ["video", "carousel"]}

    resp = _client(deps).post(CREATE_URL, json=body)

    assert resp.status_code in (200, 202)
    assert len(repo.inserted) == 2
    targets = {t for (t, _b) in cp.dispatch_calls}
    assert targets == {reel_jobs.TARGET_TEXT_REEL, reel_jobs.TARGET_TEXT_CAROUSEL}
    for _t, dispatched in cp.dispatch_calls:
        assert dispatched["input"]["text"] == "research doc body"  # identity-free, text carried
        assert "research_run_id" not in dispatched["input"]        # provenance never leaks (C3)
        assert "source_research_run_id" not in dispatched["input"]
    jobs = resp.get_json()["jobs"]
    assert len({j["job_id"] for j in jobs}) == 2                   # distinct per-output job_id
    assert [j["output"] for j in jobs] == ["carousel", "video"]    # sorted, deterministic


def test_property_all_nonempty_output_subsets():
    for subset in (["video"], ["carousel"], ["video", "carousel"]):
        repo = FakeReelJobRepo()
        cp = FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
        deps = make_deps(reel_jobs=repo, control_plane=cp,
                         uuid_factory=_counting_uuid_factory())
        resp = _client(deps).post(CREATE_URL, json={"text": "T", "outputs": subset})
        assert resp.status_code in (200, 202)
        assert len(cp.dispatch_calls) == len(set(subset))
        want = {reel_jobs.TEXT_TARGET_BY_OUTPUT[o] for o in subset}
        assert {t for (t, _b) in cp.dispatch_calls} == want
        jobs = resp.get_json()["jobs"]
        assert len({j["job_id"] for j in jobs}) == len(set(subset))


def test_duplicate_outputs_deduped():
    repo, cp = FakeReelJobRepo(), FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
    deps = make_deps(reel_jobs=repo, control_plane=cp, uuid_factory=_counting_uuid_factory())
    resp = _client(deps).post(CREATE_URL, json={"text": "T", "outputs": ["video", "video"]})
    assert resp.status_code in (200, 202)
    assert len(cp.dispatch_calls) == 1


def test_per_output_idempotency_subkeys_distinct():
    repo, cp = FakeReelJobRepo(), FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
    deps = make_deps(reel_jobs=repo, control_plane=cp, uuid_factory=_counting_uuid_factory())
    _client(deps).post(CREATE_URL, headers={"Idempotency-Key": "K1"},
                       json={"text": "T", "outputs": ["video", "carousel"]})
    crids = {crid for (_c, _s, _j, _n, crid) in repo.inserted}
    assert crids == {"K1:video", "K1:carousel"}          # per-output sub-keys, no collision


# ─────────────────────── Behavior 2: verbatim text (ISC-35) ───────────────────────


def test_create_uses_posted_text_verbatim():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(CREATE_URL, json={"text": "EDITED body", "outputs": ["carousel"]})
    assert resp.status_code in (200, 202)
    _t, dispatched = cp.dispatch_calls[0]
    assert dispatched["input"]["text"] == "EDITED body"


def test_empty_text_is_400_no_dispatch():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(CREATE_URL, json={"text": "   ", "outputs": ["carousel"]})
    assert resp.status_code == 400
    assert repo.inserted == [] and cp.dispatch_calls == []


def test_missing_text_is_400():
    deps = make_deps()
    assert _client(deps).post(CREATE_URL, json={"outputs": ["video"]}).status_code == 400


# ─────────────────────── Guards ───────────────────────


def test_no_outputs_is_400_no_work():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(CREATE_URL, json={"text": "T", "outputs": []})
    assert resp.status_code == 400
    assert repo.inserted == [] and cp.dispatch_calls == []


def test_unknown_output_is_400_no_dispatch():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(CREATE_URL, json={"text": "T", "outputs": ["gif"]})
    assert resp.status_code == 400
    assert cp.dispatch_calls == []


def test_unauthenticated_is_401():
    deps = make_deps(identity=FakeIdentity(error=Unauthorized("no session")))
    assert _client(deps).post(CREATE_URL, json={"text": "T", "outputs": ["video"]}).status_code == 401


def test_viewer_role_is_403():
    deps = make_deps(identity=FakeIdentity(make_ctx("viewer")))
    assert _client(deps).post(CREATE_URL, json={"text": "T", "outputs": ["video"]}).status_code == 403


def test_forbidden_identity_field_is_400():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(CREATE_URL, json={"text": "T", "outputs": ["video"], "org_id": "x"})
    assert resp.status_code == 400
    assert cp.dispatch_calls == []


# ─────────────────────── Provenance (research_run_id wire-key) ───────────────────────


def test_research_run_id_carried_on_db_field_not_in_cp_input():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
    deps = make_deps(reel_jobs=repo, control_plane=cp, uuid_factory=_counting_uuid_factory())
    rid = repo.seed_research_run(execution_id="exec_r", org_id=make_ctx().org_id,
                                 created_by=make_ctx().user_id)
    resp = _client(deps).post(CREATE_URL,
                              json={"text": "T", "outputs": ["carousel"], "research_run_id": str(rid)})
    assert resp.status_code in (200, 202)
    # carried on the DB-bound submission field...
    _c, submission, _j, _n, _crid = repo.inserted[0]
    assert submission.source_research_run_id == rid
    # ...but never in the reasoner input
    _t, dispatched = cp.dispatch_calls[0]
    assert "research_run_id" not in dispatched["input"]
    assert "source_research_run_id" not in dispatched["input"]


def test_malformed_research_run_id_is_400_no_work():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(CREATE_URL,
                              json={"text": "T", "outputs": ["video"], "research_run_id": "not-a-uuid"})
    assert resp.status_code == 400
    assert repo.inserted == [] and cp.dispatch_calls == []


def test_cross_org_research_run_id_is_404_no_work():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "e"}, {}))
    deps = make_deps(reel_jobs=repo, control_plane=cp)
    rid = repo.seed_research_run(execution_id="exec_r", org_id=OTHER_ORG, created_by=OTHER_USER)
    resp = _client(deps).post(CREATE_URL,
                              json={"text": "T", "outputs": ["video"], "research_run_id": str(rid)})
    assert resp.status_code == 404
    assert repo.inserted == [] and cp.dispatch_calls == []


# ─────────────────────── Partial failure (review C2) ───────────────────────


def test_zero_enqueued_cp_failure_is_502():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(503, {"error": "cp down"}, {}))  # all legs fail
    deps = make_deps(reel_jobs=repo, control_plane=cp, uuid_factory=_counting_uuid_factory())
    resp = _client(deps).post(CREATE_URL, json={"text": "T", "outputs": ["video", "carousel"]})
    assert resp.status_code == 502


# ─────────────────────── HTML contract (presence, not behavior) ───────────────────────


def test_index_has_research_mode_wiring():
    html = (Path(__file__).resolve().parents[2] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-mode="research"' in html            # ISC-28 third tab
    assert 'id="researchQuery"' in html              # ISC-29 query box
    assert 'id="researchDoc"' in html                # ISC-31/32 editable doc
    assert "<textarea" in html                       # ISC-32 editable, not read-only
    assert 'data-output="video"' in html             # ISC-33 multi-select
    assert 'data-output="carousel"' in html          # ISC-33 multi-select
    assert 'id="createFromResearch"' in html         # ISC-34 gated Create button


# Silence unused-import linters for symbols referenced only in some assertions.
_ = (AuthContext,)
