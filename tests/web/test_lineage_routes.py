"""INT-04 Cross-App Lineage View — read-only, ORG-SCOPED HTTP surface (B7).

Two GET routes under the existing ``/api`` dispatch: ``/api/v1/lineage/entity/{id}``
(forward) and ``/api/v1/lineage/run/{id}`` (reverse). Each resolves the caller's
``AuthContext`` and delegates to ``LineageView``; another org's id conceals to a 200
empty list; POST/PUT/DELETE are not routed (the read model exposes NO write).
"""

from __future__ import annotations

import server  # noqa: E402 - conftest puts web/ on the path
from conftest import (  # noqa: E402
    ORG_ID,
    OTHER_ORG,
    USER_ID,
    FakeCarouselRepo,
    FakeIdentity,
    FakeReelJobRepo,
    FakeResearchRunReader,
    make_ctx,
    make_deps,
)
from deps import AuthContext  # noqa: E402


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _seed_two_orgs():
    reel = FakeReelJobRepo()
    car = FakeCarouselRepo()
    run_id = reel.seed_research_run("exec-1", ORG_ID, USER_ID, status="succeeded")
    reel.seed_reel_job("reel-A", ORG_ID, source_research_run_id=run_id)          # caller's org
    reel.seed_reel_job("reel-Z", OTHER_ORG, source_research_run_id=run_id)       # other org
    reader = FakeResearchRunReader(details={"exec-1": {"title": "T", "result_ref": "r"}})
    return reel, car, reader


def test_lineage_read_endpoints_are_org_scoped():
    reel, car, reader = _seed_two_orgs()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=reel,
                     carousels=car, research_reader=reader)
    c1 = _client(deps)   # caller is ORG_ID

    forward = c1.get("/api/v1/lineage/entity/reel-A")
    assert forward.status_code == 200
    assert "exec-1" in forward.get_data(as_text=True)                 # upstream run surfaced

    reverse = c1.get("/api/v1/lineage/run/exec-1")
    assert reverse.status_code == 200
    assert "reel-A" in reverse.get_data(as_text=True)                 # downstream reel surfaced

    other = c1.get("/api/v1/lineage/entity/reel-Z")                   # other org's entity
    assert other.status_code == 200
    assert "exec-1" not in other.get_data(as_text=True)               # concealed empty

    unknown = c1.get("/api/v1/lineage/entity/unknown")
    assert unknown.status_code == 200
    assert unknown.get_data(as_text=True).strip() == "[]"            # empty list, not 404/500


def test_lineage_run_route_conceals_other_org():
    reel, car, reader = _seed_two_orgs()
    # a caller in OTHER_ORG sees none of ORG_ID's lineage
    other = FakeIdentity(AuthContext(user_id=USER_ID, org_id=OTHER_ORG, role="member",
                                     supertokens_user_id="st"))
    deps = make_deps(identity=other, reel_jobs=reel, carousels=car, research_reader=reader)
    c2 = _client(deps)
    # OTHER_ORG owns reel-Z from a run it does not own the reference row for -> empty reverse.
    resp = c2.get("/api/v1/lineage/run/exec-1")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True).strip() == "[]"


def test_lineage_paths_reject_writes():
    reel, car, reader = _seed_two_orgs()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=reel,
                     carousels=car, research_reader=reader)
    c1 = _client(deps)
    assert c1.post("/api/v1/lineage/entity/reel-A").status_code != 200
    assert c1.delete("/api/v1/lineage/run/exec-1").status_code != 200
