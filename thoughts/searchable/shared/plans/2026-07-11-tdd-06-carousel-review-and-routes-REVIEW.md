# Pre-Implementation Architectural Review — Plan 6: Carousel Review UI + Authed Carousel Routes

> Reviews `2026-07-11-tdd-06-carousel-review-and-routes.md` (the CONVERGENCE plan).
> Method: `/review_plan` — six architectural categories + the five cross-plan obligations
> Plan 6 is contracted to fulfill. Grounded in real code (`file:line`) and the sibling plans.
> **The plan file was not modified.**

## Summary Table

| # | Category | Rating | ✅ | ⚠️ | ❌ |
|---|----------|--------|----|----|----|
| 1 | Contracts | ⚠️ | 5 | 2 | 1 |
| 2 | Interfaces | ⚠️ | 4 | 2 | 1 |
| 3 | Promises (idempotency, cancel cleanup) | ⚠️ | 3 | 2 | 1 |
| 4 | Data Models (read model, repo schema, cap counter) | ⚠️ | 3 | 2 | 1 |
| 5 | APIs (auth, forbidden-identity, 404-conceal, Idempotency-Key, A3) | ⚠️ | 5 | 2 | 1 |
| 6 | Workflow Closure (2 BLOCKING + cancel side-effect) | ✅ | 3 | 1 | 0 |

**Overall: ⚠️ APPROVE WITH REQUIRED AMENDMENTS.** The route/auth/tenancy/closure spine is
excellent and faithfully mirrors the existing submit/poll backbone. Three cross-plan obligations
Plan 6 is *contracted to close* are under-specified or wrong, and would ship a broken prod
contract if implemented as written.

---

## Cross-Plan Obligations Checklist (the load-bearing part of this review)

