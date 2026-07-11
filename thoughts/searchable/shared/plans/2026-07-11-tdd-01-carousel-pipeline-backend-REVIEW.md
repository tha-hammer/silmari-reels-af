# Pre-Implementation Architectural Review — Carousel Generation Pipeline (Backend Core)

**Plan reviewed:** `2026-07-11-tdd-01-carousel-pipeline-backend.md`
**Reviewed against real reels-af source** (all `file:line` cites verified against the working tree).
**Review type:** `/review_plan` — Contracts · Interfaces · Promises · Data Models · APIs · Workflow Closure.

---

## Summary Table

| # | Category | Status | Issues |
|---|----------|--------|--------|
| 1 | Contracts | ✅ Well-defined | 1 minor |
| 2 | Interfaces | ⚠️ Warning | 2 |
| 3 | Promises | ✅ Well-defined | 1 minor |
| 4 | Data Models | ✅ Well-defined | 1 minor |
| 5 | APIs | ⚠️ Warning | 1 |
| 6 | Workflow Closure | ❌ Critical | 3 |

**Approval status: MINOR REVISION** — the crop/model/preset/essence behaviors (B1–B7, B10) are exemplary and can be implemented as written. Three closure-level defects (production dependency wiring, an undefined production `prompt_planner`, and a miscited registration-inspection precedent) must be resolved in the plan text before B8/B9 are built, because they determine whether the reasoner is production-callable at all.

---

## 1. Contracts — ✅ Well-Defined (1 minor)

**Well-defined**
- Crop contract (C6) is airtight: `crop="4x5" => 1080×1350`, `9x16 => 720×1280`, asserted size-independently for 256/512/1000 inputs. Matches the real `_crop_to_9x16` two-branch center-crop (`images.py:75,79`) exactly; the property test correctly exercises both branches.
- Model-resolution contract (C5) `selected_model = (model or "").strip() or IMAGE_MODEL` is a single, well-specified resolution point that respects the existing import-time `IMAGE_MODEL` (`images.py:23`).
- Per-slide isolation contract (C2) is fully enumerated: failing slide → `status="failed"`, `image_ref=None`, `error` set; siblings unaffected; manifest length preserved. The contrast with `plan_beat_visuals`' `return_exceptions=False` is **accurate** (verified `visual.py:152,160`).
- Empty-text guard (C9) and body-budget bound (C10) reuse real caps `PROMPT_BODY_CHARS=14_000` (`extract.py:18`); both pre/post conditions are stated.

**Missing / unclear**
- C10's "chunked/summarized" language (Overview §4, Behavior 7 title) overstates the Green, which is pure head truncation `cleaned[:PROMPT_BODY_CHARS]`. The *contract* (≤ budget) is honest, but "summarized" implies semantic reduction that is not implemented. Rename to "truncated to budget" to avoid a false promise.

## 2. Interfaces — ⚠️ Warning (2)

**Well-defined**
- `generate_first_frame(..., *, model=None, crop="9x16")` keeps all 5 existing positional args backward-compatible (verified against `images.py:89-95` and the current 5-arg signature). Keyword-only additions are the right call.
- `essence_from_text(app, text, *, title=...)` mirrors `extract_essence(app, url)` (`extract.py:131`) and correctly reuses `_SYSTEM` (`extract.py:32`) and the `app.ai(..., schema=Essence)` shape (`extract.py:150-157`).

**Missing / unclear**
- **⚠️ The `research_to_carousel` signature does not match the codebase reasoner convention.** Every existing reasoner exposes ONLY domain params — `topic_to_reel(topic, out_dir)` (`app.py:462`), `composite_to_reel(url, preset, count, out_dir)` (`app.py:680`), `plan_visuals(beats, essence, spoken_narration)` (`app.py:286`). Dependencies (`provider`, `app`, storage) are pulled from module scope / internal imports, never passed as reasoner params. The plan bakes `provider`, `storage`, `distiller`, `prompt_planner`, `_generate_frame` into the reasoner signature. Over the control plane the reasoner is invoked with a JSON `input` envelope (see the curl examples at `app.py:474,694`), so these injected params arrive as their `None`/absent defaults in production. The plan defines the test path but not the **production default-resolution** path.
- **⚠️ `prompt_planner` has no real production implementation.** The plan says it "reuses `pick_image_moments`/`plan_visuals`" (Behavior 9 injection note; System Map TO-BE). Neither matches an `(essence, count) -> list[str]` shape: `pick_image_moments(transcript, provider, config, *, duration_s, image_count)` (`hooks.py:261`) is transcript+duration-driven (a *video* concept, no bearing on a still carousel); `plan_visuals(beats, essence, spoken_narration)` (`app.py:286`) needs beats + narration. The only concrete `prompt_planner` in the plan is a test lambda. There is no described path from `Essence` → N image prompts in production.

