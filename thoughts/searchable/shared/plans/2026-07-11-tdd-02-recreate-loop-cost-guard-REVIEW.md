# Plan Review Report: 2026-07-11-tdd-02-recreate-loop-cost-guard (Recreate Loop + Cost Guard)

Pre-implementation architectural review of `2026-07-11-tdd-02-recreate-loop-cost-guard.md`,
grounded in the real reels-af code and the sibling Plan 1 contract.

### Review Summary

| Category | Status | Issues Found |
|----------|--------|--------------|
| Contracts | ✅ | 1 warning |
| Interfaces | ✅ | 1 warning |
| Promises | ⚠️ | 2 warnings, 1 critical |
| Data Models | ✅ | 1 warning |
| APIs | ⚠️ | 1 warning, 1 critical |
| Workflow Closure | ⚠️ | 1 warning, 1 critical |

Critical: **2** · Warnings: **7** · Approval: **Needs Minor Revision**

Ground-truth confirmations (used throughout):
- `regenerate_slide` — Plan 1 Behavior 12, `app.py`, signature
  `async def regenerate_slide(*, run_id, idx, image_prompt, out_dir, provider=None, storage=None, content_mode="general", model=None, crop="4x5", _generate_frame=generate_first_frame) -> dict`
  (Plan 1 lines 1344–1358). Returns `_slide_record` → keys `{idx, image_prompt, image_ref, status[, error]}`
  (Plan 1 EBNF line 1496). **Plan 2's OUT1 grammar (line 959, 995–998) and default `crop="4x5"` match exactly.**
- `generate_first_frame` — Plan 1 ADDs `*, model=None, crop="9x16"`; resolution rule
  `selected_model = (model or "").strip() or IMAGE_MODEL` (Plan 1 lines 285–298). Current code has
  NEITHER param (`images.py:89-95`) — they are Plan 1 deltas, correctly noted by Plan 2 (lines 65–68).
- `IMAGE_MODEL = os.getenv("REEL_AF_IMAGE_MODEL", "openrouter/google/gemini-2.5-flash-image")`
  read once at import (`images.py:23-25`). Plan 2's HQ sibling env is correctly modeled.
- **OPENROUTER_API_KEY gate lives at the reasoner entrypoints only** — `app.py:398-399` and
  `app.py:478-479`: `if "OPENROUTER_API_KEY" not in os.environ: return {"error": "OPENROUTER_API_KEY not set in env."}`.
  Downstream helpers (`generate_first_frame`, `provider.generate_image` at `images.py:107-111`) have
  NO such guard. `regenerate_slide` does not exist in `app.py` yet (grep empty), confirming Plan 2 targets a documented seam.
- Reasoner dep pattern: reasoners are registered `@reel.reasoner()` with **plain typed JSON-keyed
  params**; providers are constructed **inside the called module** (e.g. `OpenRouterProvider()` at
  `video.py:222`), or the `app` object is passed for `.ai()` text calls. There is no `deps=` container.
  Plan 1 flags (Plan 1 Overview §5, lines 43–48; gap G6 line 1670) that injected deps arrive `None`
  under the JSON `input` envelope and must be resolved in-body + gated on `OPENROUTER_API_KEY`
  (Plan 1 Behavior 8b closure).

---

### Contract Review

#### Well-Defined:
- ✅ **C1 ack-first precondition** (plan 934, 704–708, Behavior 5) — `PremiumNotAcknowledgedError`
  raised at the top of `recreate_slide` before any await/resolve/register; falsy-but-present values
  (`0, "", None`) treated as not-acknowledged (plan 678). Failure mode enumerated, ordering asserted.
- ✅ **C2 compose order + blank-note** (plan 935, 302–311) — `ValueError` on blank/whitespace note;
  order invariant (`index(original) < index(note)`) is a property test. Error contract explicit.
- ✅ **C4 single-index / siblings byte-identical / manifest unmutated** (plan 937, Behaviors 3–4) —
  out-of-range `idx` → `IndexError` before generation or cap increment (plan 525–535); a deepcopy
  before/after assertion proves purity (plan 604–621).
- ✅ **C5 cap ordering** (plan 938, 826–828) — `register` runs after ack + bounds guards, so a
  rejected recreate consumes no budget; `HqRecreateCapError` carries `{carousel_id, cap}`.
- ✅ **All three enumerated error types** (EV2/EV3/`ValueError`/`IndexError`) are distinct named
  types, each mapped to a caller disposition in the seam table (plan 1041: EV2/EV3 → HTTP 400/429).
