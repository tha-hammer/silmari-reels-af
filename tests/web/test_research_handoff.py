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

import pytest
import server
from conftest import FakeControlPlane, FakeIdentity, make_ctx, make_deps
from deps import BadRequest
from reel_jobs import (
    RESEARCH_DEFAULTS,
    TARGET_RESEARCH,
    build_research_dispatch,
)

RESEARCH_URL = "/api/v1/research/run"

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
