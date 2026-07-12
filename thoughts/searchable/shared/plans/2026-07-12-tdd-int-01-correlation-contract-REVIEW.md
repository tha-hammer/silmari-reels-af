# Plan Review Report: `2026-07-12-tdd-int-01-correlation-contract.md`

**Reviewer:** WhiteCanyon (pre-implementation architectural review, `/review_plan`)
**Date:** 2026-07-12
**Plan:** Phase 1 — Correlation Contract (`execution_id` as the safe canonical join key)

> **Scope note.** This plan spans three repos; only `carousel-impl` is in this worktree.
> `web/pg.py` claims (B4) were verified against live code (read-only). The deep-research owner
> schema (`silmari-af-deep-research/ui/workspace/postgres/schema.sql`) and the ROOT dbmate set
> (`silmari-agentfield-system/migrations/deepresearch/`) are **not in this worktree** and were
> reviewed from the plan's own citations — flagged where a claim could not be independently
> verified here.

## Review Summary

| Category | Status | Issues Found |
|----------|--------|--------------|
| Contracts | ✅ | 0 critical / 1 minor |
| Interfaces | ✅ | 0 |
| Promises | ⚠️ | 1 warning (index lock window) |
| Data Models | ❌ | **1 critical** (dedupe cascade nulls provenance) / 1 warning (FK↔join-key) |
| APIs | ✅ | 0 (no new external API) |
| Workflow Closure | ✅ | 0 (all LEAF; strong fail-closed execution guarantee) |
| **Dependency status** | ⚠️ | 1 warning (Phase 0 not yet reflected in code) |

**Overall: `Needs Major Revision`** — one concrete, localized critical (B2 de-dupe can silently null
downstream provenance via `ON DELETE SET NULL`) must be resolved before B2 runs on prod. Everything
else is high quality; the fix is a single amendment that flips this to Ready.

---

## Contract Review

#### Well-Defined
- ✅ **C1–C13 are explicitly stated** (invariant + pre/post) in the enriched System Map — unusually
  strong for a schema plan. `C3` (partial UNIQUE `execution_id`), `C5` (additive+idempotent), `C6`
  (dedupe-before-unique), `C8` (canonical-shape-wins) are each testable.
- ✅ **Error contract for the correlation key:** duplicate non-null `execution_id` → unique
  violation; NULL `execution_id` never collides (partial predicate, `C4` / ISC-A1).
- ✅ **Ownership contract is unambiguous:** owner writes `research_run`; reel-af references by id;
  master §2 (no Option A/C) is honored — no NOT NULL dropped, no CHECK widened.

#### Missing or Unclear
- ⚠️ **(minor) De-dupe winner determinism leans on `created_at`.** B2's CTE orders
  `created_at asc nulls last, id asc`. If the thin `109` shape won on prod, `created_at` may be NULL
  across the board, collapsing the tiebreak to `id asc` — deterministic but arbitrary, and not
  necessarily the row downstream references point at (see the **critical** below).

#### Recommendations
- State the winner rule in the runbook (already partly done) **and** make the winner the row that
  preserves downstream references, or re-point references first (see critical).

---

## Interface Review

#### Well-Defined
- ✅ **`get_by_execution(ctx, execution_id)`** verified in code (`web/pg.py:283-305`): SELECTs
  `source_research_run_id`, filters `where execution_id = %s and org_id = %s`, `NotFound` on absent/
  foreign-org. Matches the plan's B4 interface claim exactly (GAP1 already closed in code).
- ✅ **`insert_or_get_queued`** binds `source_research_run_id` (`web/pg.py:238,245`) — writer/reader
  agree on the column. Consistent with the `carousel` writer (`pg.py:408-464`).
- ✅ **Index interface** `ux_research_run_execution_id` mirrors the owner's existing `run_id` UNIQUE
  idiom (IF-NOT-EXISTS partial index) — follows the established pattern.

#### Missing or Unclear
- None. The interface surface is small and internally consistent.

---

## Promise Review

#### Well-Defined
- ✅ **Idempotency:** `112` is `CREATE UNIQUE INDEX IF NOT EXISTS` + a DELETE-based repair that is a
  no-op on a clean table; re-apply changes nothing (`C5`). The idempotency test doubles as the
  migration-idempotency proof (`test_dedupe_is_noop_when_clean`).
- ✅ **Ordering:** dedupe **before** unique in the same migration (`C6`) — the unique build can't
  fail on dirty prod.
- ✅ **Forward-only `down`:** documented that `migrate:down` drops the index but cannot resurrect
  deleted rows — the pre-112 `pg_dump` is the only data rollback. Honest and correct.

