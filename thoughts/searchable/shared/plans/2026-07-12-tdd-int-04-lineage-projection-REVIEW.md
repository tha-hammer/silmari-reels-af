# Plan Review Report: `2026-07-12-tdd-int-04-lineage-projection.md`

**Reviewer:** WhiteCanyon (pre-implementation architectural review, `/review_plan`)
**Date:** 2026-07-12
**Plan:** Phase 4 — Cross-App Lineage Projection (optional, non-owner CQRS read model)

> **Scope note.** This plan implements Go code under
> `silmari-agentfield-system/agentfield/control-plane` — **not in this worktree**. It was reviewed
> from the plan's own specifications and cited external canon (OpenLineage / W3C PROV). Findings
> about the event→edge contract are derived from the payload the plan itself declares (master §5.3:
> `data={run_id, status, title, result_ref, research_prompt, research_document_id}`). This phase is
> explicitly **optional upside** and **depends on Phase 2** (event emission) not yet shipped, so
> these are pre-implementation design corrections, not prod-urgent.

## Review Summary

| Category | Status | Issues Found |
|----------|--------|--------------|
| Contracts | ⚠️ | 1 critical (idempotency conflict target) |
| Interfaces | ✅ | 1 minor (port cohesion) |
| Promises | ⚠️ | 1 warning (stale labels on DO NOTHING) |
| Data Models | ❌ | **1 critical** (research.completed has no downstream → no edge) |
| APIs | ❌ | **1 critical** (tenancy: no org on event → read model can't org-scope) |
| Workflow Closure | ⚠️ | 1 warning (no production driver for Drain) + closure-fixture caveat |

**Overall: `Needs Major Revision`** — three design-level criticals, all around the **event→edge
contract** (which endpoint the edge is formed from, how it dedups, how it carries org). The
projection machinery itself (rebuild closure B5, ANTI mechanization B6, fail-closed execution) is
excellent and should be preserved.

---

## Data Model Review

#### Well-Defined
- ✅ **`LineageEdge` maps 1:1 to OpenLineage/PROV** (`runId`→`UpstreamRunID`, `wasDerivedFrom` = the
  edge, `OccurredAt` from event) — canon-faithful, "mirror not invent" honored.
- ✅ **Thin snapshot labels only** (title/prompt/result_ref/doc_id) — no mutable foreign aggregate
  stored; ids opaque (R1). Correct non-owner data discipline.

#### Missing or Unclear
- ❌ **CRITICAL — a `research.completed` event carries no *downstream* entity id, so it cannot form a
  derivation edge; the plan's "forward works today" is incorrect.**
  The edge is *"downstream Entity (reel/carousel) `wasDerivedFrom` upstream Activity (research_run)"*
  and its identity is `(downstream_entity_id, upstream_run_id, job)`. But `research.completed`'s
  payload (`{run_id, status, title, result_ref, research_prompt, research_document_id}`) contains
  **only the upstream** — the reel/carousel does not exist yet at research-completion time. So
  `edgeFromEvent(research.completed)` has **no `downstream_entity_id`** to set; `downstreamID(evt)`
  (plan line 402) has nothing to read. The complete edge can only be formed by a **downstream
  producer** event (`reel.produced` / `carousel.produced`) that carries **both** its own entity id
  **and** `source_research_run_id` (= upstream `execution_id`).
  - Consequence: the plan's claim (lines 104–108) that *"Forward (`what_produced`) works with just
    `research.completed` today; reverse becomes richer once reel-af emits"* is backwards — **neither**
    direction is answerable until a downstream `*.produced` event exists, because the edge needs both
    endpoints and `research.completed` supplies only one. B2's `TestEdgeFromResearchCompleted`
    conspicuously asserts `UpstreamRunID` but **not** `DownstreamEntityID` — the missing assertion is
    the tell.
  - **Recommendation (required):** make **downstream `*.produced` events the edge source**
    (they carry `entity_id` + `source_research_run_id`); treat `research.completed` as at most an
    *upstream-label registration* (enriching `title`/`result_ref` for an upstream key), **not** an
    edge creator. Reframe B2 (map `reel.produced`→edge; `research.completed`→label or skip), B4
    ("forward" needs downstream events), and the closure fixtures (must seed edge-forming downstream
    events). Assert `DownstreamEntityID` in B2.

- ❌ **CRITICAL (idempotency) — the upsert conflict target does not cover the edge's semantic unique
  key.** B1 declares **two** uniques: `UNIQUE(event_id)` *and*
  `UNIQUE(downstream_entity_id, upstream_run_id, job)`. But the upsert is
  `INSERT … ON CONFLICT (event_id) DO NOTHING` (plan lines 471, 491). Two **distinct** `event_id`s
  that map to the **same** `(downstream_entity_id, upstream_run_id, job)` — e.g. a producer re-run,
  a redelivery after a producer restart mints a fresh CloudEvents id, or two events describing the
  same derivation — will pass the `event_id` conflict check and then **violate the second unique**,
  which `ON CONFLICT (event_id)` does **not** handle → the INSERT raises → `Drain()` returns error →
  the projector wedges (cursor stuck). C1 ("one edge per distinct event") is not actually delivered
  for this case, and B3 only tests the same-`event_id` duplicate, never the same-edge/different-id
  case.
  - **Recommendation (required):** conflict on the **semantic edge key**
    `ON CONFLICT (downstream_entity_id, upstream_run_id, job) DO NOTHING` (or `DO UPDATE` for label
    refresh), and keep `event_id` as a *delivery* dedup only if a separate idempotency check needs
    it. Add a B3 case: two different `event_id`s → same edge → exactly one row, no error.

#### Recommendations
- Specify `jobFromEvent` deterministically (it is part of the edge identity; an unstable `job` value
  fragments edges). Currently only named, not defined.

---

## APIs Review

#### Well-Defined
- ✅ **Read-only surface** (`GET /lineage/entity/{id}`, `GET /lineage/run/{execution_id}`); unknown
  id → `200` empty; no write route registered (B7). Optional/feature-flagged mounting.

#### Missing or Unclear
- ❌ **CRITICAL — tenancy: the read model cannot be org-scoped because no org travels on the event.**
  The plan says storage is "org-scoped … `LineageEdgesByDownstream`/`ByUpstreamRun`" (line 117), but
  (a) the query method signatures (`WhatProduced(entityID)`, `WhatCameFrom(runID)`, lines 554–559)
  and the HTTP handlers (lines 738–741) take **no org/ctx** and apply **no org filter**; and (b) more
  fundamentally, **neither the CloudEvents payload (§5.3) nor the `event_outbox` row carries
  `org_id`**, so the projector has no org to stamp onto `lineage_edge` in the first place. The ANTI
  (C5) forbids joining owner tables to recover org, so org **must** arrive on the event. As written,
  `GET /lineage/run/{execution_id}` would return any org's derivation → **cross-org lineage leak**.
  - **Recommendation (required):** add `org_id` to the emitted `*.completed` event contract (a
    Phase-2 amendment this plan must depend on), stamp it on `lineage_edge`, and make the query +
    HTTP layer resolve caller org and filter by it (mirror INT-01's `get_by_execution` org-scope +
    404-conceal). Add a cross-org query test (org B cannot read org A's lineage).

#### Recommendations
- If Phase 2 cannot add `org_id` in time, gate this optional phase until it can — do not ship a
  cross-tenant-readable lineage surface.

---

## Contract Review

#### Well-Defined
- ✅ **C1–C5 explicit** (invariant + pre/post) and the enriched neighbor contracts C6–C11 correctly
  attribute the outbox's durability/at-least-once/cursor-isolation guarantees to their owner (Phase
  2/3), which this plan *depends on* rather than re-solves.
- ✅ **Fail-safe mapping:** `edgeFromEvent` returns `(edge, ok)`; malformed/non-`*.completed` →
  `ok=false`, never a partial edge. Skips are loud (`lineage_events_skipped_total`).

#### Missing or Unclear
- (Rolled into the two Data-Model criticals above — C1 idempotency and the edge-formation contract.)

---

## Interface Review

#### Well-Defined
- ✅ **`OutboxReader` is a package-local minimal interface** (grounding §4 idiom) satisfied by the
  existing `StorageProvider` via type assertion — no new bus, no new cursor table. Correct reuse.
- ✅ **Generic `*.completed` matcher** → future producers plug in with zero projector change (the
  "any app" payoff). Good extension point.

#### Missing or Unclear
- ⚠️ **(minor) Port cohesion.** `OutboxReader` bundles outbox **reads** (`ReadEventOutboxAfter`,
  `Get/AdvanceOutboxCursor`) with a lineage **write** (`UpsertLineageEdge`) in one interface. These
  are two concerns (consume vs. own-store-write); splitting them clarifies the ANTI (the write side
  is the only thing that may touch `lineage_*`). Cosmetic.

---

## Promise Review

#### Well-Defined
- ✅ **Derivable + disposable** (C4) proven by the B5 rebuild closure; **replay-safe** cursor advances
  even on skipped rows (no infinite re-read).

#### Missing or Unclear
- ⚠️ **(warning) `DO NOTHING` keeps the first-seen snapshot labels.** If a later, corrected
  `*.completed` (newer `OccurredAt`) carries updated `title`/`result_ref`, `DO NOTHING` retains the
  stale labels. Fine if labels are treated as immutable-at-first-write; if not, use `DO UPDATE` with
  an `OccurredAt`-guard (`WHERE excluded.occurred_at > lineage_edge.occurred_at`). Decide and state.

---

## Workflow Closure Review

#### Well-Defined
- ✅ **B5 rebuild-from-scratch is a correct BLOCKING closure:** SOURCE = seeded outbox; TRIGGER =
  `Drain()` then `Rebuild()` at/above the projector↔cursor seam; OBSERVE via the **query methods**
  (`WhatProduced`/`WhatCameFrom`), not raw `lineage_edge` reads; red-at-seam = remove the cursor
  reset → empty → red. Derived from the map, not invented.
- ✅ **B6 ANTI is mechanized three ways** (import guard + SQL-string guard + runtime `RecordingStore`
  write-recorder) — the "non-owner" doctrine is a passing test, not a comment. Exemplary.
- ✅ **Execution guarantee:** default `go test ./internal/lineage/...` runs SQLite-local with no
  external infra; the closure **always executes** (never skips); Postgres parity behind `@postgres`
  **fails closed**. Satisfies framework §4 rule 6.

#### Missing or Unclear
- ⚠️ **(warning) No production driver/registration for `Drain()`.** The closure calls `Drain()`
  directly in-test, but the plan does not specify **what invokes `Drain()` in production** — a
  scheduler tick, a post-publish hook, a startup catch-up task, or a consumer goroutine — nor its
  startup/shutdown/lifecycle wiring. Without a registered driver, the read model never updates in
  prod (the classic "projector with no production caller"). Specify the driver (e.g. a ticker/worker
  registered at control-plane startup) and its cadence/backoff.
- ⚠️ **(caveat, ties to Data-Model critical #1) Closure fixtures must form real edges.** If
  `dupAndOutOfOrderFixture()` seeds `research.completed` events (which cannot form edges, per the
  critical above), the projection is empty and `rebuild == incremental` passes **vacuously**. The
  fixtures must seed **downstream `*.produced`** events so the BLOCKING closure exercises real edges.

#### Recommendations
- Add the production driver + lifecycle; make closure/fixtures edge-forming.

---

## Critical Issues (Must Address Before Implementation)

1. **Edge formation** — `research.completed` lacks a downstream entity id; edges must be formed from
   downstream `*.produced` events. Reframe B2/B4/closure; assert `DownstreamEntityID`. (Data Models)
2. **Idempotency conflict target** — upsert `ON CONFLICT (event_id)` doesn't cover
   `UNIQUE(downstream_entity_id, upstream_run_id, job)`; two ids → same edge → wedge. Conflict on the
   semantic key. (Contracts / Data Models)
3. **Tenancy** — no `org_id` on the event/outbox → `lineage_edge` can't be org-scoped and the read
   API leaks cross-org; C5 forbids recovering org via owner joins. Add `org_id` to the Phase-2 event
   contract + org-filter the query/API. (APIs)

## Warnings

4. **No production driver** for `Drain()` (scheduler/worker + lifecycle) — specify it.
5. **Closure fixtures** must be edge-forming (else B5 passes vacuously).
6. **`DO NOTHING` stale labels** — decide immutable-first-write vs. `DO UPDATE` with occurred_at guard.
7. **Port cohesion / `jobFromEvent` determinism** — minor.

## Suggested Plan Amendments

```diff
# Behavior 2 (edgeFromEvent) — edges come from DOWNSTREAM producers
~ Map reel.produced / carousel.produced (carry entity_id + source_research_run_id=execution_id)
  -> LineageEdge{DownstreamEntityID: entity_id, UpstreamRunID: source_research_run_id, ...}
~ research.completed -> upstream label registration (or skip); NOT an edge source
+ assert e.DownstreamEntityID is set in TestEdgeFrom*   # currently only UpstreamRunID is asserted

# Behavior 1/3 (upsert) — conflict on the semantic edge key
~ INSERT ... ON CONFLICT (downstream_entity_id, upstream_run_id, job) DO NOTHING  # not (event_id)
+ B3 case: two distinct event_ids -> same edge -> exactly one row, no error (no wedge)

# Behavior 1 (schema) + Phase-2 dependency — tenancy
+ add org_id to lineage_edge; require org_id on the *.completed event/outbox (Phase 2 contract)
+ WhatProduced(ctx, entity_id) / WhatCameFrom(ctx, run_id): filter by ctx.org; HTTP resolves caller org
+ test: org B cannot read org A's lineage (cross-org conceal)

# Projector lifecycle
+ specify the production driver that calls Drain() (ticker/worker) + startup/shutdown registration
~ closure fixtures use downstream *.produced events (edge-forming), not research.completed only
```

## Approval Status

- [ ] **Ready for Implementation**
- [ ] **Needs Minor Revision**
- [x] **Needs Major Revision** — resolve the three event-contract criticals (edge formation, upsert
  conflict target, tenancy/org-on-event) before implementation. The projection engine itself — the
  rebuild-from-outbox closure (B5), the three-layer non-owner ANTI (B6), and the fail-closed
  execution guarantee — is strong and should be kept as-is. Because this phase is optional upside and
  Phase 2 has not shipped, there is room to fix the contract before any code lands.
