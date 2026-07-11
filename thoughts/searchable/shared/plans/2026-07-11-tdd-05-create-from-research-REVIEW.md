# Pre-Implementation Architectural Review — Plan 5: Create-from-Research

**Plan reviewed:** `2026-07-11-tdd-05-create-from-research.md`
**Reviewer pass:** `/review_plan` (Contracts · Interfaces · Promises · Data Models · APIs · Workflow Closure)
**Grounding:** real code — `web/server.py`, `web/reel_jobs.py`, `web/deps.py`, `web/index.html`, `tests/web/conftest.py`, `tests/web/test_submit.py`, `tests/web/test_index_contract.py`, and the consumed Plan 4 (`2026-07-11-tdd-04-research-handoff-provenance.md`).

---

## Summary Table

| # | Category | Rating | ✅ | ⚠️ | ❌ |
|---|----------|--------|----|----|----|
| 1 | Contracts | ⚠️ | 4 | 2 | 1 |
| 2 | Interfaces | ⚠️ | 4 | 2 | 0 |
| 3 | Promises | ⚠️ | 3 | 2 | 1 |
| 4 | Data Models | ❌ | 2 | 1 | 2 |
| 5 | APIs | ✅ | 5 | 1 | 0 |
| 6 | Workflow Closure | ✅ | 5 | 1 | 0 |

**Critical issues:** 3 · **Warnings:** 9 · **Approval:** ⚠️ **Approve with Required Amendments**

---

## 1. Contracts — ⚠️

### Well-Defined
- **Fan-out cardinality (`C_FANOUT`)** — `|dispatch_calls| == |outputs|`, targets == `map(outputs)` (plan §"Behavior 1", System Map contracts). Directly assertable on `FakeControlPlane.dispatch_calls` (`conftest.py:148,151`). Solid.
- **Verbatim text (`C_VERBATIM`)** — posted text == dispatched `input.text` (plan Behavior 2). Testable at the `FakeControlPlane` boundary. Matches the existing topic-passthrough style (`reel_jobs.py:136`).
- **Gate (`C_GATE`)** — empty/unknown outputs → 400, no row, no CP. Mirrors the existing forbidden-field gate contract in `build_submission` (`reel_jobs.py:114,116`).
- **Error contracts consumed from `_handle_submit`** — CP 4xx passthrough, `no_execution_id`→502, `mark_failed` on dispatch error (`server.py:176-181`) are inherited via the proposed shared `_dispatch_one` helper. Correctly referenced.

### Missing or Unclear
- ⚠️ **cp_input leak of `source_research_run_id`** — Plan Behavior 1 Green (§lines 290-291) nests `source_research_run_id` **under `input`**: `build_submission(target, {"input": {"text": ..., "source_research_run_id": ...}})`. `_clean_input` (`reel_jobs.py:92-94`) only strips `_CP_STRIP` (forbidden identity fields + `client_request_id`) — it does **NOT** strip `source_research_run_id`. So the provenance id would ride into `cp_input` and be dispatched to the reasoner. The plan repeatedly calls cp_input "identity-free" but never specifies stripping the provenance id from what the reasoner receives. Contract for "what the reasoner sees" is under-specified.
- ⚠️ **Partial-failure contract absent** — the plan asserts fan-out enqueues both, and observability names `outcome = enqueued_partial`, but there is **no stated contract** for what the *route* returns when output #1 dispatches and output #2's CP call fails (does it 502 the whole request? return 202 with a per-output status array? roll back the first row?). `_handle_submit` today `mark_failed`s then `raise`s (`server.py:173-181`) — for a single submission. Fan-out has no defined transactional/response contract. This is the sharpest gap.

### Recommendations
- Add a `_CP_STRIP` entry (or an explicit note) so `source_research_run_id` is carried on the `ReelSubmission` field (for the DB row) but **not** in `cp_input`.
- Define the partial-failure response contract explicitly (recommend: per-output result list in the 200/202 body; first-output failure short-circuits with 502 only if zero enqueued).

---

## 2. Interfaces — ⚠️

### Well-Defined
- **New route `POST /api/v1/research/create`** — distinct from Plan 4's `/api/v1/research/run` and `/api/v1/research/{id}` (plan §"Seam Flags"). No regex collision: Plan 4's matchers are `run` and `<execution_id>` GET; `create` is a POST literal, so a new `_create_from_research_subpath` predicate slots cleanly into `_api_router` (`server.py:228-238`).
- **`ALLOWLISTED_TARGETS` additions** — `TARGET_TEXT_REEL`/`TARGET_TEXT_CAROUSEL` added to the frozenset (`reel_jobs.py:26`). Matches the existing named-constant convention (`reel_jobs.py:21-23`).
- **`buildInput` research branch** — `state.mode`/`state.preset.kind` dispatch (`index.html:503-507`) extends naturally; config-driven per project rule.
- **Naming** — `reel-af.reel_research_to_reel` / `reel-af.reel_research_to_carousel` follow the `reel-af.reel_*` scheme (`reel_jobs.py:21-23`).