- ✅ **A1 siblings-unchanged invariant** — owned here (Plan 1 line 1307–1309 explicitly disclaims it),
  tested end-to-end with the real `regenerate_slide` (plan 200–203, Behavior 4). Correct ownership.

#### Missing or Unclear:
- ⚠️ **`carousel["carousel_id"]` is an undeclared precondition of `recreate_slide`.** The cap register
  reads `carousel["carousel_id"]` (plan 827), and the test fixture supplies it (plan 392), but the
  input contract for the `carousel` argument (plan 122–128) never states `carousel_id` is required.
  If Plan 6 passes a manifest shaped per PRD §6.3 `{run_id, slides:[...]}` (plan 84) — which has NO
  `carousel_id` — `recreate_slide` raises a bare `KeyError`, not a typed contract error. The manifest
  shape and the `recreate_slide` input shape disagree on this key.

#### Recommendations:
- State in the IN1 contract that `carousel` MUST carry `carousel_id` and `run_id`; add an edge-case
  test that a missing `carousel_id` raises a clear typed error (or have the route pass `carousel_id`
  as its own kwarg rather than digging it out of the manifest).

---

### Interface Review

#### Well-Defined:
- ✅ **`compose_recreate_prompt(original_prompt, note) -> str`**, **`resolve_hq_model() -> str`**,
  **`recreate_slide(...) -> dict`** signatures are complete with types/defaults (plan 120–135, 302, 436, 445).
- ✅ **OUT1 → `regenerate_slide` call** matches Plan 1's exact keyword-only signature and `crop="4x5"`
  default (verified against Plan 1 lines 1344–1358). No signature drift.
- ✅ **Naming fits conventions** — `resolve_hq_model` mirrors Plan 1's `selected_model` resolution;
  `HQ_RECREATE_CAP = int(os.getenv("REEL_AF_HQ_RECREATE_CAP", "5"))` mirrors the `getenv(NAME, default)`
  tunable convention (`app.py:63-84`, `app.py:845`). `_regenerate=` injectable seam mirrors Plan 1's
  `_generate_frame=` and hooks' duck-typed provider (`hooks.py:339-375`).
- ✅ **Plan 6 call path is stated** — the route imports `recreate_slide` + `apply_recreate` + a
  repo-backed `HqRecreateGuard` (plan 167–169, seam S3, line 1041).

#### Missing or Unclear:
- ⚠️ **`apply_recreate(manifest, record)` is introduced without a signature block or a test.** It is
  named in Behavior 3 Green prose (plan 550), listed as IN4 (plan 931) and in the EBNF (plan 990),
  and declared "used by tests and by Plan 6's route" — but no Red test exercises it and its return
  contract (returns a new manifest? mutates in place?) is only described, never asserted. The
  order/length-preservation guarantee (ISC-20) rides entirely on this untested helper.

#### Recommendations:
- Add a `apply_recreate` signature + a unit test asserting: replaces by matching `idx`, preserves
  ascending order and length, and (per the C4 purity claim) does not mutate the input list.

---

### Promise Review

#### Well-Defined:
- ✅ **Idempotency of replace** — recreating an already-`ok` slide is explicitly allowed and returns
  only that index (plan 498, 519). `recreate_slide` is pure (no manifest mutation), so re-invocation
  is safe modulo cap consumption.
- ✅ **Ordering** — ack → bounds → cap-register → regenerate is a stated, tested sequence (plan 841–842).
- ✅ **`resolve_hq_model` read-at-call-time** vs `IMAGE_MODEL` read-once-at-import is a deliberate,
  documented divergence (plan 422–425) enabling `monkeypatch.setenv` in tests and no-restart ops config.

#### Missing or Unclear:
- ❌ **CRITICAL — cap-decrement vs generation-failure ordering is unspecified.** `guard.register`
  runs BEFORE `regenerate_slide` (plan 826–828, order-of-impl step 6). If `regenerate_slide` then
  fails (provider error, storage error), the cap has already been consumed but no image was produced
  — the user is charged a premium-recreate slot for a failed generation. Plan 1's `regenerate_slide`
  returns a `status:"failed"` record with an `error` key rather than raising in some paths (Plan 1
  lines 1204–1206), so `recreate_slide` may even return a *failed* record while having decremented the
  cap. Neither the "what happens if HQ generation fails after cap decrement" question nor a
  compensation/rollback (or deliberate no-rollback) decision is addressed. This is exactly the
  cost-guard's core promise ("bounds spend") interacting with its own failure mode.
