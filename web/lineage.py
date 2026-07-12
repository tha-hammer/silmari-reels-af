"""Cross-App Lineage View — a reel-af-owned, read-only, ORG-SCOPED read model (INT-04).

Answers, across the research→reel boundary, over provenance ALREADY in place:
  - forward  : ``what_produced(ctx, entity_id)``  — a reel/carousel -> its upstream research run
  - reverse  : ``what_came_from(ctx, run_id)``    — a research run -> its downstream reels/carousels

The derivation link is reel-af-owned and already stored: ``reel_job``/``carousel``.
``source_research_run_id`` (uuid FK) -> reel-af's own ``research_run`` reference row, which
carries ``execution_id`` (the OpenLineage-``runId`` analog / W3C-PROV ``wasDerivedFrom`` key).

NON-OWNER (ARCHITECTURE §2/§5): this view **writes nothing**, reads only reel-af's OWN tables
through the org-scoped repos, and reaches a control-plane execution record ONLY by-id through the
owner interface (``research_reader.read``) — never a cross-service table read. Tenancy is the
request identity's org (``ctx.org_id``): every read is scoped and conceals cross-org access
(returns empty, never another org's rows). No event is consumed, no projection maintained, no
new table/outbox/cursor/projector exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from deps import BadGateway, NotFound


@dataclass(frozen=True)
class UpstreamRun:
    """The upstream research run a reel/carousel ``wasDerivedFrom`` (forward answer).

    ``execution_id``/``status`` are the fully reel-af-owned link (always present). ``title``/
    ``result_ref`` are optional owner-interface enrichment (A1, fail-open: null when the control
    plane is unreachable — the link still returns)."""

    execution_id: str | None
    status: str
    title: str | None = None
    result_ref: str | None = None

    def to_json(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "title": self.title,
            "result_ref": self.result_ref,
        }


@dataclass(frozen=True)
class Downstream:
    """A reel/carousel that ``wasDerivedFrom`` a research run (reverse answer)."""

    kind: str          # "reel" | "carousel"
    entity_id: str

    def to_json(self) -> dict:
        return {"kind": self.kind, "entity_id": self.entity_id}


def _is_uuid(value) -> bool:
    """True iff ``value`` is a reel-af ``research_run.id`` (uuid) rather than a text
    ``execution_id`` — so a text id is never bound to the uuid ``id`` column."""
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


class LineageView:
    """Read-only, ORG-SCOPED lineage over data already in place. Reads reel-af's OWN rows and
    reaches a control-plane record ONLY through the by-id owner interface. Writes nothing."""

    def __init__(self, deps):
        self._deps = deps

    # ─────────────── forward: entity -> upstream run(s) ───────────────

    def what_produced(self, ctx, entity_id) -> list[UpstreamRun]:
        entity = self._resolve_entity(ctx, entity_id)      # org-scoped; unknown/other-org -> None
        source_run_id = getattr(entity, "source_research_run_id", None)
        if source_run_id is None:
            return []                                      # unknown/other-org/no-provenance -> empty
        try:
            run = self._deps.reel_jobs.get_research_run(ctx, source_run_id)   # org-scoped
        except NotFound:
            return []
        return [self._enrich(ctx, run)]

    def _resolve_entity(self, ctx, entity_id):
        # A reel id and a carousel id are disjoint; try both org-scoped read-by-ids.
        for read in (self._deps.reel_jobs.get, self._deps.carousels.get):
            try:
                return read(ctx, entity_id)
            except NotFound:
                continue
        return None

    def _enrich(self, ctx, run) -> UpstreamRun:
        detail: dict = {}
        try:
            detail = self._deps.research_reader.read(ctx, run.execution_id)   # by-id owner interface
        except (BadGateway, NotFound):
            detail = {}                                    # fail-open: link still returned, sugar null
        return UpstreamRun(
            execution_id=run.execution_id,
            status=run.status,
            title=detail.get("title"),
            result_ref=detail.get("result_ref"),
        )

    # ─────────────── reverse: run -> downstream entities ───────────────

    def what_came_from(self, ctx, run_id) -> list[Downstream]:
        run = self._resolve_run(ctx, run_id)               # accepts research_run.id OR execution_id
        if run is None:
            return []                                      # unknown/other-org -> concealed empty
        reels = self._deps.reel_jobs.reel_jobs_by_source_run(ctx, run.id)     # WHERE ... AND org_id
        cars = self._deps.carousels.carousels_by_source_run(ctx, run.id)
        return ([Downstream("reel", str(r.job_id)) for r in reels]
                + [Downstream("carousel", str(c.job_id)) for c in cars])

    def _resolve_run(self, ctx, run_id):
        readers = []
        if _is_uuid(run_id):
            readers.append(self._deps.reel_jobs.get_research_run)             # by research_run.id
        readers.append(self._deps.reel_jobs.get_research_by_execution)        # by execution_id
        for read in readers:
            try:
                return read(ctx, run_id)
            except NotFound:
                continue
        return None
