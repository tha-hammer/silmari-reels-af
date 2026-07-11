# Plan Review Report: Media Serving — StoragePort + Object-Storage Adapter (Plan 3 of 6)

**Plan reviewed:** `thoughts/searchable/shared/plans/2026-07-11-tdd-03-media-serving-storageport.md`
**Review type:** Pre-implementation architectural review (`/review_plan`)
**Date:** 2026-07-11
**Grounding:** All code citations verified against live `silmari-reels-af/web/` and `tests/web/`.

---

### Review Summary

| Category | Status | Issues Found |
|----------|--------|--------------|
| Contracts | ✅ | 1 warning |
| Interfaces | ⚠️ | 2 warnings |
| Promises | ⚠️ | 2 warnings |
| Data Models | ✅ | 1 warning |
| APIs | ✅ | 1 warning |
| Workflow Closure | ✅ | 0 issues |

**Totals:** 0 critical · 7 warnings. No BLOCKING closure defects. Overall: **Ready for Implementation (Minor Revision recommended).**

---

### Contract Review

**Component boundaries** (plan §Overview, §System Map): three well-drawn boundaries — Media Delivery (route), Object Storage (adapter), Carousel (provisional port-only). Data crossing each seam is enumerated (S1–S6) and each contract (C1–C7) names its target with pre/post/invariant. This is unusually complete.

#### Well-Defined:
- ✅ **StoragePort surface** (`put`/`presigned_url`/`exists`) — input/output contracts specified per method; org-scoping invariant C5 ("ref always begins with `str(org_id) + '/'`") is stated as a Hypothesis property (plan Behavior 1). Matches the house convention `_object_key = f"{ctx.org_id}/..."` (`web/uploads.py:46-47`).
- ✅ **Fail-closed-when-`REEL_BUCKET_NAME`-unset (503)** — C7 targets IN2/IN3/IN4; every public method calls `_bucket()` first, raising `SchemaUnavailable` before any client call (plan Behavior 3). Mirrors the real precedent `BucketUploadStore._bucket()` raising `SchemaUnavailable` (`web/uploads.py:109-113`) and the fail-closed test `test_bucket_upload.py:91-94`.
- ✅ **Cross-org concealment as 404** — C2 targets the resolver; matches the verified precedent `RoleAccessGuard.authorize_reel_read` which raises `NotFound("job not found")` — 404 not 403 — on a foreign-org row (`web/deps.py:177-182`, raise at :179).
- ✅ **Error contracts enumerated** — `SchemaUnavailable`=503, `NotFound`=404, `BadRequest`=400, `Unauthorized`=401 all carry `status` + `code` and are serialized by the app error handler `{"error", "code"}` (`web/server.py:373`), confirming ISC-48's `resp.get_json()["code"]` assertion is valid.

#### Missing or Unclear:
- ⚠️ **`StoragePort.delete` gap not acknowledged.** The task brief and PRD note Plan 6 adds `StoragePort.delete`. This plan defines the port with exactly three methods (plan lines 519-523) and never states that `delete` is a deliberate forward-extension owned by Plan 6. A silent omission risks Plan 6 redefining the port. The Seam-ownership note (plan lines 720-728) covers `SlideRefResolverPort` cross-plan ownership but says nothing about `delete`.

#### Recommendations:
- Add one line to the Seam-ownership note: "`StoragePort.delete(ref)` is a deliberate forward-extension owned by Plan 6 (cleanup); Plan 3 defines only `put`/`presigned_url`/`exists`. Plan 6 *extends*, never *redefines*, the port." This closes the cross-plan coherence gap explicitly.

---

### Interface Review

#### Well-Defined:
- ✅ **`StoragePort` protocol** uses `@runtime_checkable Protocol` exactly like the existing ports (`web/deps.py:110-149`); `isinstance` conformance is asserted for both `ObjectStorage` and `FakeStorage` (plan Behavior 4).
- ✅ **Predicate shape matches precedent.** The new `_slide_target(method, sub)` mirrors `_submit_target`/`_poll_id` verbatim: regex at module scope, method guard, pure/no-I/O, returns `tuple | None` (verified `web/server.py:41-61`). Mount point `_api_router(deps, subpath)` and dispatch style are correct.
- ✅ **Adapter vs fake parity.** `FakeStorage` (plan lines 690-705) implements the same three methods + a test-only `presigned_for`/`presign_calls` probe; `make_deps(..., storage=, slides=)` extension is correct — the live `make_deps` threads only identity/reel_jobs/uploads/control_plane (`tests/web/conftest.py:167-183`), so these kwargs are genuinely new.