| # | Obligation (source) | Status | Evidence |
|---|---------------------|--------|----------|
| 1 | **Real `SlideRefResolverPort` impl** (Plan 3 §35/§134 mounts fail-closed placeholder; Plan 6 makes Plan 3's image route work in prod) | ✅ **MET** | B1 §377-411: `CarouselSlideRefResolver.resolve` delegates to `repo.slide_ref`; `default_deps().slides` set to it, `not isinstance _Unconfigured` asserted (§372-373). Conformance to Plan-3 `@runtime_checkable` port asserted §366. Solid. |
| 2 | **`StoragePort.delete` contract + call on cancel + test** (Plan 3 §142 defers `delete(ref)` to Plan 6) | ✅ **MET (with flag)** | B6 §803-808 calls `deps.storage.delete(ref)` per draft ref; `FakeStorage.delete` harness added §1033-1037; asserted §782. Open Seam §1054-1057 correctly flags that `delete` is NOT in Plan 3's spec and must be added to `StoragePort`+`ObjectStorage`. **Gap:** the plan never writes the *real* `ObjectStorage.delete` Green block (S3 `delete_object`) — only the fake. See Critical #4. |
| 3 | **Persisted HQ-recreate cap** — Plan 2 §195/§205-225 (obligation 2) defers cross-request cap + **atomic register-after-success** to Plan 6's `CarouselRepoPort` | ❌ **MISSING** | Plan 6 B5 §694-695 says only "Plan 2 owns … per-carousel HQ cap (ISC-54) — this route surfaces Plan 2's cap-exceeded error as its HTTP status." No repo-backed `HqRecreateGuard`, no `register`-after-success, no atomicity, **no cross-request closure test** — all three are BLOCKING for Plan 6 per Plan 2 §217-225. `CarouselRepoPort` (§380-388) has no cap-counter method. See Critical #1. |
| 4 | **Recreate-route dep/gate ownership** — Plan 2 CI-1 §205-218 assigns `None`→real provider/storage resolution + `OPENROUTER_API_KEY` gate to Plan 6's recreate route | ❌ **MISSING** | B5 handler §726-733 calls `recreate_fn(ctx, carousel_id, slide_idx, note)` with no provider/storage resolution and no `OPENROUTER_API_KEY` gate before it. Plan 2 §216-217 is explicit: "Plan 6's recreate route MUST resolve `None` provider/storage → real implementations AND apply the `OPENROUTER_API_KEY` gate **before** calling `recreate_slide`." Only referenced obliquely ("Plan 2 logic (HQ/note/cost-guard)"). See Critical #2. |
| 5 | **Wire-key** — canonical is `research_run_id` on the wire → `source_research_run_id` DB column (Plan 5 §74-86, CANONICAL DECISION); must UUID-coerce + tenancy-check + `_CP_STRIP` | ❌ **WRONG / MISSING** | Plan 6 uses **`source_research_run_id` on the create body** (§157, §494 `body.get("source_research_run_id")`) — the exact key Plan 5 §80-85 says must NOT appear on the wire. No UUID coercion, no `get_research_run` tenancy check, and `create.cp_input()` (§589) would dispatch it to the reasoner because `_CP_STRIP` (`reel_jobs.py:89`) does not strip it (Plan 5 §146-148 flags this leak). See Critical #3. |

**3 of 5 cross-plan obligations are unmet or wrong.** These are the reason this plan is not a clean approve.

---

## Category 1 — Contracts

**Well-defined**
- Auth-before-work contract C1 (§1244-1246, ISC-49): every handler opens with `deps.identity.resolve(request)` before any repo/CP/storage call — matches `_handle_submit` (`server.py:157`) and `_handle_poll` (`server.py:204`) exactly.
- Cross-org concealment C3 (§1251-1254, ISC-51): `NotFound → 404` in the repo, mirroring `RoleAccessGuard.authorize_reel_read` (`deps.py:177-179`) and `PgReelJobRepo.get_by_execution` (`pg.py:250-265`). Correctly located in the repo, not smeared into handlers.
- Terminal monotonicity C5 (§1259-1262): `set_status` guard `status not in ('succeeded','failed','cancelled')` mirrors `update_from_execution` (`pg.py:280`). Correct.
- Identity-free dispatch C7 (§1267-1270, ISC-A3): inherited by using `deps.control_plane` (`control_plane.py:27-31` sets only `Content-Type`+`X-API-Key`). Correct.

**Missing or unclear**
- ⚠️ **Idempotent-replay response shape mismatch.** Create returns `{carousel_id, status, execution_id}` (§159, IN1 §1220), but B3 §587-588 reuses `_idempotent_response(ref)` verbatim — that helper (`server.py:77-86`) emits `{"execution_id", "job_id", "status"}` / `{"job_id", ...}`, i.e. a **`job_id`** key, never `carousel_id`. A replayed create would return a different JSON shape than a first create. The plan claims "reuse … verbatim" (§595) but the response contract diverges.
- ⚠️ **`ReelJobRef` reuse for carousels is a semantic contract smell.** `FakeCarouselRepo.insert_or_get_draft` returns a `ReelJobRef(job_id=cid, …)` (§991-1001). `ReelJobRef` is a reel-job type (`reel_jobs.py:60-70`); reusing it for a carousel overloads `job_id` to mean `carousel_id`. Acceptable for the `created` flag, but the response serializer must not surface `job_id`.
- ❌ **C8 (manifest+provenance atomic, §1271-1273) is TO-BE only** and depends on obligation #5 which is wrong on the wire. As written, provenance would leak to the reasoner (Critical #3).

**Recommendations:** define a carousel-specific `_idempotent_carousel_response` (or a `CarouselRef` with a `carousel_id` field) so the replay contract matches the first-create contract.

## Category 2 — Interfaces

**Well-defined**
- `CarouselRepoPort` (§379-388) is a clean `@runtime_checkable Protocol` mirroring `ReelJobRepoPort` (`deps.py:127-135`): `ensure_ready` + org-scoped methods. Good shape.
- `CarouselSlideRefResolver` (§404-411) satisfies Plan-3's `SlideRefResolverPort` — the real resolver, correctly a thin adapter over `repo.slide_ref`.
- `AppDeps.carousels` addition + `default_deps()` wiring (§390-400) is import-safe (no I/O at construction, B1), consistent with `deps.py:213-241`.

**Missing or unclear**
- ⚠️ **`create_app` signature is silently extended.** B5 §706 calls `server.create_app(deps, enable_supertokens=False, recreate_fn=fake_recreate)` and §735 says "`create_app` accepts `recreate_fn=`," but the current signature is `create_app(deps=None, *, enable_supertokens=True, auth_decorator=None)` (`server.py:336-338`). No Green block amends it. The `recreate_fn` must be threaded from `create_app` → `_api_router` → `_handle_carousel_recreate`; the plan never shows that plumbing.
- ⚠️ **Route-predicate subpath contract unspecified.** Existing predicates match on the `/api/`-stripped subpath (`_SUBMIT_RE = ^v1/execute/async/…`, `server.py:42`; router receives `subpath`, `server.py:368`). Plan 6's routes are `/api/v1/carousels…` (§157-168) so predicates must match `v1/carousels…` — the plan lists predicate *names* (§136-138) but no regex, so the `v1/` prefix handling is unverified.
- ❌ **`CarouselRepoPort` has no cap-counter interface** (obligation #3): no `register_hq_recreate`/`count`/atomic method. See Critical #1.

**Recommendations:** add the explicit `create_app(..., recreate_fn=None)` Green diff + router threading; write the carousel predicate regexes against the `v1/`-prefixed subpath.

## Category 3 — Promises (idempotency, cancel cleanup)

**Well-defined**
- Create idempotency (§586-588, ISC-52): `insert_or_get_draft` keyed on `(org_id, created_by, client_request_id)` with `on conflict do nothing returning id`, mirroring `PgReelJobRepo.insert_or_get_queued` (`pg.py:199-231`). `FakeCarouselRepo._by_key` (§982, §993-999) models it. Sound.
- Finalize idempotency + monotonicity (§868-882): second finalize → still `succeeded`; `cancelled` cannot be finalized. Well-derived, closure-tested.
- Cancel cleanup (§803-808, ISC-44): best-effort `storage.delete` per ref, then terminal `set_status`; delete errors swallowed but status still flips (§816-817). Idempotent re-cancel tolerated (§768). Good.

**Missing or unclear**
- ⚠️ **Idempotency dedup key uses `(org_id, crid)` in the fake but `(org_id, created_by, crid)` in prod.** `FakeCarouselRepo` keys on `(ctx.org_id, crid)` (§992) — drops `created_by`. `PgReelJobRepo` and the plan's own §78 use `(org_id, created_by, client_request_id)`. Two users in one org replaying the same key would collide in the fake but not in prod. Fake should key on all three.
- ⚠️ **Cap counter promise absent** (obligation #3): a per-carousel HQ cap that "persists across requests" is promised by Plan 2 but has no promise/mechanism here.

**Recommendations:** align the fake dedup key to `(org_id, created_by, crid)`.

## Category 4 — Data Models

**Well-defined**
- `carousel` + `carousel_slide` read model added to `REQUIRED_SCHEMA` (§134, §89) and fail-closed via `_assert_schema` (`pg.py:67-82`) — consistent with the "consume, never own migrations" rule (`pg.py:1-12`).
- Slide read-model shape `{idx, image_ref, prompt, status}` (§160, §642-643) is consistent across GET, recreate, and the resolver.
- Status vocabulary reuse (`draft` → `succeeded`/`cancelled`, §113-114) is coherent with `ReelJobStatus`.

**Missing or unclear**
- ⚠️ **`REQUIRED_SCHEMA` columns for the new tables are never enumerated.** The plan says "add `carousel` + `carousel_slide` to `REQUIRED_SCHEMA`" (§134) but does not list the required columns (contrast `reel_job`'s explicit set, `pg.py:41-45`). At minimum `carousel_slide` needs `source_research_run_id` if provenance lands here (C8). Without the column list the fail-closed gate is under-specified.
- ❌ **Cap counter data model missing** (obligation #3): no `carousel.hq_recreate_count` column or `carousel_hq_recreate` ledger table. The persisted, atomic cap Plan 2 requires has nowhere to live in the schema.

**Recommendations:** enumerate `REQUIRED_SCHEMA["carousel"]` / `["carousel_slide"]` columns explicitly; add an HQ-cap counter column/table + the SQL `update … set count = count+1 where count < cap` atomic register.

## Category 5 — APIs

**Well-defined**
- ISC-49 auth-before-work, ISC-50 forbidden-identity via reuse of `_reject_forbidden_identity` (§481-489, `reel_jobs.py:73-76`) — correctly reused, not re-authored; the "no new duplication" guard (`grep -c FORBIDDEN_IDENTITY_FIELDS`, §520) is a nice invariant.
- ISC-51 cross-org 404 on every route, ISC-52 Idempotency-Key, ISC-A3 identity-free dispatch (§563, §571) — all mirror the submit/poll precedent and are unit-tested.
- A3 anti-behavior regression (§565-571) asserts `Cookie`/`Authorization`/`org_id` absent from the dispatched body. Good.

**Missing or unclear**
- ❌ **Wire-key is wrong** (obligation #5, Critical #3): body carries `source_research_run_id` not `research_run_id`; no coercion, no tenancy check, and it would ride into `cp_input` because `_CP_STRIP` (`reel_jobs.py:89`) does not strip it. Directly contradicts Plan 5 §74-86 canonical decision and re-introduces the leak Plan 5 §146-148 fixed.
- ⚠️ **`create.cp_input()` is asserted but never defined.** §589 dispatches `{"input": create.cp_input()}`; `CarouselCreate.cp_input()` has no spec. Given the wire-key bug, this is exactly where provenance would leak — it must strip identity + `research_run_id`/`source_research_run_id` like `_clean_input` (`reel_jobs.py:92-94`).
- ⚠️ Missing-`source_text` → 400 and viewer → 403 are covered (§447), good — but the create route never calls `authorize_create` in the *recreate/finalize/cancel* handlers; those rely on org-scope `get` alone. That is acceptable for read-shaped ops but the recreate route triggers **paid** HQ regen — it should `authorize_create` (write intent), not just conceal-check.

**Recommendations:** rename the wire key to `research_run_id`, coerce to `uuid.UUID` (400 `invalid_research_run_id`), tenancy-check via Plan 4 `get_research_run`, add both keys to `_CP_STRIP`, and specify `CarouselCreate.cp_input()`. Add `authorize_create` to the recreate handler before the (costly) `recreate_fn`.

## Category 6 — Workflow Closure

**Well-defined** — this is the strongest part of the plan.
- Both BLOCKING closures (create→GET-slides §268-299; finalize→succeeded §309-327) are correctly derived from the Workflow Closure Map, TRIGGER at/above `highest_new_connector` (the new `_api_router` carousel branches, `server.py:228`), OBSERVE via the production `GET` read path, with an explicit red-at-seam proof (remove the branch → 404 → assertion red).
- FORBIDDEN SPAN is named for both (§285-288, §320-321): the test drives only HTTP entrypoints, never imports/mocks handlers or repo internals.
- DRIVABILITY: async create→pipeline edge is seeded at the repo boundary via `persist_slides` (§986), synchronous span → no clock seam needed. Correct application of the framework (§9: clock demanded IFF async).
- EXECUTES, never `skip` (§296-299, §326): all-fakes default run; live-Postgres contract fails-closed in `integration/`.
- Cancel side-effect (§329-335) closure-asserted in the same style (status read-back + `FakeStorage.deleted`).

**Missing or unclear**
- ⚠️ The create→GET closure seeds the manifest via `repo.persist_slides` (§862) — legitimate (the pipeline generation is Plan 1's closure), and the Open Seam §1058-1063 honestly flags that the *production completion trigger* (what writes `carousel_slide` rows on pipeline done) is unowned wiring shared with Plan 4. This is a real production gap, correctly surfaced, but means create→GET is closure-proven only up to the seeded boundary, not end-to-end in prod. Acceptable given the seam ownership, but should be tracked as a follow-up bead.

**Recommendations:** file the completion-trigger wiring (EV1/S6, §1303-1305) as an explicit blocking follow-up so create→GET is prod-closed, not just harness-closed.

---

## Critical Issues

### Critical #1 — Persisted, atomic HQ-recreate cap is entirely missing (obligation #3, BLOCKING)
**Impact:** Plan 2 §205-225 hands Plan 6 an explicit BLOCKING obligation: back `HqRecreateGuard` with `CarouselRepoPort` so `register`/`count` persist across HTTP requests, make `register` **atomic** (check-and-increment), and add a **cross-request closure test** ("a second HTTP recreate sees the incremented count; the `(cap+1)`th is rejected"). Plan 6 delegates the whole thing back to Plan 2 ("this route surfaces Plan 2's cap-exceeded error," §695). With an in-memory guard per request, the cap never engages — "a cost guard that resets every request is not a cost guard" (Plan 2 §219). This is a cost-safety hole on a paid premium-model path.
**Fix:** add a cap-counter method to `CarouselRepoPort` (`register_hq_recreate(ctx, carousel_id) -> int` raising `HqRecreateCapError` on `(cap+1)`), back it with an atomic SQL `update … set hq_recreate_count = hq_recreate_count + 1 where … and hq_recreate_count < %s returning …`, wire it as the real `HqRecreateGuard` into `recreate_fn`, and add the cross-request closure test to `test_carousel_routes.py`.

### Critical #2 — Recreate route does not resolve deps or gate `OPENROUTER_API_KEY` (obligation #4, BLOCKING)
**Impact:** Plan 2 CI-1 §205-218: injected provider/storage arrive `None` under the AgentField JSON-`input` envelope, and the `OPENROUTER_API_KEY` gate lives only at reasoner entrypoints (`app.py:398-399,478-479`), NOT in `generate_first_frame`/`provider.generate_image` (`images.py:107-111`). Plan 6's recreate handler (§726-733) calls `recreate_fn` with no dep resolution and no key gate → a mis-wired prod call either `RecreateDepsUnresolvedError`s or spends against an unconfigured provider.
**Fix:** in `_handle_carousel_recreate`, resolve `None` provider/storage → real impls and `if "OPENROUTER_API_KEY" not in os.environ: raise SchemaUnavailable(...)` **before** invoking `recreate_fn`, exactly as Plan 1 Behavior 8b/G6 did. Add a unit test for the gate (missing key → 503, no spend).

### Critical #3 — Wire-key for research provenance is wrong and leaks to the reasoner (obligation #5, BLOCKING)
**Impact:** Plan 5's CANONICAL DECISION (§74-86): the **API wire key is `research_run_id`**, mapped onto the `source_research_run_id` DB field; it must be UUID-coerced, tenancy-checked via Plan 4 `get_research_run`, and added to `_CP_STRIP`. Plan 6 instead reads `source_research_run_id` off the body (§157, §494), does no coercion, no tenancy check, and dispatches `create.cp_input()` (§589) — and `_CP_STRIP` (`reel_jobs.py:89`) does **not** strip either key today (Plan 5 §146-148), so the provenance id leaks into the reasoner `input`. Three plans (4/5/6) are supposed to agree on `research_run_id`; Plan 6 breaks the agreement.
**Fix:** accept `research_run_id` on the wire; `_coerce_research_run_id` → `uuid.UUID` (400 `invalid_research_run_id`); call Plan 4 `get_research_run(ctx, research_run_id)` (404 cross-org) before stamping; map onto `CarouselCreate.source_research_run_id`; add `research_run_id` + `source_research_run_id` to `_CP_STRIP`; assert absent from `cp_input` in a test (mirror Plan 5 §366).

### Critical #4 — Real `ObjectStorage.delete` (S3 `delete_object`) is never written
**Impact:** cancel (obligation #2) calls `deps.storage.delete(ref)`, and the fake gets a `delete` (§1035-1037), but the plan only *flags* adding `delete` to `ObjectStorage`/`StoragePort` in the Open Seam (§1039-1041, §1054-1057) — no Green block. In prod, `deps.storage` is the real `ObjectStorage` (Plan 3), which has no `delete` → cancel `AttributeError`s past the fake. (Not caught by tests, which use `FakeStorage`.)
**Fix:** add the `ObjectStorage.delete(ref)` Green block (mirror `presigned_url`, `_bucket()`-first fail-closed 503) + the `StoragePort.delete` protocol method, and note the Plan-3 re-spin fold-back (already flagged).

### Critical #5 — Idempotent-create replay returns `job_id`, not `carousel_id`
**Impact:** §587-588 reuses `_idempotent_response` (`server.py:77-86`) which emits `job_id`; first-create returns `carousel_id` (§159). A double-clicked Create (§609 manual criterion) gets a different JSON shape on replay → the UI can't read `carousel_id` back.
**Fix:** carousel-specific idempotent response with a `carousel_id` key.

---

## Suggested Plan Amendments

```diff
--- Behavior 3 (create) — wire-key + cp_input + idempotent response
- `POST /api/v1/carousels {source_text, source_research_run_id?, preset}`
+ `POST /api/v1/carousels {source_text, research_run_id?, preset}`   # Plan 5 canonical wire key
  def build_carousel_create(body: dict):
      ...
+     rr = body.get("research_run_id")
+     research_run_id = _coerce_research_run_id(rr) if rr else None   # 400 invalid_research_run_id
      return CarouselCreate(source_text=text, preset=(body.get("preset") or "carousel-default"),
-                           source_research_run_id=body.get("source_research_run_id"))
+                           source_research_run_id=research_run_id)   # wire research_run_id -> DB field

  def _handle_carousel_create(deps):
      ctx = deps.identity.resolve(request)
      deps.access_guard.authorize_create(ctx)
      create = build_carousel_create(request.get_json(silent=True))
+     if create.source_research_run_id:                              # tenancy-check (Plan 4)
+         deps.research_runs.get_research_run(ctx, create.source_research_run_id)  # 404 cross-org
      ...
-     if not ref.created: return _idempotent_response(ref)
+     if not ref.created: return _idempotent_carousel_response(ref)  # returns carousel_id, not job_id
      cp_body = {"input": create.cp_input()}    # cp_input() strips identity + research_run_id/source_research_run_id

--- web/reel_jobs.py — close the provenance leak (Plan 5 §148)
- _CP_STRIP = FORBIDDEN_IDENTITY_FIELDS | {"client_request_id"}
+ _CP_STRIP = FORBIDDEN_IDENTITY_FIELDS | {"client_request_id", "research_run_id", "source_research_run_id"}

--- Behavior 5 (recreate) — obligations #3 + #4 (cap + dep/key gate)
  def _handle_carousel_recreate(deps, carousel_id, slide_idx, recreate_fn):
      ctx = deps.identity.resolve(request)
+     deps.access_guard.authorize_create(ctx)                        # write intent (paid HQ regen)
      deps.carousels.get(ctx, carousel_id)                           # 404 conceal BEFORE recreate
+     if "OPENROUTER_API_KEY" not in os.environ:                     # Plan 2 CI-1 gate
+         raise SchemaUnavailable("OPENROUTER_API_KEY not set")
      note = (request.get_json(silent=True) or {}).get("note")
-     slide = recreate_fn(ctx, carousel_id, slide_idx, note)
+     slide = recreate_fn(ctx, carousel_id, slide_idx, note,
+                         provider=_resolve_provider(), storage=deps.storage,   # None->real
+                         guard=deps.carousels)                       # repo-backed atomic HqRecreateGuard
      ...

--- web/deps.py — CarouselRepoPort: persisted, atomic HQ cap (Plan 2 obligation 2)
  class CarouselRepoPort(Protocol):
      ...
+     def register_hq_recreate(self, ctx, carousel_id) -> int: ...   # atomic check-and-increment; raises HqRecreateCapError

--- web/server.py — thread recreate_fn through the factory (currently absent)
- def create_app(deps=None, *, enable_supertokens=True, auth_decorator=None) -> Flask:
+ def create_app(deps=None, *, enable_supertokens=True, auth_decorator=None, recreate_fn=None) -> Flask:
      # ... pass recreate_fn into _api_router -> _handle_carousel_recreate

--- web/pg.py — enumerate REQUIRED_SCHEMA columns (fail-closed gate, currently unlisted)
+ "carousel": {"id", "org_id", "created_by", "status", "source_research_run_id",
+              "hq_recreate_count", "created_at"},
+ "carousel_slide": {"carousel_id", "org_id", "idx", "image_ref", "prompt", "status"},

--- tests/web/conftest.py — fix the dedup key (drop of created_by)
- key = (ctx.org_id, crid)
+ key = (ctx.org_id, ctx.user_id, crid)   # match (org_id, created_by, client_request_id)
```

Add a **cross-request cap closure test** to `test_carousel_routes.py` (Plan 2 §223-225):
two sequential `POST …/recreate` HTTP calls on one carousel see an incremented persisted count; the `(cap+1)`th → cap error status.

---

## Approval Status

**⚠️ APPROVE WITH REQUIRED AMENDMENTS (do not implement as-is).**

The route/auth/tenancy/idempotency/closure architecture is strong and correctly grounded in the
existing backbone; obligations #1 (real resolver) and #2 (`StoragePort.delete` call) are met.
But **three of the five cross-plan obligations this convergence plan exists to close are unmet or
wrong** (persisted atomic HQ cap; recreate dep/key gate; `research_run_id` wire-key + `_CP_STRIP`
leak), plus two contract bugs (idempotent-replay shape; `create_app` signature). Land the five
amendments above — especially Critical #1–#3, which are BLOCKING per Plans 2 and 5 — then this is a
clean approve. The two BLOCKING workflow closures (create→GET, finalize→succeeded) and the cancel
side-effect are correctly specified and can proceed as written.

**Blocking before implementation:** Critical #1, #2, #3.
**Fix during implementation:** Critical #4, #5; the schema-column enumeration; the fake dedup key.
