"""Plan 4 — Cross-node research handoff + provenance (unit, fake-first).

Behaviors covered here (unit, no DB):
- B1 (ISC-22): POST /api/v1/research/run dispatches only-`query` + defaults to
  `meta_deep_research.execute_deep_research`.
- B2 (ISC-23): GET /api/v1/research/<execution_id> polls + surfaces status.
- B3 (ISC-24): a dispatched run is recorded as an owned `research_run` row.
- B4 (ISC-25): a create-from-research submit stamps + reads back provenance.

The Postgres SQL contract + closure round-trips live in
tests/web/integration/test_pg_research_run.py.
"""

from __future__ import annotations

import uuid

import pytest
import server
from conftest import FakeControlPlane, FakeIdentity, make_ctx, make_deps
from deps import AuthContext, BadRequest
from reel_jobs import (
    RESEARCH_DEFAULTS,
    TARGET_COMPOSITE,
    TARGET_RESEARCH,
    build_research_dispatch,
    build_submission,
)

RESEARCH_URL = "/api/v1/research/run"
COMPOSITE_URL = f"/api/v1/execute/async/{TARGET_COMPOSITE}"
OTHER_ORG = uuid.UUID("33333333-3333-3333-3333-333333333333")
OTHER_USER = uuid.UUID("44444444-4444-4444-4444-444444444444")

_DEFAULTS_KEYSET = {
    "research_focus",
    "research_scope",
    "max_research_loops",
    "num_parallel_streams",
    "analysis_depth",
    "source_strictness",
    "tension_lens",
    "mode",
    "evidence_style",
}


# ───────────── Behavior 1 builder (unit, no route) — ISC-22 property ─────────────


def test_build_research_dispatch_only_query_plus_defaults():
    target, body = build_research_dispatch({"query": "  fusion startups  "})
    assert target == TARGET_RESEARCH == "meta_deep_research.execute_deep_research"
    assert body["input"]["query"] == "fusion startups"  # trimmed
    # property: full defaults keyset always present; no model/api_key/query leaks in
    assert _DEFAULTS_KEYSET <= set(body["input"])
    assert "model" not in body["input"] and "api_key" not in body["input"]
    assert body["input"]["mode"] == "general"
    assert body["input"]["num_parallel_streams"] == 2  # matches ui/defaults.json


def test_build_research_dispatch_mode_override_and_rejections():
    _, body = build_research_dispatch({"query": "x", "mode": "bear"})
    assert body["input"]["mode"] == "bear"
    with pytest.raises(BadRequest):
        build_research_dispatch({"query": "   "})  # empty/whitespace query
    with pytest.raises(BadRequest):
        build_research_dispatch({"query": "x", "org_id": "evil"})  # forbidden identity


def test_research_defaults_exclude_secrets():
    assert set(RESEARCH_DEFAULTS) == _DEFAULTS_KEYSET
    assert "api_key" not in RESEARCH_DEFAULTS and "model" not in RESEARCH_DEFAULTS


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


# ─────────────────────── Behavior 1 (ISC-22) ───────────────────────


def test_research_run_dispatches_only_query_plus_defaults():
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_r1"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), control_plane=cp)

    resp = _client(deps).post(RESEARCH_URL, json={"query": "  fusion startups  "})

    assert resp.status_code in (200, 202)
    assert len(cp.dispatch_calls) == 1
    target, body = cp.dispatch_calls[0]
    assert target == "meta_deep_research.execute_deep_research"
    assert body["input"]["query"] == "fusion startups"
    # full defaults keyset present
    for k in (
        "research_focus",
        "research_scope",
        "max_research_loops",
        "num_parallel_streams",
        "analysis_depth",
        "source_strictness",
        "tension_lens",
        "mode",
        "evidence_style",
    ):
        assert k in body["input"]
    assert body["input"]["mode"] == "general"


def test_mode_override_and_empty_query_rejected():
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_r2"}, {}))
    deps = make_deps(control_plane=cp)
    assert (
        _client(deps).post(RESEARCH_URL, json={"query": "x", "mode": "bear"}).status_code
        in (200, 202)
    )
    assert cp.dispatch_calls[-1][1]["input"]["mode"] == "bear"
    assert _client(deps).post(RESEARCH_URL, json={"query": "   "}).status_code == 400


class RecordingControlPlane(FakeControlPlane):
    def __init__(self, *, events: list[str] | None = None, dispatch_response=None, poll_response=None):
        super().__init__(response=dispatch_response or (202, {"execution_id": "exec_r1"}, {}))
        self.events = events
        self._poll_response = poll_response or (200, {"status": "succeeded"}, {})

    def dispatch_async(self, target, body):
        if self.events is not None:
            self.events.append("dispatch")
        return super().dispatch_async(target, body)

    def get_execution(self, execution_id):
        self.get_calls.append(execution_id)
        return self._poll_response


# ─────────────────────── Behavior 2 (ISC-23) ───────────────────────


def test_research_poll_reconciles_and_returns_document():
    cp = RecordingControlPlane(
        poll_response=(
            200,
            {
                "status": "succeeded",
                "result": {
                    "markdown": "# R",
                    "html": "<h1>R</h1>",
                    "sources": [{"title": "S"}],
                },
            },
            {},
        )
    )
    deps = make_deps(control_plane=cp)
    deps.reel_jobs.seed_research_run(
        execution_id="exec_r1",
        org_id=make_ctx().org_id,
        created_by=make_ctx().user_id,
    )

    resp = _client(deps).get("/api/v1/research/exec_r1")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "succeeded"
    assert body["markdown"] == "# R"
    assert body["html"] == "<h1>R</h1>"
    assert body["sources"] == [{"title": "S"}]
    assert cp.get_calls == ["exec_r1"]


