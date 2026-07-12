"""INT-04 Cross-App Lineage View — service-level behaviors (B1–B6).

A reel-af-owned, read-only, ORG-SCOPED read model over provenance already in place
(``source_research_run_id`` on ``reel_job``/``carousel`` → reel-af ``research_run`` →
``execution_id``). Forward = entity→run; reverse = run→entities. Non-owner: writes
nothing, reads no owner table by SQL, reaches a control-plane record only by-id.

Uses the real ``make_deps`` + fake org-scoped repos (the fakes enforce ``ctx.org_id``,
so the cross-org conceal is real, not mocked). No outbox/cursor/projector exists.
"""

from __future__ import annotations

import inspect
import re
import uuid

from conftest import (  # noqa: E402 - conftest puts web/ + fakes on the path
    ORG_ID,
    OTHER_ORG,
    USER_ID,
    FakeCarouselRepo,
    FakeReelJobRepo,
    FakeResearchRunReader,
    make_deps,
)
from deps import AuthContext, BadGateway  # noqa: E402
from lineage import LineageView  # noqa: E402

THIRD_ORG = uuid.UUID("55555555-5555-5555-5555-555555555555")


def ctx_for(org: uuid.UUID) -> AuthContext:
    return AuthContext(user_id=USER_ID, org_id=org, role="member", supertokens_user_id="st")


# ─────────────────────────── seed helpers (source only) ───────────────────────────


def _view_with(reel_repo, car_repo, reader=None):
    deps = make_deps(reel_jobs=reel_repo, carousels=car_repo,
                     research_reader=reader or FakeResearchRunReader())
    return LineageView(deps)


def one_reel_with_provenance(org=ORG_ID, entity="reel-7", execution_id="exec-42",
                             detail=None):
    reel = FakeReelJobRepo()
    car = FakeCarouselRepo()
    run_id = reel.seed_research_run(execution_id, org, USER_ID, status="succeeded")
    reel.seed_reel_job(entity, org, source_research_run_id=run_id)
    reader = FakeResearchRunReader(details={execution_id: (detail or {"title": "T",
                                                                      "result_ref": "r"})})
    return reel, car, reader, run_id


# ─────────────────────────── B1: view over data already in place ───────────────────────────


def test_view_adds_no_new_schema_or_plumbing():
    # The blast radius is a service + two read-only reverse lookups + two GET routes.
    from pg import PgCarouselRepo, PgReelJobRepo

    reverse_sql = (
        inspect.getsource(PgReelJobRepo.reel_jobs_by_source_run)
        + inspect.getsource(PgCarouselRepo.carousels_by_source_run)
        + inspect.getsource(PgReelJobRepo.get)
    )
    # (a) reverse lookups are SELECT-only against reel-af's OWN tables — no writes.
    assert re.search(r"\bselect\b", reverse_sql, re.I)
    assert not re.search(r"\b(insert|update|delete|create\s+table)\b", reverse_sql, re.I)
    # (b) they name only reel-af-owned tables.
    assert "deepresearch.reel_job" in reverse_sql
    assert "deepresearch.carousel" in reverse_sql
    # (c) the service module DEFINES no outbox/cursor/projector/edge-table/event plumbing
    # (checked over defined identifiers via AST — prose in the docstring is not a definition).
    import ast

    import lineage

    tree = ast.parse(inspect.getsource(lineage))
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name.lower())
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id.lower())
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            defined.update(alias.name.lower() for alias in node.names)
    for banned in ("outbox", "projector", "cursor", "lineage_edge", "lineage_upstream", "event_type"):
        assert not any(banned in name for name in defined), \
            f"lineage.py must not define/import {banned!r}"


# ─────────────────────────── B2: forward — what_produced ───────────────────────────


def test_what_produced_resolves_entity_to_upstream_run():
    reel, car, reader, _run = one_reel_with_provenance(entity="reel-7", execution_id="exec-42")
    view = _view_with(reel, car, reader)
    up = view.what_produced(ctx_for(ORG_ID), "reel-7")
    assert [u.execution_id for u in up] == ["exec-42"]      # upstream run, reel-af-owned link
    assert up[0].status == "succeeded"
    assert up[0].title == "T"                               # owner-interface enrichment (A1, reachable)


def test_what_produced_resolves_carousel_identically():
    reel = FakeReelJobRepo()
    car = FakeCarouselRepo()
    run_id = reel.seed_research_run("exec-9", ORG_ID, USER_ID, status="succeeded")
    car.seed(ORG_ID, "carousel-C", source_research_run_id=run_id)
    view = _view_with(reel, car, FakeResearchRunReader(details={"exec-9": {"title": "C"}}))
    up = view.what_produced(ctx_for(ORG_ID), "carousel-C")
    assert [u.execution_id for u in up] == ["exec-9"]
    assert up[0].title == "C"


def test_what_produced_enrichment_fails_open_on_bad_gateway():
    reel, car, _reader, _run = one_reel_with_provenance(entity="reel-7", execution_id="exec-42")

    class _DownReader:
        def read(self, ctx, execution_id):
            raise BadGateway("owner interface down")

    view = _view_with(reel, car, _DownReader())
    up = view.what_produced(ctx_for(ORG_ID), "reel-7")
    assert [u.execution_id for u in up] == ["exec-42"]      # link still returned (fail-open)
    assert up[0].title is None                              # enrichment sugar nulled, not an error


