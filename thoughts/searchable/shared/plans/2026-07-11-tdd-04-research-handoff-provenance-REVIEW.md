# Pre-Implementation Architectural Review — Plan 04: Cross-Node Research Handoff + Research Provenance

**Plan under review:** `thoughts/searchable/shared/plans/2026-07-11-tdd-04-research-handoff-provenance.md`
**Review type:** `/review_plan` (6-dimension architectural review, grounded in real code)
**Reviewed against code at:** `silmari-reels-af/web/{server,pg,reel_jobs,deps,control_plane}.py`, `tests/web/{conftest.py,integration/test_pg_reel_jobs.py}`, `silmari-af-deep-research/{main.py,ui/defaults.json,ui/index.html}`

---

## Summary Table

| # | Category | Status | ✅ | ⚠️ | ❌ |
|---|----------|--------|----|----|----|
| 1 | Contracts | ⚠️ | 5 | 2 | 0 |
| 2 | Interfaces | ⚠️ | 4 | 3 | 0 |
| 3 | Promises | ⚠️ | 3 | 2 | 1 |
| 4 | Data Models | ⚠️ | 3 | 2 | 1 |
| 5 | APIs | ✅ | 5 | 1 | 0 |
| 6 | Workflow Closure | ✅ | 6 | 1 | 0 |

**Overall:** Strong, closure-literate plan. The headline bug (GAP1 SELECT-omission) is correctly identified, correctly attributed, and correctly wrapped in a red-at-seam integration closure test. Two **❌ critical** issues block a clean run: (a) the test harness references (`OTHER_ORG`/`OTHER_USER`) and fake extensions the plan's own test code depends on do not exist yet and are under-specified; (b) the new `research_run` columns (`execution_id`, `created_at`) the writer inserts are **not** in `REQUIRED_SCHEMA` (`pg.py:40`), so production readiness will not fail-closed if the root migration omits them.

---

## 1. Contracts — ⚠️

### Well-Defined
- **Identity-free egress (C1).** Correctly grounded: `HttpControlPlane._headers` (`web/control_plane.py:27-31`) injects only `Content-Type` + `X-API-Key`; the docstring (`control_plane.py:1-7`) already promises no `Cookie`/`Authorization` forwarding. The new research routes reuse the same `dispatch_async`/`get_execution` port (`control_plane.py:33-37`), so the invariant is inherited, not re-implemented. ✅
- **Ownership-from-context (C2).** `FORBIDDEN_IDENTITY_FIELDS` (`reel_jobs.py:32-45`) + `_reject_forbidden_identity` (`reel_jobs.py:73-76`) are the existing gate; the plan reuses them via `build_research_dispatch` (Behavior 1 Green). ✅
- **Byte-exact target (C3).** `TARGET_RESEARCH = "meta_deep_research.execute_deep_research"` matches the DR node/reasoner (`main.py:3038` `execute_deep_research`, `ui/defaults.json` `node:"meta_deep_research"`, `reasoner:"execute_deep_research"`). Property test asserts byte-exactness. ✅
- **Owner-scoped research_run with NotFound concealment (C4).** Mirrors `authorize_reel_read`'s cross-org concealment idiom (`deps.py:177-182`). ✅
- **SELECT-omission fix as an explicit contract (C5 / GAP1).** Confirmed real: `get_by_execution` (`pg.py:256-268`) SELECTs `id, org_id, created_by, status, execution_id, result_ref, completed_at` and **omits `source_research_run_id`**, while the writer `insert_or_get_queued` (`pg.py:203-215`) **does** bind `submission.source_research_run_id`. The plan names this the closure gap and makes "add the column to SELECT + `ReelJobRef`" a behavior with a red-at-seam test (Behavior 4). Excellent. ✅