#### Missing or Unclear
- ⚠️ **(warning) Prod lock window for the unique-index build.** `CREATE UNIQUE INDEX IF NOT EXISTS`
  (non-`CONCURRENTLY`) inside a dbmate migration runs in a transaction and takes a
  build-duration lock on `deepresearch.research_run` — a **live prod table**. On a large table this
  blocks writes for the build. `CONCURRENTLY` cannot run inside a transaction, so this needs an
  explicit choice: accept the lock (state the expected window) **or** split the index build into a
  non-transactional migration step using `CONCURRENTLY`. The plan's runbook is silent on this.

#### Recommendations
- Add a runbook line: measured/expected lock window, or a `-- migrate:up transaction:false` +
  `CREATE UNIQUE INDEX CONCURRENTLY` variant (with the caveat that `CONCURRENTLY` can leave an
  `INVALID` index on failure that must be dropped and rebuilt).

---

## Data Model Review

#### Well-Defined
- ✅ **Canonical `research_run` shape enumerated** (`run_id NOT NULL UNIQUE`, `query`, `visibility`,
  `params`, `status CHECK`) and asserted by B3's shape test. The partial UNIQUE is a clean additive.
- ✅ **`schema.sql` ≡ migration-set convergence** (`C7`) — both use IF NOT EXISTS so a green-field
  apply and the `100..112` set land the same index.

#### Missing or Unclear
- ❌ **CRITICAL — B2's de-dupe DELETE can silently NULL downstream provenance.**
  `108_create_reel_job.sql` (and the `110` carousel migration) declare
  `source_research_run_id uuid references deepresearch.research_run(id) **on delete set null**`
  (plan lines 83–86). B2 **deletes** the losing duplicate `research_run` rows
  (`delete … where ranked.rn > 1`). Any `reel_job`/`carousel` row whose `source_research_run_id`
  points at a **deleted** duplicate's `id` will have its provenance **set to NULL** by the FK — a
  silent provenance loss that **directly violates `C11` ("provenance non-null on read")**. The winner
  rule (earliest `created_at`) does **not** consider which duplicate is actually referenced
  downstream, so the referenced row may be the one deleted.
  - **Impact:** on a prod DB that has both `execution_id` duplicates *and* reel_jobs/carousels
    referencing the losing duplicates, applying `112` silently drops those provenance links —
    exactly the correlation the whole phase exists to protect.
  - **Recommendation (required amendment):** in `112`, **before** the DELETE, re-point references
    to the surviving row per `execution_id`:
    ```sql
    with ranked as ( … same window … ),
    winner as (select execution_id, id from ranked where rn = 1)
    update deepresearch.reel_job j
       set source_research_run_id = w.id
      from ranked r join winner w on w.execution_id = r.execution_id
     where j.source_research_run_id = r.id and r.rn > 1;
    -- repeat for deepresearch.carousel, THEN run the delete.
    ```
    (Root migration touching reel-af tables in the shared `deepresearch` schema is acceptable for a
    data-integrity repair; if ownership doctrine forbids it, gate the DELETE to skip rows still
    referenced and record them for manual reconciliation.) Add a regression: seed a `reel_job`
    pointing at a losing duplicate → after `112`, its `source_research_run_id` equals the winner's
    id, **not NULL**.

- ⚠️ **(warning) FK target ≠ join key (S4, consciously deferred).** `source_research_run_id` FK →
  `research_run(id)` (uuid PK), but the semantic correlation join is `execution_id`. Phase-1 keeps
  the app-level `execution_id` join and relies on the new UNIQUE for integrity — reasonable — but it
  leaves a **dangling FK that a future reader may trust for correlation integrity when it doesn't
  provide it.** Record the decision prominently at the column (not only in Open Seams) so the FK's
  reduced role is legible, and confirm the FK is still wanted at all (it is what creates the
  `ON DELETE SET NULL` cascade in the critical above).

#### Recommendations
- Land the re-point-before-delete amendment (critical). Decide and document the FK's fate (keep for
  cascade-cleanup semantics vs. drop in favor of pure app-level correlation).

---

## API Review

- ✅ **No new external API.** The plan's surface is schema (dbmate `112` + `schema.sql` mirror) and
  an internal repo reader (`get_by_execution`). Org-scoping + 404-conceal on the reader is the only
  "API contract" and it is verified in code. Nothing to version; no auth surface added.

---

## Workflow Closure Review

#### Well-Defined
- ✅ **All four behaviors classified LEAF with reasons** — appropriate: these are single additive
  schema changes / a same-process reader round-trip, not multi-hop async workflows. No unclassified
  behavior.