def test_what_produced_no_provenance_or_cross_org_is_empty():
    reel = FakeReelJobRepo()
    reel.seed_reel_job("reel-9", ORG_ID, source_research_run_id=None)   # no provenance
    view = _view_with(reel, FakeCarouselRepo())
    assert view.what_produced(ctx_for(ORG_ID), "reel-9") == []          # no provenance -> empty
    assert view.what_produced(ctx_for(ORG_ID), "unknown") == []         # unknown -> empty
    assert view.what_produced(ctx_for(OTHER_ORG), "reel-9") == []       # other org -> concealed


# ─────────────────────────── B3: reverse — what_came_from ───────────────────────────


def _run_with_downstreams():
    reel = FakeReelJobRepo()
    car = FakeCarouselRepo()
    run_id = reel.seed_research_run("exec-1", ORG_ID, USER_ID, status="succeeded")
    reel.seed_reel_job("reel-A", ORG_ID, source_research_run_id=run_id)
    reel.seed_reel_job("reel-B", ORG_ID, source_research_run_id=run_id)
    car.seed(ORG_ID, "carousel-C", source_research_run_id=run_id)
    reel.seed_reel_job("reel-Z", OTHER_ORG, source_research_run_id=run_id)   # same run, other org
    return reel, car, run_id


def _entity_ids(downstreams):
    return {d.entity_id for d in downstreams}


def test_what_came_from_returns_org_scoped_downstreams():
    reel, car, run_id = _run_with_downstreams()
    view = _view_with(reel, car)
    ctx = ctx_for(ORG_ID)
    by_id = view.what_came_from(ctx, run_id)               # by reel-af research_run.id
    by_exec = view.what_came_from(ctx, "exec-1")           # by execution_id (org-scoped resolve)
    assert _entity_ids(by_id) == {"reel-A", "reel-B", "carousel-C"}     # NOT reel-Z (org-2)
    assert _entity_ids(by_exec) == {"reel-A", "reel-B", "carousel-C"}
    # each downstream is tagged with its kind
    assert {d.kind for d in by_id} == {"reel", "carousel"}


def test_what_came_from_unknown_or_cross_org_is_empty():
    reel, car, run_id = _run_with_downstreams()
    view = _view_with(reel, car)
    assert view.what_came_from(ctx_for(THIRD_ORG), run_id) == []        # cross-org conceal
    assert view.what_came_from(ctx_for(ORG_ID), "no-such-run") == []    # unknown -> empty


# ─────────────────────────── B4: tenancy (BLOCKING) ───────────────────────────


def test_cross_org_conceal_both_directions():
    reel = FakeReelJobRepo()
    car = FakeCarouselRepo()
    run_id = reel.seed_research_run("exec-1", ORG_ID, USER_ID, status="succeeded")
    reel.seed_reel_job("reel-A", ORG_ID, source_research_run_id=run_id)
    view = _view_with(reel, car)
    assert view.what_produced(ctx_for(OTHER_ORG), "reel-A") == []       # forward: concealed
    assert view.what_came_from(ctx_for(OTHER_ORG), run_id) == []        # reverse: concealed
    assert view.what_produced(ctx_for(ORG_ID), "reel-A") != []          # owner sees it


# ─────────────────────────── B5: round-trip consistency (BLOCKING closure) ───────────────────────────


def test_forward_reverse_round_trip():
    reel, car, _run_id = _run_with_downstreams()
    reel.seed_reel_job("reel-NP", ORG_ID, source_research_run_id=None)  # no-provenance entity
    view = _view_with(reel, car)
    ctx = ctx_for(ORG_ID)
    entities = ["reel-A", "reel-B", "carousel-C", "reel-NP"]
    assert any(view.what_produced(ctx, e) for e in entities)            # guard: real derivations exist
    assert view.what_produced(ctx, "reel-NP") == []                     # no-provenance -> empty forward
    for e in entities:
        for up in view.what_produced(ctx, e):
            back = view.what_came_from(ctx, up.execution_id)
            assert e in _entity_ids(back)                              # reverse(forward(entity)) ⊇ {entity}


# ─────────────────────────── B6: ANTI — non-owner, writes nothing (BLOCKING) ───────────────────────────


class _RecordingRepo:
    """Thin proxy recording (method) per call — proves a full cycle issues zero writes."""

    WRITES = frozenset({
        "insert_or_get_queued", "attach_execution_id", "mark_failed", "update_from_execution",
        "insert_research_run", "update_research_status", "insert_or_get_draft", "replace_slide",
        "set_status", "register_hq_recreate", "mark_stale_queued",
    })

    def __init__(self, inner):
        self._inner = inner
        self.calls: list = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*a, **k):
            self.calls.append(name)
            return attr(*a, **k)

        return wrapped

    def writes(self):
        return [c for c in self.calls if c in self.WRITES]


def test_lineage_reads_no_owner_table_and_writes_nothing():
    import lineage as lineage_module

    src = inspect.getsource(lineage_module)
    # (a) the service module is read-only — no write verbs anywhere.
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", src, re.I)
    # (c) the ONLY control-plane touch is the by-id owner interface reader.
    assert "research_reader.read" in src
    assert "get_execution" not in src          # never the raw CP execution client, only the reader

    # (d) runtime witness: a full forward+reverse cycle records ZERO writes.
    reel, car, _run = _run_with_downstreams()
    rec_reel, rec_car = _RecordingRepo(reel), _RecordingRepo(car)
    deps = make_deps(reel_jobs=rec_reel, carousels=rec_car)
    view = LineageView(deps)
    ctx = ctx_for(ORG_ID)
    for e in ["reel-A", "reel-B", "carousel-C"]:
        for up in view.what_produced(ctx, e):
            view.what_came_from(ctx, up.execution_id)
    assert rec_reel.writes() == []             # zero writes on reel-af's own tables
    assert rec_car.writes() == []
