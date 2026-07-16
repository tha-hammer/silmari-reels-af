"""B15a → orphan log carries `target`; _dispatch_one stops aborting the fan-out.

SCOPE: the ZERO-MIGRATION half. The durable orphan record + repair path (B15b) is
DEFERRED — it is unreachable in its own failure mode (the trigger IS Postgres
unavailability, so writing a row to that same Postgres fails exactly when it
matters) and this repo owns no migrations at all (web/pg.py: root-owned, in the
monorepo root). Recommended follow-up is a CP-reconciling sweep over
mark_stale_queued's existing `status='queued' AND execution_id IS NULL` predicate
— the reel_job row already IS the durable record.

N-2: _DispatchOutcome exposes `.ok` / `.execution_id` / `.outcome` (server.py).
The plan asserted `.disposition`, which does not exist — "disposition" is the
prose term in _dispatch_one's own docstring. These tests use the real field.
"""

from __future__ import annotations

import logging
import uuid

import server
from conftest import ORG_ID, FakeControlPlane, FakeIdentity, FakeReelJobRepo, make_ctx, make_deps
from deps import RepositoryUnavailable
from reel_jobs import TARGET_DSL_HOOKS, TARGET_TEXT_CAROUSEL, TARGET_TEXT_REEL, build_submission

A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"
DSL_HOOKS_URL = f"/api/v1/execute/async/{TARGET_DSL_HOOKS}"

VALID_A1_INPUT = {
    "source_url": A1_SOURCE_URL,
    "composite_ref": "a1://runs/r1/composite.ts.md",
    "words_ref": "a1://runs/r1/transcript.words.json",
    "hook_ref": "a1://runs/r1/hook-plan.json",
    "clip_idx": 1,
}


class _AttachFailsRepo(FakeReelJobRepo):
    """Attach raises like a Postgres outage — the real orphan trigger."""

    def attach_execution_id(self, ctx, job_id, execution_id):
        raise RepositoryUnavailable("db down")


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


# ── B15a: the submit-path orphan log carries `target` ──────────────


def test_orphaned_dispatch_log_includes_target(caplog):
    repo = _AttachFailsRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_1"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    with caplog.at_level(logging.ERROR):
        resp = _client(deps).post(DSL_HOOKS_URL, json={"input": VALID_A1_INPUT})

    assert resp.status_code == 503                     # unchanged behavior
    messages = [r.getMessage() for r in caplog.records if "orphaned_dispatch" in r.getMessage()]
    assert messages, "no orphaned_dispatch log emitted"
    logged = messages[0]
    assert TARGET_DSL_HOOKS in logged                  # RED: target omitted today
    assert "exec_1" in logged
    assert str(ORG_ID) in logged


def test_orphaned_dispatch_log_keeps_its_existing_fields(caplog):
    """The log already carried these — adding `target` must not drop them."""
    repo = _AttachFailsRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_2"}, {}))
    ctx = make_ctx()
    deps = make_deps(identity=FakeIdentity(ctx), reel_jobs=repo, control_plane=cp)

    with caplog.at_level(logging.ERROR):
        _client(deps).post(
            DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, "client_request_id": "crid-9"}}
        )

    logged = next(r.getMessage() for r in caplog.records if "orphaned_dispatch" in r.getMessage())
    for expected in ("exec_2", str(ctx.org_id), str(ctx.user_id), "crid-9"):
        assert expected in logged


# ── B15a: the fan-out path stops breaching its contract ────────────


def test_fanout_attach_failure_returns_outcome_instead_of_raising():
    """_dispatch_one's attach is unguarded today: the HttpError escapes the
    function entirely, violating its documented "returns a disposition instead of
    raising" contract and aborting the whole fan-out at its call site."""
    repo = _AttachFailsRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_3"}, {}))
    ctx = make_ctx()
    deps = make_deps(identity=FakeIdentity(ctx), reel_jobs=repo, control_plane=cp)
    submission = build_submission(TARGET_TEXT_REEL, {"input": {"text": "some research text"}})

    outcome = server._dispatch_one(
        deps, ctx, TARGET_TEXT_REEL, submission, uuid.uuid4(), "crid-1", deps.clock.now()
    )

    assert outcome.ok is False
    assert outcome.outcome == "attach_failed"          # N-2: `.outcome`, not `.disposition`
    assert outcome.execution_id is None


def test_fanout_attach_failure_logs_orphaned_dispatch_with_target(caplog):
    repo = _AttachFailsRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_4"}, {}))
    ctx = make_ctx()
    deps = make_deps(identity=FakeIdentity(ctx), reel_jobs=repo, control_plane=cp)
    submission = build_submission(TARGET_TEXT_REEL, {"input": {"text": "some research text"}})

    with caplog.at_level(logging.ERROR):
        server._dispatch_one(
            deps, ctx, TARGET_TEXT_REEL, submission, uuid.uuid4(), "crid-1", deps.clock.now()
        )

    logged = next(r.getMessage() for r in caplog.records if "orphaned_dispatch" in r.getMessage())
    assert TARGET_TEXT_REEL in logged
    assert "exec_4" in logged


def test_fanout_sibling_dispatches_survive_one_orphan():
    """The contract breach: one bad attach must not take down the whole batch."""
    ctx = make_ctx()
    calls: list[str] = []

    class _OneBadAttachRepo(FakeReelJobRepo):
        def attach_execution_id(self, ctx, job_id, execution_id):
            calls.append(execution_id)
            if len(calls) == 1:
                raise RepositoryUnavailable("db blip")
            return super().attach_execution_id(ctx, job_id, execution_id)

    repo = _OneBadAttachRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_5"}, {}))
    deps = make_deps(identity=FakeIdentity(ctx), reel_jobs=repo, control_plane=cp)

    outcomes = [
        server._dispatch_one(
            deps, ctx, target, build_submission(target, {"input": {"text": "t"}}),
            uuid.uuid4(), f"crid:{target}", deps.clock.now(),
        )
        for target in (TARGET_TEXT_REEL, TARGET_TEXT_CAROUSEL)
    ]

    assert outcomes[0].ok is False and outcomes[0].outcome == "attach_failed"
    assert outcomes[1].ok is True                       # sibling survived
    assert outcomes[1].outcome == "enqueued"


def test_successful_attach_is_unaffected():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_6"}, {}))
    ctx = make_ctx()
    deps = make_deps(identity=FakeIdentity(ctx), reel_jobs=repo, control_plane=cp)
    submission = build_submission(TARGET_TEXT_REEL, {"input": {"text": "t"}})

    outcome = server._dispatch_one(
        deps, ctx, TARGET_TEXT_REEL, submission, uuid.uuid4(), "crid-ok", deps.clock.now()
    )

    assert outcome.ok is True
    assert outcome.outcome == "enqueued"
    assert outcome.execution_id == "exec_6"


def test_no_new_table_was_introduced():
    """B15b is deferred: this slice must add no deepresearch.* schema."""
    from pg import FEATURE_SCHEMA

    assert not any("orphan" in table.lower() for table in FEATURE_SCHEMA)