#### Missing or Unclear:
- ⚠️ **Naming convention inconsistency inherited.** `SlideRefResolverPort` and `StoragePort` correctly end in `Port`. But note the codebase already carries one non-`Port` port (`IdentityProvider`, `web/deps.py:110`). The plan follows the *majority* convention (good), but should note it is not renaming `IdentityProvider` (out of scope) so a reviewer doesn't flag the mixed naming as this plan's doing.
- ⚠️ **`_handle_slide` return shape is redundant, not wrong.** Plan line 646 returns `redirect(url, code=302), 302`. Every existing handler returns `(Response, int)` (e.g. `_handle_poll` → `jsonify(...), status`, `web/server.py:216`). `redirect()` returns a `Response`, so `(Response, 302)` is shape-consistent — but the `code=302` inside `redirect()` **and** the trailing `, 302` set the status twice (Flask's tuple status wins). Harmless, but a cleaner form is `return redirect(url, code=302)` (Flask honors the Response's own 302) OR `return redirect(url), 302`. Pick one to avoid a confusing double-declaration.

#### Recommendations:
- Change plan line 646 to `return redirect(url, code=302)` (single source of the 302), or note the redundancy is intentional.
- Add a half-sentence: "This plan does not rename `IdentityProvider` (`deps.py:110`); the `…Port` suffix applies to new ports only."

---

### Promise Review

#### Well-Defined:
- ✅ **Presigned-URL TTL/expiry** — `presigned_url(ref, ttl)` resolves `ttl if ttl is not None else _presign_ttl_s()` (default 3600 via `REEL_PRESIGN_TTL_S`); tests assert the expiry is reflected in the URL and that env override is honored (plan Behavior 2). Matches `web/uploads.py:30-31,143-155`.
- ✅ **Idempotency of `put`** — C5 promises "same `(org_id, key)` → same ref"; the ref is a pure function `f"{org_id}/{key}"` (plan line 317), so re-`put` is naturally idempotent-addressed. Asserted (plan line 269).
- ✅ **Fail-closed placeholder promise for the resolver** — `default_deps().slides = _Unconfigured(SchemaUnavailable, ...)` (plan line 537). Verified: `_Unconfigured.__getattr__` returns `_fail` for *any* attribute, so `deps.slides.resolve(...)` raises `SchemaUnavailable` → 503 (`web/deps.py:185-195`). This 503 is **safe, not a leak**: it denies before any ref lookup or URL mint, so no cross-org existence signal escapes. The plan documents this as "wired but 503s in prod until Plan 6 lands" (plan lines 727-728).

#### Missing or Unclear:
- ⚠️ **`put` object-overwrite semantics under a stable ref not stated.** Same `(org_id, key)` yields the same ref → a second `put` with *different bytes* overwrites the object silently. The plan proves ref stability but never says whether overwriting existing bytes is intended (last-write-wins) or should be rejected. For a slide-recreate flow (Plan 2/6) this matters. State the intended semantics.
- ⚠️ **Cleanup/expiry of objects is out of scope but the ISC-48 "expired" case leans on it.** ISC-48 (plan line 118) tests "object no longer exists (expired/deleted)". Nothing in this slice *deletes* or *expires* objects (`delete` is Plan 6; presigned-URL expiry ≠ object expiry). The test simulates it via `FakeStorage(objects={})` + a resolvable ref (plan line 609), which is fine — but the plan should note that real object expiry/lifecycle is not configured here, so ISC-48's "expired" branch is exercised only via the missing-object path until Plan 6.

#### Recommendations:
- Add to §What We're NOT Doing: "`put` is last-write-wins on a stable ref; object lifecycle/expiry and `delete` are Plan 6." One sentence resolves both warnings.

---

### Data Model Review

#### Well-Defined:
- ✅ **Object key scheme** — `ref = f"{org_id}/{key.lstrip('/')}"` (plan line 317); org-scoped, matches `_object_key` prefix convention (`web/uploads.py:46-47`). The ref *is* the S3 Key (opaque, self-describing) — clean, no separate ref table needed.
- ✅ **TTL config** — `REEL_PRESIGN_TTL_S` int, default 3600 (plan lines 290-291); reuses the exact env + default as uploads (`web/uploads.py:22,30-31`).
- ✅ **Serialization** — bytes/file-like accepted on `put` (`isinstance(data, (bytes, bytearray))` else `.read()`, plan line 322); mirrors `BucketUploadStore` `upload_fileobj` handling.