- ✅ **Execution guarantee is exemplary.** Both integration suites
  (`test_execution_id_unique.py`, `test_provenance_by_execution.py`) require `TEST_DATABASE_URL` and
  **fail closed** (`pytest.fail`, *not* `skip`) when it is unset — satisfying framework §4 rule 6
  (no skip-to-green). This is exactly right and rare.
- ✅ **B4 reads via the production path** (`get_by_execution`), not a raw `SELECT` of a downstream
  read model — the seeded `reel_job` is the *source-of-truth* table, so this is legitimate, not a
  read-model shortcut.

#### Missing or Unclear
- ⚠️ **(warning, dependency) Phase-0 precondition not yet true in code.** The plan states owner
  cross-writes were "removed in Phase 0"; but `insert_research_run` (`web/pg.py:340`) and
  `update_research_status` (`web/pg.py:350`) **still exist** in the current tree (CoralGrove handed
  the pg.py dead-code removal to claude-alpha; not yet landed). B4's anti-contract `C13` ("reel-af
  never writes `research_run`") cannot hold until that removal lands. Not a plan-design defect (the
  dependency is correctly declared), but Phase 1 must **gate B4 on Phase 0 being merged** and add a
  guard assertion that those methods are gone (or dead) before asserting `C13`.

#### Recommendations
- Add an explicit readiness gate: "B4 does not run until Phase 0 (owner-cross-write removal) is
  merged" + a static test that `web/pg.py` contains no `INSERT/UPDATE … deepresearch.research_run`.

---

## Critical Issues (Must Address Before Implementation)

1. **Data Models — B2 de-dupe cascade nulls provenance (`ON DELETE SET NULL`).**
   - Impact: silent loss of `reel_job`/`carousel` → `research_run` provenance for any row referencing
     a deleted duplicate; violates `C11`. The one behavior meant to *protect* correlation can
     *destroy* it.
   - Recommendation: re-point `source_research_run_id` from losing duplicates to the surviving row
     **before** the DELETE (SQL above), plus a seeded regression. Blocks B2's prod apply.

## Warnings (Address, non-blocking)

2. **Promises — unique-index build lock window on prod `research_run`** (non-`CONCURRENTLY` in a txn):
   state the expected lock window or switch to a `transaction:false` + `CONCURRENTLY` step.
3. **Data Models — S4 FK↔join-key misalignment:** document the FK's reduced role at the column; decide
   keep-vs-drop.
4. **Dependency — Phase 0 not yet reflected in code** (`insert_research_run`/`update_research_status`
   still present): gate B4 on Phase 0 merge + add a "no owner-write in pg.py" guard.

## Suggested Plan Amendments

```diff
# Behavior 2 (de-dupe repair) — migrate:up, BEFORE the DELETE
+ -- Re-point downstream provenance from losing duplicates to the winner per execution_id,
+ -- so the ON DELETE SET NULL FK cannot silently null reel_job/carousel provenance (C11).
+ with ranked as ( ...existing window... ),
+      winner as (select execution_id, id from ranked where rn = 1)
+ update deepresearch.reel_job j set source_research_run_id = w.id
+   from ranked r join winner w on w.execution_id = r.execution_id
+  where j.source_research_run_id = r.id and r.rn > 1;
+ -- (repeat the same UPDATE for deepresearch.carousel)
  delete from deepresearch.research_run r using ranked
   where r.id = ranked.id and ranked.rn > 1;

# Behavior 2 — add regression
+ test_dedupe_repoints_downstream_provenance_not_null: seed a reel_job pointing at a losing
+ duplicate; after 112, its source_research_run_id == winner id (NOT NULL).

# Behavior 1 — runbook / migration
~ Note the CREATE UNIQUE INDEX lock window on prod research_run, OR use
  `-- migrate:up transaction:false` + CREATE UNIQUE INDEX CONCURRENTLY (+ INVALID-index cleanup note).

# Behavior 4 — readiness gate
+ Gate B4 on Phase 0 (owner-cross-write removal) being merged; add a static guard test asserting
+ web/pg.py issues no INSERT/UPDATE against deepresearch.research_run.
```

## Approval Status

- [ ] **Ready for Implementation**
- [ ] **Needs Minor Revision**
- [x] **Needs Major Revision** — resolve the B2 de-dupe provenance-cascade (critical) before B2 runs
  on prod; the three warnings should be folded in the same pass. The plan is otherwise well-grounded
  (explicit C1–C13 contracts, additive+idempotent discipline, exemplary fail-closed integration
  tests); the required change is a single, localized migration amendment.
