"""B2 + B4: the reel-af durable-cursor consumer of ``research.completed``.

B2 — read events with ``Seq > cursor`` (type-filtered), dedup on the CloudEvents ``id``
before side effects, advance the cursor only after the effect, catch up after downtime,
and never stall on a malformed event; never ride the in-memory bus (A1).

B4 — the idempotent one-transaction stamp: deliver/replay once → one effect; the stamp
value is the resolved LOCAL UUID (never the text execution_id); unknown correlation is
mark+advance with no stamp (C7); no cross-tenant write; consumer writes no owner table (A3).
"""

from __future__ import annotations

import inspect

import pg
from conftest import ORG_ID, OTHER_ORG, _ConsumerState, make_deps, make_event
from events import consume_since

CONSUMER = "reel-af"


# ─────────────────────────── B2: read + dedup + cursor ───────────────────────────


def test_reads_only_events_after_cursor_type_filtered_in_order():
    reader = __import__("conftest").FakeEventReader([
        make_event(5, id="a", subject="exec-a"),
        make_event(6, id="b", subject="exec-b"),
        make_event(4, id="stale", subject="exec-old"),          # <= cursor, skipped
        {"id": "other", "type": "reel.completed", "subject": "x", "sequence": 7},  # wrong type
    ])
    deps = make_deps(events=reader)
    high = consume_since(deps, cursor=4, consumer=CONSUMER)
    assert reader.read_calls == [(4, "com.silmari.research.completed.v1", 100)]
    # only research.completed with Seq > 4 processed, in order
    assert [row[0] for row in deps.processed.state.processed_rows] == ["a", "b"]
    assert high == 6
    assert deps.cursor.get(CONSUMER) == 6


def test_dedup_replay_is_noop_but_cursor_still_advances():
    state = _ConsumerState()
    state.processed.add("dup")                                   # already processed
    reader = __import__("conftest").FakeEventReader([make_event(9, id="dup", subject="exec-d")])
    deps = make_deps(events=reader, consumer_state=state)
    consume_since(deps, cursor=8, consumer=CONSUMER)
    # no second processed row; cursor advanced past the deduped event
    assert state.processed_rows == []
    assert state.cursors[CONSUMER] == 9


def test_downtime_catchup_reads_all_after_cursor():
    reader = __import__("conftest").FakeEventReader(
        [make_event(n, id=f"e{n}", subject=f"exec-{n}") for n in (11, 12, 13)]
    )
    deps = make_deps(events=reader)
    consume_since(deps, cursor=10, consumer=CONSUMER)
    assert [row[0] for row in deps.processed.state.processed_rows] == ["e11", "e12", "e13"]
    assert deps.cursor.get(CONSUMER) == 13


def test_malformed_event_marked_and_advanced_never_stalls():
    reader = __import__("conftest").FakeEventReader([
        {"id": "", "type": "com.silmari.research.completed.v1", "subject": "exec-x", "sequence": 21},  # no id
        {"id": "noSubject", "type": "com.silmari.research.completed.v1", "subject": "", "sequence": 22},  # no subj
        make_event(23, id="good", subject="exec-good"),
    ])
    deps = make_deps(events=reader)
    high = consume_since(deps, cursor=20, consumer=CONSUMER)
    # the poison rows do not stall — cursor advances past them and the good event processes
    assert deps.cursor.get(CONSUMER) == 23
    assert high == 23
    assert ("good", "exec-good") in deps.processed.state.processed_rows
    # the missing-id row cannot be marked (no PK) but never stalls; the missing-subject row is marked
    assert any(cid == "noSubject" for cid, _ in deps.processed.state.processed_rows)


def test_never_subscribes_to_in_memory_bus():
    reader = __import__("conftest").FakeEventReader([make_event(1, id="z", subject="exec-z")])
    deps = make_deps(events=reader)
    consume_since(deps, cursor=0, consumer=CONSUMER)
    assert reader.subscribed_bus is False                       # A1 — durable surface only