## 3. Promises — ✅ Well-Defined (1 minor)

**Well-defined**
- Ordering promise (C1): `[s["idx"] for s in slides] == list(range(n))`, `len==n`, proven for n∈{1,3,5}. Sequential loop (not `asyncio.gather`) is a deliberate, well-justified choice that keeps failure isolation deterministic.
- Idempotency of `regenerate_slide` (retry an `ok` slide is allowed, touches only that idx) is stated (Behavior 12 edge cases).
- No resource-cleanup or cancellation hazards — the LEAF path starts no workers, opens no long-lived sessions (the real network session lives only in `_fetch`, which the text path bypasses by contract C8).

**Missing / unclear**
- No promise about **out-of-range `idx` on the batch path**. B12 guards `idx < 0` in `regenerate_slide`, but the batch loop trusts `prompt_planner` to return exactly `n` prompts (`prompts[idx]` at Behavior 9 Green). If an injected/real planner returns `< n` prompts, `prompts[idx]` raises `IndexError` *inside* the try/except and the slide silently becomes `failed` rather than surfacing a planner-contract violation. State a planner post-condition (`len(prompts) == n`) or a guard.

## 4. Data Models — ✅ Well-Defined (1 minor)

**Well-defined**
- Slide-manifest schema `{run_id, preset, slides:[{idx, image_prompt, image_ref, status[, error]}]}` is fully typed in the EBNF (`slide_record`, `status = ok|failed`) and asserted key-by-key. It matches PRD §6.3 as claimed.
- `Essence` reuse is correct: `core_claim, mechanism, evidence:list[str](min 1), content_mode, domain`, `extra="forbid"` (verified `models.py:36`). All test fixtures populate the required fields.
- `carousel-default` preset shape (`kind, canvas_w, canvas_h, crop, slide_count`, no `middle_third/lower_third` overlay) is a clean flat dict consistent with `presets.py` "one hop" access and correctly avoids the guard's membership set (`app.py:632`).