### Missing or Unclear
- ⚠️ **Request body key-name divergence with Plan 4** — Plan 5's body uses `source_research_run_id` (§67, §216, §231, §291). Plan 4 (§10, §119, §122) says the create path stamps provenance "from a **caller-supplied `research_run_id`**" and that deep-links carry "`research_run_id` only". The inbound key name is inconsistent across the two plans that share this seam. Pick one (`research_run_id` on the wire, mapped to `source_research_run_id` on the submission).
- ⚠️ **`_dispatch_one` signature vs. current `_handle_submit`** — the plan proposes extracting `_dispatch_one(deps, ctx, target, cp_input, crid, now)`. Today `_handle_submit` also owns `job_id = deps.uuid_factory()` and the idempotency `_idempotent_response` early-return (`server.py:163-168`). The extracted helper's boundary (does it own job_id minting and the returning-key branch, or does the caller?) is unspecified — material for the fan-out (see Data Models).

### Recommendations
- Normalize the wire key with Plan 4 before either merges.
- Specify `_dispatch_one`'s exact responsibility split (job_id, idempotency early-return, attach) so both call-sites stay identical.

---

## 3. Promises — ⚠️

### Well-Defined
- **Distinct per-output idempotency sub-keys** — `{crid}:video` / `{crid}:carousel` (plan §Constraints, `C_SUBKEY`). `FakeReelJobRepo._by_key` keys on `(org, user, crid)` (`conftest.py:68`), so distinct sub-keys → two independent rows. Verifiable.
- **Automatic auto-creates both (OD-3)** — captured as the load-bearing automated seam (ISC-30).
- **Dedup within one output** — reuses `insert_or_get_queued` returning-row semantics (`conftest.py:69-74`, `server.py:167`).

### Missing or Unclear
- ⚠️ **Ordering under partial failure** — "for each output" iterates a set (`{"video","carousel"}`); Python set iteration order is unspecified. If output #1 fails and the contract short-circuits, *which* output failed is nondeterministic. The property test asserts cardinality/targets but not order; fine for the happy path, but the partial-failure semantics (Contracts ⚠️) inherit this nondeterminism.
- ⚠️ **Duplicate-output de-dup** — plan §238 says "duplicate output types de-duplicated" but doesn't state where (validate_outputs dedups to a set? preserves order?). Underspecified against the sub-key promise.

### Missing (❌)
- ❌ **job_id collision in tests / response shape** — `_handle_submit` mints one `job_id = deps.uuid_factory()` (`server.py:163`); in tests `uuid_factory` returns the constant `FIXED_JOB_ID` (`conftest.py:181`). If fan-out mints a job_id per output via the same factory, **both rows share the same job_id under test**, and the plan never specifies the multi-job response shape (single `job_id`? list?). The `_idempotent_response`/success payload is single-job today (`server.py:80-84,192`). The plan's Red test only asserts `len(repo.inserted) == 2` and dispatch targets — it never asserts distinct job_ids or the response body, so this collision would pass tests while being wrong in production.

### Recommendations
- Specify the response body for a 2-output create (recommend a `jobs: [{output, job_id, execution_id}]` array) and assert distinct job_ids (may require the test to inject a counting `uuid_factory`).

---

## 4. Data Models — ❌

### Well-Defined
- **Create payload shape** — `{text, outputs:[...], source_research_run_id?}` (plan §67, EBNF `EV_CREATE_REQ` §613). Clear.
- **`outputs` domain** — `{"video","carousel"}` with `|S|≥1` (property §242). Clear.

### Missing or Unclear
- ⚠️ **`text` normalization boundary** — plan says "verbatim… beyond the documented normalization" (§176) but never documents *what* normalization (trim? none?). Behavior 2 Green validates `.strip()` truthiness but forwards raw `text` — so leading/trailing whitespace the user typed survives. State "no trim; only non-empty check" as the contract, or the property `input.text == T` is ambiguous for whitespace-padded `T`.