def test_dispatched_case_tight_subject_filter_same_read_path():
    reader = __import__("conftest").FakeEventReader([
        make_event(31, id="mine", subject="exec-mine"),
        make_event(32, id="theirs", subject="exec-theirs"),
    ])
    deps = make_deps(events=reader)
    consume_since(deps, cursor=30, consumer=CONSUMER, subject="exec-mine")
    processed_ids = [row[0] for row in deps.processed.state.processed_rows]
    assert processed_ids == ["mine"]                            # only my dispatched execution
    assert deps.cursor.get(CONSUMER) == 32                      # cursor still advances past theirs


# ─────────────────────────── B4: idempotent one-TX stamp ───────────────────────────


def test_fresh_event_stamps_resolved_local_uuid_not_execution_id():
    state = _ConsumerState()
    run = state.seed_local_run("exec-42", ORG_ID)
    state.seed_reel_row(ORG_ID, "exec-42")
    reader = __import__("conftest").FakeEventReader([make_event(41, id="m", subject="exec-42")])
    deps = make_deps(events=reader, consumer_state=state)
    consume_since(deps, cursor=40, consumer=CONSUMER)
    stamped = state.stamp_of(ORG_ID, "exec-42")
    assert stamped == run.id                                    # the LOCAL UUID
    assert stamped != "exec-42"                                 # never the text execution_id


def test_replay_same_event_twice_single_stamp_effect():
    state = _ConsumerState()
    run = state.seed_local_run("exec-50", ORG_ID)
    state.seed_reel_row(ORG_ID, "exec-50")
    event = make_event(51, id="once", subject="exec-50")
    reader = __import__("conftest").FakeEventReader([event])
    deps = make_deps(events=reader, consumer_state=state)
    consume_since(deps, cursor=50, consumer=CONSUMER)            # first delivery
    consume_since(deps, cursor=50, consumer=CONSUMER)            # replay (cursor reset)
    assert state.stamp_of(ORG_ID, "exec-50") == run.id
    assert [row[0] for row in state.processed_rows] == ["once"]  # one processed row only


def test_unknown_correlation_marks_and_advances_no_stamp():
    state = _ConsumerState()                                    # no local run seeded
    state.seed_reel_row(ORG_ID, "exec-unk")
    reader = __import__("conftest").FakeEventReader([make_event(61, id="u", subject="exec-unk")])
    deps = make_deps(events=reader, consumer_state=state)
    consume_since(deps, cursor=60, consumer=CONSUMER)
    assert ("u", "exec-unk") in state.processed_rows            # dedup-marked
    assert state.cursors[CONSUMER] == 61                        # advanced
    assert state.stamp_of(ORG_ID, "exec-unk") is None           # NO stamp (C7, not dead-letter)


def test_cross_tenant_never_stamps_other_org():
    state = _ConsumerState()
    run = state.seed_local_run("exec-70", ORG_ID)               # resolves to ORG_ID
    state.seed_reel_row(ORG_ID, "exec-70")
    state.seed_reel_row(OTHER_ORG, "exec-70")                   # accidental id overlap in ORG_B
    reader = __import__("conftest").FakeEventReader([make_event(71, id="t", subject="exec-70")])
    deps = make_deps(events=reader, consumer_state=state)
    consume_since(deps, cursor=70, consumer=CONSUMER)
    assert state.stamp_of(ORG_ID, "exec-70") == run.id
    assert state.stamp_of(OTHER_ORG, "exec-70") is None         # ORG_B untouched (C6)


def test_stamp_is_null_guarded_does_not_overwrite_existing():
    state = _ConsumerState()
    state.seed_local_run("exec-80", ORG_ID)
    state.reel_rows[(ORG_ID, "exec-80")] = {"source_research_run_id": "PRE-EXISTING"}
    reader = __import__("conftest").FakeEventReader([make_event(81, id="n", subject="exec-80")])
    deps = make_deps(events=reader, consumer_state=state)
    consume_since(deps, cursor=80, consumer=CONSUMER)
    assert state.stamp_of(ORG_ID, "exec-80") == "PRE-EXISTING"  # null-guard preserved it


