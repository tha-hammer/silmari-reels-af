"""INT Phase 0 · Behavior 4 — provenance stays reel-af-owned (REGRESSION).

Removing reel-af's illegal writes to the OWNER's ``research_run`` table (Behavior 2)
must NOT touch reel-af's OWN provenance column ``source_research_run_id``. This guards
that create-from-research still stamps it on the reel-af-owned row, and that the id
never leaks into the reasoner input (existing ``_CP_STRIP`` behavior).
"""

from __future__ import annotations

import server
from conftest import FakeControlPlane, FakeIdentity, FakeReelJobRepo, make_ctx, make_deps

COMPOSITE_URL = "/api/v1/execute/async/reel-af.reel_composite_to_reel"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def test_create_from_research_still_stamps_source_research_run_id():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_1"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    rid = repo.seed_research_run(                          # tenancy owns rr in this org
        execution_id="exec_seed", org_id=make_ctx().org_id, created_by=make_ctx().user_id)

    resp = _client(deps).post(COMPOSITE_URL, json={
        "research_run_id": str(rid),
        "input": {"url": "https://x.test/a", "preset": "carousel-default"},
    })

    assert resp.status_code == 202
    # REGRESSION: reel-af's OWN provenance column is still stamped on the inserted row.
    _ctx, submission, _job_id, _now, _crid = repo.inserted[-1]
    assert submission.source_research_run_id == rid
    # ...and the id never leaked into the reasoner input (existing _CP_STRIP behavior).
    _target, dispatched = cp.dispatch_calls[0]
    assert str(rid) not in str(dispatched)


def test_no_research_run_id_leaves_provenance_none():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_2"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(COMPOSITE_URL, json={
        "input": {"url": "https://x.test/a", "preset": "carousel-default"},
    })

    assert resp.status_code == 202
    _ctx, submission, _job_id, _now, _crid = repo.inserted[-1]
    assert submission.source_research_run_id is None      # unchanged; not required