### Missing or Unclear
- **⚠️ Error contract for DR-node dispatch failure / CP `>=400` on `/research/run`.** Behavior 1 Green (`_handle_research_run`) calls `dispatch_async` and returns `jsonify(cp), status` with no handling of `status >= 400`, no `execution_id`-absent guard, and (critically) **no compensation of the `research_run` row** if the record write succeeds but dispatch does not — the opposite ordering from `_handle_submit` (`server.py:176-181`), which marks the job failed on CP error. The record-then-return ordering (Behavior 3 Green: dispatch → `insert_research_run("queued")`) is stated but the failure interleaving (dispatch 202 but no `execution_id`; dispatch 4xx/5xx) has no contract. Specify: if CP does not return an `execution_id`, do **not** write a `research_run` row (or write it `failed`).
- **⚠️ Poll-timeout / terminal-status contract is asserted but not owned here.** Behavior 2 surfaces terminal status "unchanged" but there is no timeout/cancellation contract for a run that never terminates (PRD ISC-23). The plan explicitly defers SSE and says "polling is sufficient," which is acceptable, but the *absence of a server-side terminal deadline* should be stated as an explicit non-goal tied to `mark_stale_queued` (`pg.py:285-300`) — otherwise `research_run` rows can sit `queued` forever with no reaper (there is a reaper for `reel_job`, none proposed for `research_run`).

### Recommendations
- Add an explicit error-contract subsection to Behavior 1/3: CP non-2xx or missing `execution_id` → no `research_run` row persisted (or persisted `failed`), 502/passthrough returned.
- State the `research_run` no-reaper decision as a named non-goal, or add `mark_stale_research` as a follow-up bead.

---

## 2. Interfaces — ⚠️

### Well-Defined
- **Control-plane port signatures.** `dispatch_async(target, body) -> (int, dict, dict)` and `get_execution(execution_id) -> (int, dict, dict)` (`control_plane.py:33-37`, protocol `deps.py:146-149`) are reused verbatim; the plan does not fork them. ✅
- **`ReelJobRepoPort` extension is explicitly enumerated.** Seam Flags (plan §Seam Flags) lists the five additions (`insert_research_run`, `get_research_run`, `get_research_by_execution`, `update_research_status`, extended `get_by_execution` + `ReelJobRef`) and marks them owned-by-Plan-4/consumed-by-5/6. ✅
- **Naming matches conventions.** `insert_research_run`/`get_research_run` mirror `insert_or_get_queued`/`get_by_execution`; `ResearchRunRef` mirrors `ReelJobRef`. ✅
- **DR reasoner input contract.** `execute_deep_research(query, mode="general", research_focus=3, ...)` (`main.py:3038-3052`) takes flat kwargs; the dispatch wraps them in `{"input": {...}}`, matching reel-af's own established `{"input": cp_input}` convention (`server.py:170`). ✅