def test_local_run_resolver_port_resolves_execution_id_to_local_run():
    # LocalRunResolverPort is the declared resolution seam (execution_id → local research_run);
    # production stamping resolves inline in the one-tx effect (C5), but the port is the
    # exposed, tested surface used to resolve the dispatched-run (§1a) / by other INT phases.
    state = _ConsumerState()
    run = state.seed_local_run("exec-90", ORG_ID)
    deps = make_deps(consumer_state=state)
    assert deps.local_runs.resolve("exec-90") == run          # resolves the local UUID row
    assert deps.local_runs.resolve("exec-absent") is None      # unknown → None (not an error)


def test_consumer_store_never_writes_owner_research_run_table():
    # A3 / C-Own (source-level guard): PgEventConsumerStore writes only reel-af's own tables.
    # No INSERT/UPDATE/DELETE into deepresearch.research_run appears anywhere in the class.
    src = inspect.getsource(pg.PgEventConsumerStore).lower()
    for forbidden in (
        "insert into deepresearch.research_run",
        "update deepresearch.research_run",
        "delete from deepresearch.research_run",
    ):
        assert forbidden not in src
    # the only research_run touch is a SELECT (resolution read).
    assert "select id, org_id, created_by from deepresearch.research_run" in src


# ─────────────────────────── B3: middleware handler adapter ───────────────────────────


def test_middleware_handler_stamps_via_stamp_dedup_advance():
    """B3: _build_research_handler produces a handler that stamps like consume_since."""
    from types import SimpleNamespace
    from events import _build_research_handler

    state = _ConsumerState()
    run = state.seed_local_run("exec-mw", ORG_ID)
    state.seed_reel_row(ORG_ID, "exec-mw")
    deps = make_deps(consumer_state=state)

    handler = _build_research_handler(deps)
    dto = SimpleNamespace(
        event_id="mw-1",
        execution_id="exec-mw",
        event_type="com.silmari.research.completed.v1",
        sequence=101,
        data={"run_id": "r1", "status": "succeeded"},
    )
    handler(dto, lambda eid: None)

    assert state.stamp_of(ORG_ID, "exec-mw") == run.id
    assert state.cursors["reel-af"] == 101
    assert ("mw-1", "exec-mw") in state.processed_rows


def test_middleware_handler_dedup_replay_is_noop():
    """B3: handler is idempotent — already-processed events are skipped."""
    from types import SimpleNamespace
    from events import _build_research_handler

    state = _ConsumerState()
    state.processed.add("already")
    deps = make_deps(consumer_state=state)

    handler = _build_research_handler(deps)
    dto = SimpleNamespace(
        event_id="already",
        execution_id="exec-dup",
        event_type="com.silmari.research.completed.v1",
        sequence=200,
        data={},
    )
    handler(dto, lambda eid: None)

    assert state.cursors["reel-af"] == 0
    assert len(state.processed_rows) == 0


def test_middleware_handler_fetch_failure_does_not_block_stamp():
    """B3: fetch_body failure is non-blocking — stamp still applies."""
    from types import SimpleNamespace
    from events import _build_research_handler

    state = _ConsumerState()
    state.seed_local_run("exec-fetch-fail", ORG_ID)
    state.seed_reel_row(ORG_ID, "exec-fetch-fail")
    deps = make_deps(consumer_state=state)

    handler = _build_research_handler(deps)
    dto = SimpleNamespace(
        event_id="ff-1",
        execution_id="exec-fetch-fail",
        event_type="com.silmari.research.completed.v1",
        sequence=301,
        data={},
    )

    def _boom(eid):
        raise ConnectionError("CP unreachable")

    handler(dto, _boom)

    assert state.stamp_of(ORG_ID, "exec-fetch-fail") is not None
    assert state.cursors["reel-af"] == 301