- ⚠️ **Persisted-cap gap is explicit but the in-memory promise is overclaimed for `cap=0`.** GAP1
  (plan 964, 224–239, 1088) correctly defers cross-request persistence to Plan 6. But the plan asserts
  the in-memory guard is "complete for the policy layer" (plan 974); with no persistence, two
  sequential HTTP requests each get a fresh `_MemGuard` and the cap never engages in production until
  Plan 6 lands. The deferral is named; the *risk that the feature is non-functional as a cost guard
  until Plan 6* is understated (a cost guard that resets every request is not a cost guard).
- ⚠️ **No concurrency/atomicity note on `guard.register`.** Two simultaneous recreates on the same
  carousel could both read `count < cap` and both proceed (check-then-increment race). Acceptable for
  the in-memory fake, but the protocol contract Plan 6 inherits (plan 821–823) says nothing about
  atomic register, so a repo-backed guard could silently exceed the cap under concurrency.

#### Recommendations:
- Decide and document the cap-on-failure policy: either register the cap AFTER a successful
  `regenerate_slide` (only successful HQ spend counts), or keep register-before but state that a
  failed generation still consumes a slot (and why). Add a test for "regenerate fails → cap state is X".
- Add to the `HqRecreateGuard` protocol contract that `register` must be atomic (check-and-increment)
  so Plan 6's repo-backed implementation is obligated to enforce it.
- Sharpen GAP1 to state the feature does not enforce spend across requests until Plan 6.

---

### Data Model Review

#### Well-Defined:
- ✅ **SlideRecord EV1 `{idx, image_prompt, image_ref, status}`** matches Plan 1's `_slide_record`
  exactly (Plan 1 EBNF line 1496; keys confirmed `idx/image_prompt/image_ref/status[,error]`).
- ✅ **Guard state model** — `{carousel_id -> count}` in-memory map (plan 375–388); `HQ_RECREATE_CAP`
  a module int from env; per-carousel independence tested (plan 782–794).
- ✅ **Model-tier config** — `REEL_AF_IMAGE_MODEL_HQ` sibling env with defined fallback to
  `IMAGE_MODEL` (no fabricated id) (plan 58–64, 442). Sound.

#### Missing or Unclear:
- ⚠️ **EV1's `error` key is dropped from Plan 2's schema.** Plan 2's EV1 grammar (plan 1001–1002)
  lists only `{idx, image_prompt, image_ref, status:"ok"|"error"}` and uses status value `"error"`,
  whereas Plan 1's canonical record uses `status:"ok"|"failed"` with an optional `error` key
  (Plan 1 EBNF line 1496). Two mismatches: (a) the status enum value (`"error"` vs `"failed"`), and
  (b) the missing `error` field. Since `recreate_slide` returns `regenerate_slide`'s record verbatim,
  Plan 2's downstream/observability consumers (e.g. `outcome`/`error.type` span attrs, plan 1130) will
  see `status="failed"`, not `"error"`.

#### Recommendations:
- Align EV1 to Plan 1: `status = "ok" | "failed"`, add optional `error`. Fix the observability
  `outcome` mapping so a `status="failed"` record maps to span `outcome=error`.

---

### API Review

#### Well-Defined:
- ✅ **The mounted callable is precisely scoped** — Plan 2 owns `recreate_slide` (+ `apply_recreate`,
  `HqRecreateGuard`), Plan 6 owns the route `POST /api/v1/carousels/{id}/slides/{idx}/recreate`, auth,
  tenancy, idempotency (plan 8–11, 167–169). Boundary is clean and stated.
- ✅ **Error surface for the route is enumerated** — EV2 → 400, EV3 → 400/429, EV1 → JSON body
  (seam S3, plan 1041).

#### Missing or Unclear:
- ❌ **CRITICAL — the provider/storage resolution obligation for Plan 6 is not stated.** In every
  Plan 2 test, `provider=` and `storage=` are injected fakes. But the production route (Plan 6) is an
  HTTP handler, and Plan 1 established that under the AgentField JSON-`input` envelope, injected deps
  arrive as `None` and the reasoner must resolve `None → OpenRouterProvider()` / real storage AND gate
  `OPENROUTER_API_KEY` in-body (Plan 1 Overview §5 lines 43–48; gate at `app.py:478-479`).
  `recreate_slide` has NO `None → real` resolution and NO key gate — it passes whatever `provider` it
  is handed straight into `regenerate_slide` → `generate_first_frame` → `provider.generate_image`
  (`images.py:107-111`), which has no guard. If Plan 6 mounts this the way reasoners are mounted,
  `provider`/`storage` will be `None` and the call crashes with an `AttributeError` on `None`, not the
  clean `{"error": "OPENROUTER_API_KEY not set"}` the rest of the system returns. Plan 2 must
  EXPLICITLY assign the None-resolution + key-gate obligation to Plan 6 (or provide a resolver seam),
  the same way Plan 1 discharged it in Behavior 8b. Right now that obligation is silently unowned.