def test_research_poll_foreign_run_is_404():
    foreign = AuthContext(
        user_id=OTHER_USER,
        org_id=OTHER_ORG,
        role="member",
        supertokens_user_id="st-2",
    )
    cp = RecordingControlPlane(poll_response=(200, {"status": "running"}, {}))
    deps = make_deps(identity=FakeIdentity(foreign), control_plane=cp)
    deps.reel_jobs.seed_research_run(
        execution_id="exec_r1",
        org_id=make_ctx().org_id,
        created_by=make_ctx().user_id,
    )

    resp = _client(deps).get("/api/v1/research/exec_r1")

    assert resp.status_code == 404
    assert cp.get_calls == []


# ─────────────────────── Behavior 3 (ISC-24) ───────────────────────


def test_research_run_dispatches_without_owner_table_write():
    # INT Phase 0: the deep-research node OWNS research_run — reel-af dispatches and
    # returns its OWN handle + the owner execution_id, but writes NOTHING to the owner table.
    events: list[str] = []
    cp = RecordingControlPlane(events=events, dispatch_response=(202, {"execution_id": "exec_r9"}, {}))
    deps = make_deps(control_plane=cp)
    deps.reel_jobs.events = events

    resp = _client(deps).post(RESEARCH_URL, json={"query": "grid storage"})

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["research_run_id"] == str(uuid.UUID(body["research_run_id"]))  # reel-af's own handle
    assert body["execution_id"] == "exec_r9"
    assert events == ["dispatch"]                       # only the dispatch — no insert/update
    assert "insert_research_run" not in events
    assert "update_research_status" not in events
    assert deps.reel_jobs.research_runs == {}           # no owner-table row minted


def test_research_run_missing_execution_id_passes_through_without_owner_write():
    # CP returns 2xx but no execution_id -> passthrough of the CP body/status (INT Phase 0
    # removed the row-first bookkeeping that used to synthesize a 502); still NO owner write.
    events: list[str] = []
    cp = RecordingControlPlane(
        events=events, dispatch_response=(202, {"status": "accepted_without_execution"}, {}))
    deps = make_deps(control_plane=cp)
    deps.reel_jobs.events = events

    resp = _client(deps).post(RESEARCH_URL, json={"query": "grid storage"})

    assert resp.status_code == 202                      # passthrough of CP status
    assert deps.reel_jobs.research_runs == {}
    assert "insert_research_run" not in events
    assert "update_research_status" not in events


# ─────────────────────── Behavior 4 (ISC-25) ───────────────────────


def test_research_provenance_keys_do_not_leak_to_reasoner_input():
    rid = uuid.uuid4()
    sub = build_submission(
        TARGET_COMPOSITE,
        {
            "input": {
                "url": "https://x.test/a",
                "preset": "carousel-default",
                "research_run_id": str(rid),
                "source_research_run_id": str(rid),
            }
        },
        source_research_run_id=rid,
    )

    assert sub.source_research_run_id == rid
    assert "research_run_id" not in sub.cp_input
    assert "source_research_run_id" not in sub.cp_input
    assert "research_run_id" not in sub.params
    assert "source_research_run_id" not in sub.params


def test_create_from_research_stamps_and_reads_back_provenance():
    cp = RecordingControlPlane(
        dispatch_response=(202, {"execution_id": "exec_c1"}, {}),
        poll_response=(200, {"status": "succeeded"}, {}),
    )
    deps = make_deps(control_plane=cp)
    rid = deps.reel_jobs.seed_research_run(
        execution_id="exec_r1",
        org_id=make_ctx().org_id,
        created_by=make_ctx().user_id,
    )

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={
            "research_run_id": str(rid),
            "input": {"url": "https://x.test/a", "preset": "carousel-default"},
        },
    )

    assert resp.status_code == 202
    read = _client(deps).get("/api/v1/executions/exec_c1").get_json()
    assert read["source_research_run_id"] == str(rid)


def test_cross_org_research_run_is_not_stamped():
    cp = RecordingControlPlane(dispatch_response=(202, {"execution_id": "exec_c2"}, {}))
    deps = make_deps(control_plane=cp)
    rid = deps.reel_jobs.seed_research_run(
        execution_id="exec_rx",
        org_id=OTHER_ORG,
        created_by=OTHER_USER,
    )

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={
            "research_run_id": str(rid),
            "input": {"url": "https://x.test/a", "preset": "carousel-default"},
        },
    )

    assert resp.status_code == 404
    assert cp.dispatch_calls == []


def test_malformed_research_run_id_is_400_no_work():
    cp = RecordingControlPlane(dispatch_response=(202, {"execution_id": "exec_bad"}, {}))
    deps = make_deps(control_plane=cp)

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={
            "research_run_id": "not-a-uuid",
            "input": {"url": "https://x.test/a", "preset": "carousel-default"},
        },
    )

    assert resp.status_code == 400
    assert deps.reel_jobs.inserted == []
    assert cp.dispatch_calls == []