### Missing or Unclear
- **⚠️ `ReelJobRepoPort` protocol edit is not in a "Files touched" list.** Behavior 3 lists `web/deps.py` for the protocol extension, but Behavior 4 (which extends `get_by_execution`'s return `ReelJobRef` and relies on `get_research_run`) does not restate the protocol edit. Because `ReelJobRepoPort` is `@runtime_checkable` (`deps.py:127`), adding methods to `PgReelJobRepo` without adding them to the protocol is silently allowed but breaks the "fakes implement the port" contract. Ensure `deps.py:128-135` gains all five signatures in one edit.
- **⚠️ `FakeReelJobRepo` extension is under-specified for the read-back-via-poll path.** Behavior 4's unit test (`test_create_from_research_stamps_and_reads_back_provenance`) POSTs a submit then GETs `/api/v1/executions/exec_c1` and asserts `read["source_research_run_id"]`. But the current fake's `get_by_execution` returns a single fixed `self._job` and **ignores `execution_id`** (`conftest.py:96-101`); it does not thread `source_research_run_id` from the `submission` through insert→attach→read. The plan says "extend the fake" but does not specify that the fake must (a) store submissions keyed by execution_id and (b) surface `source_research_run_id` on read — without which the unit closure cannot go green. Specify the fake's new read model.
- **⚠️ Defaults keyset vs. reasoner signature drift.** The plan's property keyset is 9 keys and **omits `model`**, but `ui/defaults.json` defines `model` (and the reasoner accepts `model` + `api_key`). This is acceptable (model blank ⇒ server default), but the plan should state that `model`/`api_key` are intentionally not forwarded (and `api_key` must **never** be forwarded from a mirrored defaults file — it is a secret-shaped field). Note `num_parallel_streams` default is **2** in `defaults.json`, not `3` as the plan's illustrative comment (Behavior 1 Green line ~324) implies — align the mirror.

### Recommendations
- One consolidated `deps.py` protocol edit adding all five method signatures; reference it from both Behavior 3 and 4 "Files touched."
- Specify `FakeReelJobRepo`'s new execution-keyed store and `source_research_run_id` read surface in the conftest Seam Flag.
- In `web/research_defaults.json`, exclude `model` and `api_key`; document the exclusion inline.

---

## 3. Promises — ⚠️

### Well-Defined
- **Identity-free cross-node call.** Guaranteed by port reuse (C1). The Observability section adds `identity.headers_leaked=false` as an assertion. ✅
- **Provenance write atomicity (single INSERT).** `insert_research_run` is one INSERT; `insert_or_get_queued` already commits atomically (`pg.py:216-227`). ✅
- **Re-stamp on receiving UI (OD-4 deferred).** The plan correctly builds only the reel-af re-stamp side (C6 validation via `get_research_run` before stamp) and defers the shared cookie; safe under both domain outcomes. ✅

### Missing or Unclear
- **⚠️ Idempotency of research dispatch.** Behavior 3 says "re-dispatch idempotency is out of scope (each dispatch records a run)." Contrast: reel submit has durable idempotency on `(org_id, created_by, client_request_id)` (`pg.py:199-207`). A user double-clicking "Research" will mint N runs + N executions. That may be acceptable for research, but it is a *promise gap* worth stating as a conscious decision, not a silent omission — especially since `research_run` has no unique key proposed.
- **⚠️ Terminal-status detection / cancellation on poll.** Behavior 2 reuses `_normalize_execution_status` (`server.py:118-122`), which is good, but `update_research_status` is called on every poll with no terminal-monotonicity guard analogous to `update_from_execution`'s `status not in ('succeeded','failed','cancelled')` SQL clause (`pg.py:277-282`). A late poll could downgrade a terminal `research_run`. Specify the same monotonic guard.

### Missing or Unclear (❌)
- **❌ Write-then-return ordering leaves an orphan-execution window with no compensation.** In Behavior 3 Green, the sequence is `dispatch → insert_research_run(execution_id, "queued")`. If the process dies between dispatch (CP now has a live execution) and the DB insert, the execution exists with **no** `research_run` row and no reaper — the run is unpollable via reel-af (`get_research_by_execution` 404s) and unrecordable. `_handle_submit` handles the mirror case by inserting the `reel_job` row **first**, then dispatching, then attaching (`server.py:163-183`), so a crash leaves a recoverable `queued` row + `mark_stale_queued` reaper. The research path inverts this without the safety net. Either insert the `research_run` row **before** dispatch (mint `run_id` first, insert `queued`, dispatch, then `update_research_status` with the CP `execution_id`), or add a reaper. This is the single biggest robustness gap.

### Recommendations
- Invert Behavior 3 to mint `run_id` → `insert_research_run(status="queued", execution_id=None)` → dispatch → `update_research_status`/attach execution_id — mirroring `_handle_submit`.
- Add terminal-monotonicity to `update_research_status` (SQL `status not in (terminal)` guard).
- State the "no dispatch idempotency" decision explicitly.

---

## 4. Data Models — ⚠️

### Well-Defined
- **`research_run` core columns exist in the readiness gate.** `REQUIRED_SCHEMA["research_run"] = {id, org_id, created_by, status}` (`pg.py:40`); startup raises `SchemaUnavailable` if absent (`pg.py:76-82`). ✅
- **`source_research_run_id` FK semantics.** The integration fixture (`test_pg_reel_jobs.py`) already declares `source_research_run_id uuid references deepresearch.research_run(id) on delete set null` — nullable, `ON DELETE SET NULL`. Correct for provenance (deleting a research run must not cascade-delete reels). ✅
- **`ReelJobRef` must gain the column (the bug).** Behavior 4 Green explicitly adds `source_research_run_id` to both the SELECT and `ReelJobRef` (`reel_jobs.py:60-70`). ✅

### Missing or Unclear
- **⚠️ `ResearchRunRef` shape unspecified beyond four columns.** Behavior 3 Green returns `ResearchRunRef(*row)` from a 4-column SELECT (`id, org_id, created_by, status`). But `insert_research_run` writes `execution_id` and `created_at` too, and Behavior 2's poll resolves a run *by execution_id* (`get_research_by_execution`) — that reader needs `execution_id` in its projection. Define `ResearchRunRef` fields explicitly (include `execution_id`).
- **⚠️ No unique constraint on `research_run`.** Given "each dispatch records a run," there is no dedup key; fine, but note it so Plans 5/6 don't assume one.

### Missing or Unclear (❌)
- **❌ `research_run.execution_id` and `created_at` are NOT in `REQUIRED_SCHEMA` but are written by `insert_research_run`.** The Behavior 3 Green INSERT is `(id, org_id, created_by, execution_id, status, created_at)` (plan lines ~527-531), yet `REQUIRED_SCHEMA["research_run"]` (`pg.py:40`) requires only `{id, org_id, created_by, status}`. Production `_assert_schema` (`pg.py:76-82`) will therefore pass startup even if the root migration's `research_run` lacks `execution_id`/`created_at`, and the **first live INSERT will fail at runtime** (undefined column) instead of failing-closed at readiness. The plan must extend `REQUIRED_SCHEMA["research_run"]` to `{id, org_id, created_by, execution_id, status, created_at}` and note the root-migration (`migrations/deepresearch/`) dependency. The existing integration schema fixture also only defines `{id, org_id, created_by, status}` for `research_run` (see `test_pg_reel_jobs.py` `_SCHEMA`) — the new integration test's fixture must add the columns.

### Recommendations
- Extend `REQUIRED_SCHEMA["research_run"]` (`pg.py:40`) with `execution_id` + `created_at`; call out the root migration dependency as a Seam Flag.
- Define `ResearchRunRef` fields including `execution_id`; update the two SELECTs to project it.

---

## 5. APIs — ✅

### Well-Defined
- **Route mounting.** New handlers dispatch through the existing catch-all `@app.route("/api/<path:subpath>")` → `_api_router` (`server.py:363-369`, `:228-238`) via new pure predicate matchers, matching `_submit_target`/`_poll_id` (`server.py:42-61`). ✅
- **`POST /api/v1/research/run` request/response** — `{query, mode?}` → `{research_run_id, execution_id}`. Clear. ✅
- **`GET /api/v1/research/<execution_id>`** — auth via `identity.resolve` + owned-run lookup + poll → `{status, markdown, html, sources}`. 404-on-foreign concealment matches `authorize_reel_read` (`deps.py:177-182`). ✅
- **DR "Send to reels" by-reference payload.** Carries `research_run_id` only (ISC-27), not markdown; deep-links to reel-af. `REELS_BASE` from config, not literal. ✅
- **Auth via `identity.resolve` + org scope.** Every new handler resolves `AuthContext` first (matches `_handle_submit`/`_handle_poll`, `server.py:157,204`). ✅

### Missing or Unclear
- **⚠️ `_handle_research_poll` skips `access_guard`.** Behavior 2 Green resolves identity and does `get_research_by_execution(ctx, ...)` (owner-scoped 404) but, unlike `_handle_poll` (`server.py:206` calls `authorize_reel_read`), does not call an authorize step. Org-scoping in the query is sufficient for tenancy, but confirm the omission is intentional (there is no per-run role gate) and state it, since reel poll has a defense-in-depth `authorize_reel_read`.

### Recommendations
- Note explicitly that research poll authorization is org-scope-only by design (no per-run role check), or add an `authorize_research_read` for symmetry.

---

## 6. Workflow Closure — ✅

### Well-Defined
- **Two BLOCKING closures correctly identified and separated from LEAF behaviors.** (§Workflow Closure) — provenance write→read (ISC-25) and research-run record→read (ISC-24). ✅
- **TRIGGER at/above `highest_new_connector`.** Closure #1 starts at `insert_or_get_queued` (integration) / the submit route (unit) — the new provenance-carrying `build_submission` branch + new SELECT. Closure #2 starts at the `POST /api/v1/research/run` route. Both are at or above the highest new connector. ✅
- **OBSERVE via the production reader, not a raw store read.** Both closures explicitly forbid re-SELECTing the column / raw store reads and assert through `get_by_execution` / `get_research_run` / the HTTP poll body. This is the correct closure discipline. ✅
- **RED-AT-SEAM specified.** Closure #1: leaving the reader as-is (no `source_research_run_id` in SELECT) fails `read.source_research_run_id == seeded_run_id`; adding the column turns it green — and independently, hardcoded `None` in `build_submission` also fails. Closure #2: without the route calling `insert_research_run`, `get_research_run` raises `NotFound` → red. ✅
- **Integration-marked, EXECUTES + fails-closed.** Uses `@pytest.mark.integration` + the `db` fixture that `pytest.skip()`s when `TEST_DATABASE_URL` is unset (confirmed real: `test_pg_reel_jobs.py` `db`/`seed` fixtures do exactly this). Not `describe.skip`; fail-closed skip, real execution when DB present. ✅
- **SELECT-omission fix is a behavior with a test reading provenance through the production reader.** Behavior 4's integration test (`test_stamped_source_research_run_id_is_read_back`) drives real write (`insert_or_get_queued`) then real read (`get_by_execution`) and asserts the value — RED until the reader SELECTs the column. ✅

### Missing or Unclear
- **⚠️ The Behavior 4 *unit* closure depends on an un-built fake read path.** The unit-level "read back via poll" (`GET /api/v1/executions/exec_c1`) can only go green if `FakeReelJobRepo` is extended to key jobs by `execution_id` and surface `source_research_run_id` (see §2). As written, the fake returns a single fixed `_job` ignoring execution_id (`conftest.py:96-101`), so the unit closure would pass trivially or falsely. The integration closure is sound; the unit closure's fake must be specified to genuinely thread the column, or the unit assertion should be dropped in favor of the integration one.

### Recommendations
- Make the integration closure the authoritative provenance round-trip; downgrade the unit "read-back" to only assert the stamp reached `insert_or_get_queued` (via the fake's `inserted` log), OR fully specify the fake's execution-keyed, provenance-carrying read model.

---

## Critical Issues

### CI-1 (❌, Data Models) — `research_run.execution_id`/`created_at` written but not in `REQUIRED_SCHEMA`
**Impact:** Production readiness (`_assert_schema`, `pg.py:76-82`) will pass with a `research_run` table missing `execution_id`/`created_at`; the first live `insert_research_run` then throws an undefined-column error at request time instead of failing-closed at startup (503). Silent readiness pass → runtime 500s. The plan's own claim that the schema is "reserved and already exists" (`plan §What We're NOT Doing`, `pg.py:40-46`) is only true for the 4-column core, not the 6 columns the writer needs.
**Fix:** Extend `REQUIRED_SCHEMA["research_run"]` to `{id, org_id, created_by, execution_id, status, created_at}` (`pg.py:40`); add a Seam Flag for the root migration (`migrations/deepresearch/`) dependency; add the columns to the new integration test's schema fixture.

### CI-2 (❌, Interfaces/Closure) — test harness dependencies (`OTHER_ORG`/`OTHER_USER`, fake extensions) do not exist
**Impact:** Behavior 2 and Behavior 4 unit tests do `from conftest import OTHER_ORG, OTHER_USER` — **neither symbol exists in `tests/web/conftest.py`** (only `ORG_ID`/`USER_ID`, `conftest.py:32-33`). The tests import-error before asserting anything. Separately, `FakeReelJobRepo.get_by_execution` ignores `execution_id` and carries no `source_research_run_id`/`research_runs` store (`conftest.py:96-101`), so the Behavior 4 unit read-back and Behavior 2 seeded-research-run path cannot function. The plan references `seed_research_run`, `get_research_run`, `get_research_by_execution`, `update_research_status` on the fake but does not specify them.
**Fix:** Add `OTHER_ORG`/`OTHER_USER` constants to conftest; fully specify the `FakeReelJobRepo` `research_runs` store + `seed_research_run` return value (a `research_run_id`) + execution-keyed job read surfacing `source_research_run_id`. List these conftest edits in Behavior 2, 3, and 4 "Files touched."

### CI-3 (❌, Promises) — orphan-execution window on the research dispatch path
**Impact:** Behavior 3's `dispatch → insert_research_run` ordering (opposite of `_handle_submit`'s row-first ordering, `server.py:163-183`) creates a live CP execution with no `research_run` row and no reaper if the process dies mid-request; the run becomes unpollable (`get_research_by_execution` 404) and unrecordable.
**Fix:** Invert to row-first: mint `run_id` → `insert_research_run(status="queued", execution_id=None)` → dispatch → `update_research_status` with the returned `execution_id`. Add terminal-monotonicity to `update_research_status`.

---

## Suggested Plan Amendments

```diff
--- Behavior 3 (research_run persistence) — schema gate
+### Readiness gate (new, prepended to Behavior 3)
+- [ ] Extend REQUIRED_SCHEMA["research_run"] in web/pg.py:40 from
+      {id, org_id, created_by, status}
+      to {id, org_id, created_by, execution_id, status, created_at}
+      so _assert_schema (pg.py:76-82) fails-closed (503) at startup if the
+      root migration lacks the columns the writer INSERTs.
+- [ ] Seam Flag: research_run.execution_id + created_at are a root-migration
+      dependency (migrations/deepresearch/) — reel-af consumes, does not own.

--- Behavior 3 Green (ordering) — mirror _handle_submit, close the orphan window
-`_handle_research_run` (Behavior 1) now: dispatch → run_id = deps.uuid_factory() →
-insert_research_run(ctx, run_id, cp["execution_id"], "queued", now) → return ...
+`_handle_research_run` now (row-first, matching server.py:163-183):
+  run_id = deps.uuid_factory()
+  insert_research_run(ctx, run_id, execution_id=None, status="queued", now)  # row first
+  status, cp, _h = dispatch_async(target, cp_body)
+  if status >= 400 or "execution_id" not in cp:
+      update_research_status(ctx, run_id, "failed"); return jsonify(cp), status
+  update_research_status(ctx, run_id, execution_id=cp["execution_id"])       # attach
+  return {"research_run_id": str(run_id), "execution_id": cp["execution_id"]}, status

--- update_research_status — terminal monotonicity (mirror pg.py:277-282)
+def update_research_status(self, ctx, run_id, status=None, execution_id=None):
+    # never downgrade a terminal run; SQL guard: status not in
+    # ('succeeded','failed','cancelled'), scoped by org_id.

--- tests/web/conftest.py (CI-2) — declare the symbols the plan's tests import
+OTHER_ORG = uuid.UUID("33333333-3333-3333-3333-333333333333")
+OTHER_USER = uuid.UUID("44444444-4444-4444-4444-444444444444")
+# FakeReelJobRepo gains:
+#   self.research_runs: dict[uuid, ResearchRunRef]           # by research_run_id
+#   self._jobs_by_exec: dict[str, ReelJobRef]                # replace single _job
+#   seed_research_run(execution_id, org_id, created_by) -> research_run_id
+#   get_research_run(ctx, run_id)            # NotFound on foreign org
+#   get_research_by_execution(ctx, exec_id)  # NotFound on foreign/absent
+#   update_research_status(ctx, run_id|exec_id, ...)
+#   get_by_execution now keys on exec_id AND carries source_research_run_id

--- web/deps.py:128-135 — extend the runtime_checkable protocol in ONE edit
+    def insert_research_run(self, ctx, run_id, execution_id, status, now): ...
+    def get_research_run(self, ctx, run_id): ...
+    def get_research_by_execution(self, ctx, execution_id): ...
+    def update_research_status(self, ctx, key, status=None, execution_id=None): ...
+    # get_by_execution return (ReelJobRef) now includes source_research_run_id

--- web/research_defaults.json — exclude secret/optional-passthrough keys
+# Mirror ui/defaults.json defaults EXCEPT `model` and `api_key`:
+# api_key is secret-shaped and must never be forwarded; model blank ⇒ server default.
+# NOTE: num_parallel_streams default is 2 (not 3) — match defaults.json.
```

---

## Approval Status

**APPROVED WITH REQUIRED CHANGES.**

The plan is architecturally sound, correctly diagnoses and closure-tests the latent SELECT-omission bug (GAP1 at `pg.py:256-268`), respects the identity-free and ownership-from-context backbone invariants, and its two BLOCKING closures satisfy the closure framework (trigger at highest-new-connector, observe via production reader, red-at-seam, fail-closed integration execution). It is **not ready to implement as written** due to three ❌ criticals that will cause import-errors, a silent readiness gap, and an orphan-execution robustness hole (CI-1, CI-2, CI-3). Resolve those three plus the terminal-monotonicity and `ResearchRunRef`-shape warnings, and the plan is green to build.

**Merge-blocking:** CI-1, CI-2, CI-3.
**Should-fix before merge:** §2 protocol single-edit + fake read model; §3 idempotency decision statement; §4 `ResearchRunRef` explicit fields; §5 research-poll authorization note.