- ⚠️ **`crop` default drift is latent but real.** `recreate_slide` defaults `crop="4x5"` (matches
  `regenerate_slide`), but `generate_first_frame`'s own default is `"9x16"` (Plan 1 line 293). Since
  `regenerate_slide` always passes `crop` through, this is currently consistent — but the plan never
  states WHY carousel recreate uses `4x5` (carousel aspect) vs the reel `9x16`, so a future edit to
  either default could desync silently. Worth a one-line rationale.

#### Recommendations:
- Add a "Consumed obligation for Plan 6" note: the route must resolve `None` provider/storage to real
  implementations and gate `OPENROUTER_API_KEY` before calling `recreate_slide`, mirroring
  `app.py:478-479` / Plan 1 Behavior 8b. Optionally add a `_resolve_deps` seam here so the gate is
  testable in this module.
- State the `crop="4x5"` rationale (carousel aspect ratio) so the default is intentional, not incidental.

---

### Workflow Closure Review

#### Well-Defined:
- ✅ **All-LEAF classification is present and per-behavior justified** (plan 205–239). Every behavior
  is a same-module pure/async call with fakes at the boundary; no cross-process async edge, no
  registration boundary is crossed at runtime in Plan 2's own tests. This is defensible: `recreate_slide`
  is called directly in-process (plan 188–191), and the one production async seam (slide file →
  fetchable `image_ref` via `StoragePort`) is owned + closure-tested by Plan 3 (plan 234).
- ✅ **Deferred closures are named, not hidden** — persisted-cap cross-request closure → Plan 6
  (plan 224–239); route/auth/idempotency → Plan 6; fetch/serve → Plan 3 (plan 866–871). Ownership is
  explicit and correct for a mid-stack policy plan.
- ✅ **Integration test crosses the real seam** — one in-process test drives policy → real
  `regenerate_slide` → fake provider/storage, asserting HQ model + composed prompt + one `put` +
  siblings unchanged + cap incremented (plan 860–865). This is a boundary-inward source-to-sink that
  still crosses the new `recreate_slide` → `regenerate_slide` connector (`highest_new_connector` for
  this slice). No downstream read model is seeded directly.

#### Missing or Unclear:
- ❌ **CRITICAL (shared with API review) — the recreate function's ONLY production reachability is via
  Plan 6's route, and the dep-resolution/key-gate connector into it is unspecified.** The all-LEAF
  claim is valid for Plan 2's units, but the *production* trigger→sink chain
  (route → resolve deps + gate key → `recreate_slide` → `regenerate_slide` → `StoragePort` → UI) has
  an unmodeled node: the `None → real provider/storage` + `OPENROUTER_API_KEY` gate that Plan 1 proved
  is mandatory for anything reachable over the AgentField control plane (Plan 1 G6, Behavior 8b).
  Plan 2 neither owns it nor explicitly hands it to Plan 6 with the `app.py:478` evidence. Per the
  closure framework this is a "handler has an unspecified production caller / the trigger sits below
  a required new connector" gap — it must be assigned before Plan 6 can close the chain.
- ⚠️ **Persisted-cap deferral is the one place the LEAF claim leans on Plan 6 for correctness, not
  just for wiring.** The in-memory guard means the cap behavior is *tested* but not *production-closed*
  until Plan 6 supplies a repo-backed guard and a cross-request "second recreate sees incremented
  count" test. Plan 2 states this (plan 224–239) — good — but should mark it as a BLOCKING obligation
  ON Plan 6 (a required closure test Plan 6 must add), not merely "deferred."

#### Recommendations:
- Add an explicit "Obligations handed to Plan 6" subsection: (1) resolve `None` provider/storage +
  gate `OPENROUTER_API_KEY` before `recreate_slide` (cite `app.py:478-479`, Plan 1 Behavior 8b);
  (2) supply a repo-backed atomic `HqRecreateGuard` and a cross-request cap closure test. Frame both
  as BLOCKING for Plan 6.

---

### Critical Issues (Must Address Before Implementation)