#### Missing or Unclear:
- ⚠️ **Ref opacity vs. transparency tension.** The ref is documented as "stable opaque ref" (plan line 103) but is in fact fully transparent (`<org_id>/<key>`), and the observability span even derives `org_id` "from ref prefix" (plan line 1068). Callers could parse it. This is fine, but "opaque" is misleading — either call it "org-prefixed key-ref" or commit to opacity (and then the observability prefix-parse is a contract violation). Pick one framing.

#### Recommendations:
- Rename "opaque ref" → "org-prefixed ref" throughout, OR remove the `org_id`-from-prefix derivation in the `storage.presign` span (plan line 1068) and pass `org_id` explicitly. Consistency only; no behavior change.

---

### API Review

#### Well-Defined:
- ✅ **Route contract** — `GET /api/v1/carousels/<cid>/slides/<idx>` → 302 (owner), 404 (cross-org, concealed), 404 (missing/expired + code), 401 (no session), 503 (unconfigured). All status codes enumerated and mapped to the existing `HttpError` handler (`web/server.py:373`). Non-integer `<idx>` falls through `_SLIDE_RE` → `_not_found` → 404 (plan line 575).
- ✅ **Auth via `identity.resolve`** — `_handle_slide` resolves identity first (plan line 641), matching `_handle_poll` auth-before-work ordering (`web/server.py:207`). The no-session-401-before-storage test (plan lines 616-621) mirrors `test_poll.py:34-38`.
- ✅ **Identity never from body** — the GET route carries no body, so `FORBIDDEN_IDENTITY_FIELDS` (`web/reel_jobs.py:32-45`, enforced :73-76) is inherited by *having no identity input surface* (plan line 74). Correct and correctly reasoned — there is nothing to reject because nothing is accepted.

#### Missing or Unclear:
- ⚠️ **404-vs-503 distinguishability for the concealment guarantee.** With the Plan-6 resolver still `_Unconfigured`, *every* slide request in prod returns **503** (resolver fails first, before org check). That is the documented interim state — but it means ISC-47's cross-org-404 concealment cannot be observed in prod until Plan 6, only in tests (where `FakeSlideRefResolver` is injected). The plan should state that the *concealment invariant is test-verified now, prod-active at Plan 6* so no one mistakes the interim 503 for the concealment path.

#### Recommendations:
- Add to the Seam-ownership note: "Until Plan 6 wires the real resolver, prod returns 503 for all slide requests (resolver `_Unconfigured`); ISC-47's 404-concealment is verified via `FakeSlideRefResolver` in `test_slide_route.py` and becomes prod-active when Plan 6 lands."

---

### Workflow Closure Review

The image-serving route is correctly declared the single **BLOCKING** behavior (plan Behavior 5, §Workflow Closure); the five adapter/port behaviors are classified **LEAF** with per-behavior reasons (plan lines 210, 352, 420, 484). No behavior is unclassified.

#### Well-Defined:
- ✅ **Production operation chain fully drawn** (plan lines 168-175): browser GET → `create_app` `/api/<path>` (`web/server.py:347-353`, verified) → `_api_router` → `_slide_target` (new) → `_handle_slide` → `identity.resolve` → `slides.resolve` → `storage.exists` → `presigned_url` → 302. Every hop maps to real, verified code.
- ✅ **TRIGGER at/above `highest_new_connector`.** TRIGGER is the mounted Flask route via `test_client().get(url)` (plan line 183); `highest_new_connector` is the `_api_router` slide branch this slice adds (`web/server.py:212`). Starting at the HTTP entrypoint crosses it. Correct.
- ✅ **OBSERVABLE via production read path** — asserts `resp.status_code == 302` and `resp.headers["Location"]` = URL from the **real** `ObjectStorage.presigned_url` (plan lines 188-191). No raw store read.
- ✅ **Red-at-seam specified** — remove the `_slide_target` branch from `_api_router` → route falls to `_not_found()` → owner-fetch `302` assertion goes red; re-add → green (plan lines 195-197). Names the exact connector + expected red assertion.
- ✅ **Drivability** — `StoragePort` + `SlideRefResolverPort` injected via `AppDeps`, seeded through fakes; real `ObjectStorage` uses injected S3 `client_factory` (boundary mock). Span is **synchronous** → correctly states no clock/driver seam needed (plan lines 186, 198-201). No span-mocking.
- ✅ **Execution guarantee (fails-closed, not skip).** Closure test runs in the default `uv run pytest tests/web/test_slide_route.py` with no external infra (both boundaries always-present fakes) → "always executes (never skip)" (plan lines 202-206). The real-bucket variant lives behind `@pytest.mark.integration` and fails-closed (red) if a configured bucket is unreachable (plan lines 732-735). This matches the repo precedent `tests/test_finish_closure.py`, which drives the real `finish_reel()` entrypoint and `pytest.fail()`s (not skips) when ffmpeg is absent (verified `test_finish_closure.py:37-39,153-162`).
- ✅ **Runtime context across edges** — `AuthContext` (frozen, server-trusted, `web/deps.py:97-104`) flows from `identity.resolve` into `slides.resolve(ctx, ...)`; org-scope is re-established at the resolver seam, not carried in the URL. Correct.
- ✅ **No silent/degraded failure** — `exists(ref) is False` raises `NotFound` (404 + code), never a swallowed 500 (plan line 644); `exists` swallows only the boundary `ClientError` into `False`, never `SchemaUnavailable` (plan lines 462, 470-471). Degraded (unconfigured) path is a distinct 503.