**Missing / unclear**
- The manifest omits an **`out_dir`/`run_dir`** field, yet `regenerate_slide` (Plan 2's entry) needs `out_dir` + `run_id` to place the retried frame in the same run directory. The manifest returned by `research_to_carousel` carries `run_id` but not the resolved output path, so Plan 2 cannot reconstruct where slides live. Add `out_dir` (or the resolved `run_dir`) to the manifest.

## 5. APIs — ⚠️ Warning (1)

**Well-defined**
- Reasoner naming: `research_to_carousel` under prefix `reel` → control-plane id `reel-af.reel_research_to_carousel` (the manual check at Behavior 8 is consistent with `app.py:87` prefix + `include_router` at `app.py:821`).

**Missing / unclear**
- **⚠️ No `OPENROUTER_API_KEY` gate is specified for the carousel reasoner.** Both entry reasoners guard missing keys in-body and return `{"error": "OPENROUTER_API_KEY not set in env."}` (`topic_to_reel` `app.py:478`; `article_to_reel` per `test_gates_and_patches.py:24`). `research_to_carousel` calls `app.ai(...)` transitively via `essence_from_text` and calls the image provider, both of which need the key. The plan's Behavior 8 edge case says "import must not require keys" (true) but never adds the **runtime** gate. Without it the reasoner will fail deep inside the provider with an opaque error instead of the house `{"error": ...}` contract. Specify the gate (and a test) to match the sibling reasoners.

## 6. Workflow Closure — ❌ Critical (3)

The plan asserts **all-LEAF, no BLOCKING closure**. The crop/model/preset/essence behaviors (B1–B7, B10, B12) genuinely are LEAF and are certified correctly. But three claims underpinning the reasoner's *production completeness* do not hold:

- **❌ C-1 — Production caller / dependency wiring is unspecified (the real closure hole).** The plan's own closure rationale (§Workflow Closure) says the production caller "is the control-plane router mounted at `app.include_router(reel)` … no new worker/listener is introduced, so there is no unregistered handler to certify." That is true for *registration*, but it conflates registration with **invocability**. The registered production entry point receives only JSON `input` fields; `provider/storage/distiller/prompt_planner/_generate_frame` will be `None`. The plan never shows the `None → real implementation` resolution (real `OpenRouterProvider`, Plan-3 `StoragePort`, `essence_from_text`, a real planner). As written, a production call to the registered reasoner would pass `distiller=None` → `await None(text)` → `TypeError`, and `storage=None` → `None.put(...)`. **The reasoner is registered but not production-callable.** This is the exact "production caller must be specified" closure requirement, and it is unmet.

- **❌ C-2 — `prompt_planner` production default is undefined** (see §2). "All-LEAF" is only true because every reasoner-level test injects a lambda. The behavior "text → N image prompts" — the PRD's core "prompt-from-text" seam this plan claims to OWN — has no production leaf. This is an unclassified behavior masquerading as covered.

- **❌ C-3 — Registration-inspection precedent is miscited.** Behavior 8's helper `_registered_reasoner_names(app_module)` claims to "mirror `tests/util.py:132` style" and `tests/test_gates_and_patches.py:22`. **Verified false:** `util.py:132` is an out-of-process *config probe* (`_PROBE`) reading `app.ai_config.model` — it inspects no registry; `test_gates_and_patches.py:22` merely `import reel_af.app as app` then calls `app.topic_to_reel(...)` directly — it inspects no registry either. **No existing test inspects registered reasoner names.** The real mechanism exists and is usable — `router.reasoners` is a list of `{"func","wrapper","path",...}` (verified `agentfield/router.py:77-86`), reachable as `app_module.reel.reasoners`, so the assertion should be `any(r["func"].__name__ == "research_to_carousel" for r in app_module.reel.reasoners)`. The closure anchor must be *derived from `router.py:77`*, not invented against a mis-attributed precedent.

**Note (not a defect):** the injected-kwarg *test* call is mechanically valid — `router.py:62-71` `wrapper(*args, **kw)` forwards everything to the original `func`, so `app_module.research_to_carousel(text=..., provider=..., ...)` reaches the function. The problem is purely the missing production side.

---

## Critical Issues

| # | Issue | Impact | Fix |
|---|-------|--------|-----|
| C-1 | Reasoner deps (`provider/storage/distiller/prompt_planner/_generate_frame`) are constructor-style params with `None` defaults; production invocation passes none of them. | Registered but **not production-callable** — a live `POST …reel_research_to_carousel` hits `None(text)`/`None.put(...)` and 500s. Defeats the plan's LEAF closure claim. | Add a Behavior/step: when an injected dep is `None`, resolve the real one in-body — `distiller = distiller or (lambda t: essence_from_text(app, t))`, `storage = storage or <Plan-3 StoragePort>`, `provider = provider or OpenRouterProvider(...)`, `_generate_frame = _generate_frame or generate_first_frame`. Add a test that calls the reasoner with only domain params + monkeypatched module singletons (mirrors how `topic_to_reel` is tested). |
| C-2 | No production `prompt_planner`; `pick_image_moments`/`plan_visuals` don't fit `(essence, count) -> [str]`. | The OWNED "prompt-from-text" seam has no leaf; "all-LEAF" is untrue. | Define a concrete `plan_carousel_prompts(essence, count) -> list[str]` (one `app.ai` call, `schema=list[str]` or a small model), or explicitly reduce this plan's scope and hand the planner to Plan 2 with a stated interim (e.g. N copies of a single `Essence`-derived prompt). Assert `len(prompts)==n`. |
| C-3 | Behavior 8 registration helper cites `util.py:132` / `test_gates_and_patches.py:22`, neither of which inspects registration. | Implementer builds against a non-existent pattern; the closure anchor is invented, not derived. | Replace with the real registry: `assert any(r["func"].__name__ == "research_to_carousel" for r in app_module.reel.reasoners)` (source: `agentfield/router.py:77-86`). Update the plan's citation. |

---

## Suggested Plan Amendments

```diff
--- a/Overview §4 (text→Essence)
-   Chunks/summarizes over-long text; rejects empty/whitespace. (ISC-13…ISC-16)
+   Head-truncates over-long text to PROMPT_BODY_CHARS (not semantic summary);
+   rejects empty/whitespace. (ISC-13…ISC-16)

--- a/Current State Analysis §Reasoner registration
-   Registration is observable by importing `reel_af.app` and inspecting the router's
-   registered reasoners (pattern already used at `tests/util.py:132`,
-   `tests/test_gates_and_patches.py:22`).
+   Registration is observable via `app_module.reel.reasoners` — a list of
+   {"func","wrapper","path",...} dicts appended by the decorator
+   (agentfield/router.py:77-86). NOTE: util.py:132 is a config probe and
+   test_gates_and_patches.py:22 calls a reasoner directly; neither inspects the
+   registry. The anchor is router.py:77, not those tests.

--- a/Behavior 8 🔴 Red
-    names = _registered_reasoner_names(app_module)  # helper mirrors tests/util.py:132 style
-    assert any("research_to_carousel" in n for n in names)
+    names = [r["func"].__name__ for r in app_module.reel.reasoners]
+    assert "research_to_carousel" in names

--- a/Behavior 8 🟢 Green (add production gate + dep resolution)
 @reel.reasoner()
 async def research_to_carousel(
     text: str,
     preset: str = "carousel-default",
     slide_count: int | None = None,
     model: str | None = None,
     out_dir: str | None = None,
+    *,
+    provider=None, storage=None, distiller=None,
+    prompt_planner=None, _generate_frame=None,
 ) -> dict:
+    if "OPENROUTER_API_KEY" not in os.environ:
+        return {"error": "OPENROUTER_API_KEY not set in env."}
+    # None => real production wiring (tests inject fakes)
+    provider       = provider       or OpenRouterProvider(...)
+    storage        = storage        or _default_storage_port()   # Plan 3
+    distiller      = distiller      or (lambda t: essence_from_text(app, t))
+    prompt_planner = prompt_planner or plan_carousel_prompts       # C-2
+    _generate_frame = _generate_frame or generate_first_frame

+## NEW Behavior 8b: production call resolves real deps + gates missing key
+  Given only domain params (no injected fakes) and a monkeypatched provider/
+  storage/distiller singleton, research_to_carousel runs the real resolution
+  path (asserts each None branch is taken); with OPENROUTER_API_KEY unset it
+  returns {"error": "OPENROUTER_API_KEY not set in env."} (mirrors app.py:478).

+## NEW Behavior 9c: prompt_planner production contract
+  plan_carousel_prompts(essence, n) returns exactly n non-empty prompts
+  (len(prompts) == n); batch loop asserts this before indexing prompts[idx].

--- a/Behavior 9 🟢 Green (manifest carries out_dir)
-return {"run_id": run_id, "preset": preset, "slides": slides}
+return {"run_id": run_id, "preset": preset, "out_dir": str(run_dir), "slides": slides}

--- a/Workflow Closure (correct the all-LEAF claim)
-**No BLOCKING closure test applies — every behavior in this plan is LEAF.**
+**Crop/model/preset/essence behaviors are LEAF.** The reasoner has ONE
+production-caller obligation (Behavior 8b): the control-plane router invokes it
+with JSON input only, so the None=>real dependency resolution + the
+OPENROUTER_API_KEY gate are the production-completeness anchors and MUST be
+tested, not just the injected-fake path.
```

---

## Approval Status: **MINOR REVISION**

B1–B7, B10, B12 are strong — accurate `file:line` grounding, real crop branches, honest contracts, correct `Essence`/preset reuse, and a well-justified sequential-isolation design. The revision is scoped to the reasoner's production edge: (1) wire `None → real` deps + the API-key gate and test that path (C-1, API §5); (2) define a real `prompt_planner` or explicitly defer it with an interim (C-2); (3) fix the registration-inspection anchor to `router.reasoners` (C-3); plus two minors (manifest `out_dir`; drop "summarize" wording). None require re-architecting; all are plan-text edits before B8/B9 implementation.