1. **APIs / Workflow Closure — provider/storage `None`-resolution + `OPENROUTER_API_KEY` gate is
   unowned.**
   - Impact: When Plan 6 mounts `recreate_slide` behind an HTTP route on the AgentField control plane,
     injected `provider`/`storage` arrive `None` (Plan 1 Overview §5); `recreate_slide` has no in-body
     resolution and no key gate, so it calls `provider.generate_image` on `None` and crashes with an
     `AttributeError` instead of the system-standard `{"error": "OPENROUTER_API_KEY not set"}`. Every
     production recreate fails ungracefully. This is the exact failure Plan 1 closed with Behavior 8b.
   - Recommendation: Add an "Obligations for Plan 6" note requiring `None → real` resolution + the key
     gate before `recreate_slide` (cite `app.py:398-399`, `app.py:478-479`, Plan 1 Behavior 8b/G6), or
     add a `_resolve_deps` seam in `recreate.py` so the gate is testable here.

2. **Promises — cap decrement occurs before generation; behavior on HQ-generation failure is
   undefined.**
   - Impact: `guard.register` runs before `regenerate_slide` (plan 826–828). A provider/storage failure
     after register consumes a premium-recreate slot for a slide that was never produced; because
     Plan 1's `regenerate_slide` can return a `status:"failed"` record rather than raise, `recreate_slide`
     may return a failed record with the cap already decremented. The cost guard mis-charges on failure.
   - Recommendation: Choose and document register-after-success (only successful HQ spend counts) OR
     register-before-with-explicit-no-refund; add a "regenerate fails → cap state" test.

### Suggested Plan Amendments

```diff
# In "Desired End State" / Behavior contract (plan 137-149)
+ Precondition: `carousel` MUST carry `carousel_id` and `run_id`; a missing `carousel_id`
+   raises a typed error (not a bare KeyError). (Contract C-input)

# In Behavior 2 / API surface (add "Obligations handed to Plan 6" subsection)
+ Plan 6 route MUST resolve None provider/storage → real implementations AND gate
+   `OPENROUTER_API_KEY` (app.py:478-479) before calling recreate_slide — mirrors Plan 1
+   Behavior 8b / gap G6. recreate_slide itself does NOT resolve deps or gate the key.
+ (Optional) add `_resolve_deps` seam in recreate.py so the key gate is unit-testable here.

# In Behavior 6 (cap) — Test Specification + Order of Implementation step 6
~ Modify: decide cap-vs-failure ordering.
+   Option A (recommended): guard.register AFTER a successful regenerate_slide.
+   Option B: register before, document "failed HQ generation still consumes a slot" + why.
+ Add test: regenerate_slide fails → assert cap count == <chosen policy>.
+ Add to HqRecreateGuard protocol contract: register() MUST be atomic (check-and-increment)
+   so Plan 6's repo-backed guard cannot exceed cap under concurrent requests.

# In Behavior 3 (apply_recreate)
+ Add: apply_recreate(manifest, record) signature block + a Red test asserting replace-by-idx,
+   order + length preserved, input list not mutated.

# In System Map EV1 grammar (plan 1001-1002) + Observability outcome mapping (plan 1130)
~ Modify: SlideRecord status enum to Plan 1's canonical: "ok" | "failed" (not "error"),
+   add optional `error` key; map status="failed" → span outcome=error.

# In Behavior 2 note (plan 65-68) / References
+ Add one-line rationale: recreate uses crop="4x5" (carousel aspect) deliberately vs reel 9x16.

# In Workflow Closure / GAP1 (plan 224-239, 1088)
~ Modify: mark the persisted-cap cross-request closure and the repo-backed atomic guard as a
+   BLOCKING obligation ON Plan 6 (a required closure test), not merely "deferred". State the
+   in-memory guard does NOT enforce spend across requests until Plan 6 lands.
```

### Approval Status

- [ ] **Ready for Implementation** — No critical issues
- [x] **Needs Minor Revision** — Address the 2 critical issues (Plan 6 dep-resolution/key-gate
      obligation; cap-vs-failure ordering) and the EV1 status-enum mismatch before implementation.
      All of Plan 2's *own* unit behaviors (compose, HQ resolve, single-index, siblings, ack, cap
      boundary) are well-specified and the OUT1/`regenerate_slide` contract aligns exactly with
      Plan 1 — the criticals are boundary-ownership and failure-mode gaps, not core-logic defects,
      so this is a minor (not major) revision.
- [ ] **Needs Major Revision** — Critical issues must be resolved first