### Missing (❌)
- ❌ **`source_research_run_id` type coercion** — `ReelSubmission.source_research_run_id: uuid.UUID | None` (`reel_jobs.py:54`) and `PgReelJobRepo` binds it into the INSERT (`pg.py:212`, UUID column). JSON delivers `source_research_run_id` as a **string**. Plan Green (§291) does `body.get("source_research_run_id")` with **no `uuid.UUID(...)` coercion or validation**. A string bound to a UUID column will either fail at the DB adapter or store an unvalidated value. The plan asserts "passthrough preserved" against fakes (which don't type-check), so this passes tests but breaks against real pg. Must coerce + validate (and 400 on malformed).
- ❌ **Ownership/tenancy of `source_research_run_id`** — Plan 4 §136 requires: "a `research_run_id` owned by a **different org** → rejected." Plan 5 treats the id as a pass-through non-forbidden field with **no ownership check** and does not delegate the check to Plan 4 explicitly (it only says "authoritative stamping/provenance semantics are Plan 4's", §509). This leaves a **contract hole**: as written, Plan 5's route would stamp a cross-org research id unchecked. Either Plan 5 must call a Plan-4 `get_research_run(ctx, id)` ownership guard before stamping, or the two plans must explicitly agree who owns that guard on *this* route.

### Recommendations
- Coerce `source_research_run_id` to `uuid.UUID` with a 400 on malformed input; add a test with a fake pg that type-checks.
- Add an explicit ownership-validation seam (call Plan 4's `get_research_run`) or a written hand-off that Plan 6/4 owns it, with a `RISK-*` flag if deferred.

---

## 5. APIs — ✅

### Well-Defined
- **Auth backbone participation** — `identity.resolve(request)` → `authorize_create(ctx)` (`server.py:157-158`, `deps.py:173-175`), identity never from body. Plan §Constraints §115-119 correctly scopes the full matrix to Plan 6 and asserts *participation* only (unauth 401, viewer 403, forbidden-field 400). Matches `test_submit.py:29-51` patterns.
- **`FORBIDDEN_IDENTITY_FIELDS` rejection** — inherited via `build_submission` → `_reject_forbidden_identity` (`reel_jobs.py:73-76,114,122`). Guard listed in tests (plan §325).
- **Status codes** — 200/202 success, 400 gate/field, 401/403 auth, 502 no-execution-id — all sourced from typed exceptions in `deps.py:52-91`. Consistent.
- **Manual/E2E UI boundary honestly drawn** — the "no JS harness" reality (plan §27-46, §681) is stated up front; the UI is not claimed as automated.

### Missing or Unclear
- ⚠️ **503 store-unavailable path on fan-out** — `insert_or_get_queued` raises 503 until the DB is applied (`server.py:166` comment, `deps.py`). For fan-out, if output #1 inserts and output #2 hits a transient 503, the response/rollback is unspecified (ties to the partial-failure gap).

### Recommendations
- Fold the 503-mid-fanout case into the partial-failure contract (Contracts ⚠️).

---

## 6. Workflow Closure — ✅

### Well-Defined
- **Behaviors correctly classified** — ISC-30 and ISC-35 are **LEAF** (synchronous route, no async/registration seam *within this slice*); the genuine async generation (Plan 1) + media serving (Plan 3) are explicitly out-of-slice and referenced-not-retested (plan §192-209, §215-223). This is an honest closure boundary.
- **Automated route asserts through the production handler** — the Red test drives `POST /api/v1/research/create` via `server.create_app(...).test_client()` (plan §262-278), i.e., through `_api_router` → `_handle_create_from_research`, **not** a raw seed. Matches `test_submit.py:24-25` boundary. ✅ Correct.
- **7 UI behaviors honestly marked MANUAL/E2E** — ISC-28/29/31/32/33/34/36 (plan §206-209, §448-466) are NOT falsely claimed automated; the HTML presence-check is explicitly labeled *presence, not behavior* (§40, §418). ✅
- **RED-AT-SEAM proof present** — "fan-out disabled → two-dispatch assertion goes red" (§221). Fails-closed, never skipped (§223).
- **Dependency ordering flagged** — `RISK-DEP` (§714): live E2E blocked until Plan 4 (research proxy) + Plan 1 (text reasoners) merge; route tests green against fakes today. Plan 1 & Plan 4 must exist for the allowlist targets to resolve; correctly noted.

### Missing or Unclear
- ⚠️ **The "fan-out both" closure test can pass while wrong** (see Promises ❌ / Data Models ❌): asserting only `len(inserted)==2` + targets leaves job_id collision, cp_input leakage, and UUID coercion undetected. The closure *seam* is right; the *assertions* under-constrain it.

### Recommendations
- Strengthen the ISC-30 assertions: distinct job_ids, cp_input excludes provenance id, and a real-pg-shaped fake that type-checks `source_research_run_id`.

---

## Critical Issues

1. **`source_research_run_id` is unvalidated & un-typed & un-ownership-checked (Data Models ❌).**
   *Impact:* JSON string bound to a UUID column (`pg.py:212`) breaks against real Postgres; a cross-org research id is stamped unchecked, violating Plan 4 §136's tenancy contract; leaks into `cp_input` sent to the reasoner (`_clean_input` doesn't strip it, `reel_jobs.py:92-94`).
   *Fix:* coerce to `uuid.UUID` (400 on malformed); add `source_research_run_id` to `_CP_STRIP`; call Plan 4's `get_research_run(ctx, id)` ownership guard (or document the hand-off + `RISK` flag). Reconcile the wire key name with Plan 4 (`research_run_id`).

2. **No partial-failure / multi-job response contract (Contracts ⚠️ → elevated).**
   *Impact:* when one output enqueues and the other's CP call 502s/503s, route behavior (rollback? 502 whole? per-output status?) is undefined; `_handle_submit`'s single-submission `mark_failed`+`raise` (`server.py:173-181`) doesn't compose over a set. Set-iteration order makes *which* leg fails nondeterministic.
   *Fix:* define a per-output result array in the 2xx body; short-circuit to 502 only when zero enqueued; sort `outputs` deterministically.

3. **job_id collision + unspecified response shape hides a real bug behind green tests (Promises ❌).**
   *Impact:* `uuid_factory` is constant in tests (`conftest.py:181`); per-output minting yields identical job_ids under test, and the Red test never asserts distinct ids or the response body — production would return/persist colliding job identities while tests pass.
   *Fix:* specify the multi-job response body; inject a counting `uuid_factory` in the fan-out test and assert distinct job_ids.

---

## Suggested Plan Amendments

```diff
--- a/2026-07-11-tdd-05-create-from-research.md
+++ b/2026-07-11-tdd-05-create-from-research.md
@@ Constraints (must respect)
+- **Provenance id is validated, typed, and tenancy-checked:** the wire key is
+  `research_run_id` (align with Plan 4 §119/§122). The route coerces it to
+  `uuid.UUID` (400 `invalid_research_run_id` on malformed), and — before stamping —
+  calls Plan 4's `get_research_run(ctx, id)` so a cross-org id is rejected
+  (Plan 4 §136). It is mapped onto `ReelSubmission.source_research_run_id`
+  (a `uuid.UUID`), NOT passed under `input`.
+- **Provenance id never reaches the reasoner:** add `source_research_run_id`
+  (and `research_run_id`) to `_CP_STRIP` (`reel_jobs.py:89`) so `cp_input`
+  stays free of it; carry it only on the submission field for the DB row.

@@ Idempotency: ... per-output sub-key
+- **Per-output job_id:** mint a distinct `job_id` per output (do not reuse one).
+  The 2xx response body is `{"jobs": [{"output","job_id","execution_id"}, ...]}`.

@@ Behavior 1: Test Specification / Edge Cases
+**Partial failure:** if output #1 enqueues and output #2's CP call fails,
+the route returns 502 ONLY if zero outputs enqueued; otherwise 207-style
+2xx with the failed leg carrying `enqueue.outcome:"cp_error"`. Iterate
+`outputs` in a deterministic (sorted) order so the failing leg is stable.
+**Malformed `research_run_id` → 400; cross-org `research_run_id` → 404/403 (no row, no CP).**

@@ Behavior 1: 🔴 Red test — strengthen assertions
+    # distinct job_ids (inject a counting uuid_factory), and provenance NOT in cp_input:
+    assert len({j["job_id"] for j in resp.get_json()["jobs"]}) == 2
+    for _t, dispatched in cp.dispatch_calls:
+        assert "source_research_run_id" not in dispatched["input"]

@@ Behavior 1: 🟢 Green (reel_jobs.py)
-# build_submission: add a text branch ... carry source_research_run_id, cp_input={"text": text} identity-free
+# build_submission text branch: validate non-empty text; coerce+carry
+# source_research_run_id onto the ReelSubmission field; cp_input = {"text": text}
+# with source_research_run_id STRIPPED (via _CP_STRIP). Do not trim text.

@@ Data / normalization note (add)
+**Text normalization:** the route performs NO trim beyond the non-empty
+(`text.strip()` truthiness) check; the user's exact bytes are forwarded (ISC-35).
```

---

## Approval Status

⚠️ **APPROVE WITH REQUIRED AMENDMENTS.**

The plan's automated seam is well-chosen (LEAF fan-out route asserted through the production handler), the manual/E2E split is honest, and dependency ordering is flagged. Blocking items before implementation: (1) validate/type/tenancy-check `source_research_run_id` and keep it out of `cp_input`, reconciling the wire key with Plan 4; (2) define the partial-failure + multi-job response contract with deterministic ordering; (3) strengthen ISC-30 assertions (distinct job_ids, no provenance leak) so the closure test can't stay green while wrong. The UI/config work (Behavior 3) is sound as-is under its stated presence-check limitation.
