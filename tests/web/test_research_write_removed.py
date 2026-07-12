"""INT Phase 0 · Behavior 2 — ANTI: reel-af issues ZERO writes to the owner's
``research_run`` table on the dispatch path.

Drives ``POST /api/v1/research/run`` end-to-end and asserts, via the live
``_record_event`` spy on ``FakeReelJobRepo``, that neither ``insert_research_run``
nor ``update_research_status`` is ever called and no owner-table row is minted —
while dispatch still happens exactly once and the reel-af-owned handle is returned.
"""

from __future__ import annotations

import server
from conftest import FakeControlPlane, FakeIdentity, FakeReelJobRepo, make_ctx, make_deps

RUN = "/api/v1/research/run"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def test_dispatch_issues_zero_owner_table_writes():
    repo = FakeReelJobRepo()
    repo.events = []                                        # arm the spy (conftest _record_event hook)
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_1"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    r = _client(deps).post(RUN, json={"query": "q"})

    assert r.status_code == 202
    # ANTI: reel-af NEVER writes the owner's research_run table
    assert "insert_research_run" not in repo.events
    assert "update_research_status" not in repo.events
    assert repo.research_runs == {}                         # no owner-table row minted
    assert len(cp.dispatch_calls) == 1                      # positive control: dispatch still happens
    body = r.get_json()                                     # reel-af's own handle + the execution_id
    assert body["execution_id"] == "exec_1"
    assert "research_run_id" in body


def test_dispatch_failure_still_writes_no_owner_row():
    repo = FakeReelJobRepo()
    repo.events = []
    cp = FakeControlPlane(response=(502, {"error": "down"}, {}))   # CP failure branch
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    r = _client(deps).post(RUN, json={"query": "q"})

    assert r.status_code == 502
    assert repo.events == []                                # NO fallback owner-table write on failure
    assert repo.research_runs == {}


def test_poll_issues_no_owner_table_write():
    # ISC-1 covers the poll path too: polling reconciles nothing into the owner table.
    repo = FakeReelJobRepo()
    repo.seed_research_run(execution_id="exec_p", org_id=make_ctx().org_id,
                           created_by=make_ctx().user_id)
    repo.events = []                                        # arm AFTER seeding
    cp = FakeControlPlane(response=(200, {"status": "succeeded", "result": {}}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    r = _client(deps).get("/api/v1/research/exec_p")

    assert r.status_code == 200
    assert "update_research_status" not in repo.events      # no status reconcile write
    assert "insert_research_run" not in repo.events