#### Missing or Unclear:
- (none blocking)

#### Recommendations:
- Optional: the closure test's FORBIDDEN SPAN list (plan line 192) is exemplary — keep it verbatim in the test docstring so the "no raw store read / no internal mock" rule survives implementation.

---

### Critical Issues (Must Address Before Implementation)

**None.** No critical (❌) findings. The plan has no unclassified workflow behavior, no un-mounted handler, no test that seeds the read model, no span-mock, and a fails-closed closure test derived from the map. All seven findings are warnings (⚠️) that improve clarity/coherence but do not block implementation.

---

### Suggested Plan Amendments

```diff
# In §What We're NOT Doing (plan ~line 126-136)

+ - `put` is **last-write-wins** on a stable ref; object lifecycle/expiry and
+   `StoragePort.delete(ref)` are Plan 6. ISC-48's "expired" branch is exercised
+   here only via the missing-object path (FakeStorage(objects={})).

# In §Harness additions → Seam ownership note (plan ~line 720-728)

+ - **`StoragePort.delete(ref)` is a deliberate forward-extension owned by Plan 6**
+   (cleanup). Plan 3 defines only put/presigned_url/exists; Plan 6 *extends*,
+   never *redefines*, the port.
+ - Until Plan 6 wires the real resolver, **prod returns 503 for all slide
+   requests** (`slides` is `_Unconfigured`); ISC-47's 404-concealment is
+   test-verified now (FakeSlideRefResolver) and becomes prod-active at Plan 6.
+ - This plan does not rename `IdentityProvider` (deps.py:110); the `…Port`
+   suffix applies to new ports only.

# In Behavior 5 → 🟢 Green (plan line 646)

~ -    return redirect(url, code=302), 302
~ +    return redirect(url, code=302)   # Response carries its own 302; matches (Response,int) handler shape

# In §Data Models / Observability (plan line 103, 1068)

~ Rename "stable opaque ref" → "org-prefixed ref" (it IS parseable: <org_id>/<key>),
~ OR drop the "org_id derived from ref prefix" note in the storage.presign span
~ and pass org_id explicitly. Pick one framing for consistency.

# Minor line-drift fixes (non-blocking; verified actuals)
~ _bucket() is web/uploads.py:109-113 (plan cites :104-108)
~ presign() empty-ref code is "missing_source" in BucketUploadStore (uploads.py:146);
   plan's ObjectStorage uses code="missing_ref" (line 393) — intentional new code, fine,
   but note the divergence so it's not mistaken for a copy error.
```

### Approval Status

- [x] **Ready for Implementation** — No critical issues. (Address the 7 warnings via the amendments above for coherence; none block starting.)
- [ ] **Needs Minor Revision**
- [ ] **Needs Major Revision**

**Rationale:** Contracts, APIs, and Workflow Closure are ✅ and grounded in verified real code. The BLOCKING closure test is correctly derived from the map, drivable without a span-mock, and fails-closed (not skip) — matching the repo's `test_finish_closure.py` precedent. Interfaces/Promises/Data-Models carry only clarity warnings (the `delete` cross-plan gap, `put` overwrite semantics, interim-503 concealment framing, "opaque" ref naming). The three-line Seam-ownership amendment resolves the cross-plan coherence risks and is strongly recommended before Plan 6 begins.
