---
title: Image Carousel Pipeline + Deep-Research → Reels-AF Handoff
slug: carousel-image-pipeline-and-research-handoff
type: PRD
status: draft
effort: E4
created: 2026-07-11
owner: Maceo Jourdan
repos:
  - silmari-reels-af (primary — receiver, UI, pipeline)
  - silmari-af-deep-research (minimal — "Send to reels" button)
feeds: /create_tdd_plan → /implement_plan
---

# PRD — Image Carousel Pipeline + Deep-Research → Reels-AF Handoff

## 1. Summary

Extend `reel-af` to produce **image carousels** (ordered sets of still images) in addition to
video, and wire a **research-to-reels workflow** so a user can run a deep-research query and pipe
the result straight into `reel-af` to create a **video and/or image carousel**.

Two entry directions:

- **Push** — the deep-research UI gets a single **"Send to reels"** button. Deep-research stays
  *research-only*; the button hands the finished research to `reel-af`.
- **Pull** — the `reel-af` UI gets a **"Create from research"** workflow with two modes
  (**Automatic** and **Full control**), a research-document display, a **video | carousel**
  multi-select, and a **carousel review screen** with a per-image **recreate-with-note** loop that
  uses a higher-quality (more expensive) image model.

This PRD is grounded in the current code. It defines scope, contracts, acceptance criteria, and the
prerequisites that gate the feature. It is the input to a subsequent TDD implementation plan; it is
**not** an implementation.

## 2. Current State (grounded in code)

### 2.1 reel-af render/reasoner pipeline
- Reasoners are declared with `@reel.reasoner()` on node id `reel-af` and are HTTP-callable at
  `POST /api/v1/execute/async/reel-af.reel_<name>` with `{"input": {...}}` → `execution_id`
  (`src/reel_af/app.py:87`, example at `app.py:32-38,694-696`).
- Entry reasoners: `article_to_reel(url)` (`app.py:385`), `topic_to_reel(topic)` (`app.py:463`),
  `composite_to_reel(url, preset, count)` (`app.py:680` → blocking `_run_composite_reels`
  `app.py:613`).
- **Still-image generation already exists.** `generate_first_frame(provider, image_prompt, idx,
  out_dir, content_mode) -> Path` (`render/images.py:89`) is the atomic *prompt → image file*
  function; it calls `provider.generate_image(...)` and crops to **9:16 720×1280**
  (`images.py:60,107`). Image model: `IMAGE_MODEL = os.getenv("REEL_AF_IMAGE_MODEL",
  "openrouter/google/gemini-2.5-flash-image")`, **read once at import** (`images.py:23`).
- Prompt sources from text: `pick_image_moments(transcript, ...) -> list[ImageMoment]`
  (`render/hooks.py:261`, only `image_prompt` matters for a carousel) and `plan_visuals`
  (`agents/visual.py:130`, per-beat 9:16 prompts).
- Text/LLM: `AIConfig(model=os.getenv("REEL_AF_MODEL", ...))` (`app.py:71`); standalone
  `_request_text` (`hooks.py:339`) with `DEFAULT_TEXT_MODEL` (`hooks.py:43`).
- Presets: `load_preset(name)` reads `render/config/presets.json` (`render/presets.py:31`). Existing
  presets carry `canvas_w/canvas_h`, `overlay`, `reel_seconds`, `image_count`, etc. The composite
  path **rejects any preset whose `overlay` is not `middle_third`/`lower_third`** (`app.py:632`).
- Output dir: `Path.cwd()/output/<source>-<run_id>/`; frames named `frame-{idx:02d}.jpg`
  (`images.py:103`).
- **No raw-text-document input path exists today** — inputs are URL or topic only.

### 2.2 reel-af web UI (`web/`)
- **Flask** single-page, config-driven, vanilla-JS app (not FastAPI). `create_app`
  (`web/server.py:320`); one static page `web/index.html` with an in-file JSON `#config`
  (`index.html:12-64`) and one IIFE (`index.html:384-654`). Film "cutting room" dark house style,
  CSS tokens at `index.html:66-296`.
- Flow today: mode = **DROP FILE / FROM URL** + preset select + **ROLL**
  (`index.html:325-328,515`); submit `POST /api/v1/execute/async/{target}` with `Idempotency-Key`
  and `{input}` from `buildInput()` (`index.html:503,562`); poll
  `GET /api/v1/executions/{id}` every 2.5s (`index.html:586`); `finish()` shows a result path +
  download link (`index.html:613`).
- Jobs: `ReelJobStatus = queued|producing|succeeded|failed|cancelled` (`web/reel_jobs.py:18`);
  `_handle_submit`/`_handle_poll` (`web/server.py:148,195`); routes mounted on `_api_router`
  (`server.py:212`).
- **Auth/tenancy backbone (must respect).** `deps.identity.resolve(request)` yields a
  server-trusted `AuthContext(user_id, org_id, role, ...)` (`web/deps.py:97`), **never** from the
  request body — `FORBIDDEN_IDENTITY_FIELDS` are rejected (`reel_jobs.py:32`).
  `RoleAccessGuard.authorize_create` / `authorize_reel_read` (`deps.py:173,177`) enforce org scope;
  cross-org rows appear as 404. SuperTokens emailpassword+session; sign-up gated by
  `REEL_ALLOWED_EMAILS`/`REEL_OWNER_EMAILS` (`server.py:279-290`).
- **Provenance already pre-plumbed but unused.** `reel_job.source_research_run_id` column + a
  `research_run` table exist (`web/pg.py:40-46`), `ReelSubmission.source_research_run_id` exists
  (`reel_jobs.py:54`), and `TARGET_ARTICLE = "reel-af.reel_article_to_reel"` is defined but **not**
  in `ALLOWLISTED_TARGETS` (`reel_jobs.py:23,26`). Today `source_research_run_id` is always `None`.
- **Media serving is NOT wired.** `server.py` has no file/image/download route; `_resolve_result_ref`
  (`server.py:89`) returns a fetchable URL only if the control-plane result carries
  `download_url`/`url`/`object_uri`, else a non-fetchable `cp-execution://...` placeholder
  (`server.py:102`). Local uploads cannot presign without `REEL_BUCKET_*` (`uploads.py:76-83`).

### 2.3 deep-research node (`silmari-af-deep-research`)
- Reasoner `execute_deep_research(query, mode, research_focus, research_scope, max_research_loops,
  num_parallel_streams, ..., model, api_key) -> DocumentResponse` (`main.py:3038`); node id
  `meta_deep_research` (`main.py:63`).
- Returns structured `research_package` (`UniversalResearchPackage`, `main.py:270-298`) with a
  generated `document.sections` + `source_notes`; the DR UI renders it to markdown + HTML.
- **One-click defaults** live in `ui/defaults.json` (`research_focus=3, research_scope=3,
  max_research_loops=3, num_parallel_streams=2, analysis_depth="ANALYTICAL_BRIEF",
  source_strictness="mixed", tension_lens="balanced", mode="general"`); only `query` is required.
- DR UI is Flask (`ui/app.py:292`); routes `POST /api/run`, `GET /api/result?run=<id>` returns
  `{markdown, html, sources}` (`app.py:228-271,356,413`).
- Cross-node calls: `POST /api/v1/execute/async/<node_id>.<reasoner>` with `{input}` → run/execution
  id; poll `GET /api/v1/executions/{id}`; SSE progress at
  `GET /api/ui/v1/workflows/{id}/notes/events`. reel-af's client: `control_plane.py:33-37`
  (`dispatch_async`, `get_execution`).
- **Cross-node identity is identity-free**; each UI is the tenancy boundary that stamps ownership and
  strips identity from the control-plane body. A shared SuperTokens session across DR and reels
  requires a **common parent domain** (`ui/app.py:88-92`); otherwise ownership is per-service and the
  receiving UI must **re-stamp**.

## 3. Goals / Non-Goals

### Goals
- G1 — Produce an ordered image carousel from research/text, reusing `generate_first_frame`.
- G2 — A `reel-af` "Create from research" workflow with Automatic + Full-control modes.
- G3 — A deep-research "Send to reels" push button (research-only DR preserved).
- G4 — A carousel review UI with per-image recreate-with-note on a higher-quality model.
- G5 — Video and carousel are independently selectable (multi-select) from one research input.
- G6 — All new surfaces respect the existing auth/ownership/idempotency backbone.

### Non-Goals (this PRD)
- NG1 — No research logic added to reel-af; DR remains the sole researcher.
- NG2 — No new video pipeline changes beyond accepting a text-doc input.
- NG3 — No auto-publishing to social platforms (carousel export only).
- NG4 — No change to the SuperTokens/tenancy schema beyond using reserved columns.
- NG5 — No arbitrary aspect-ratio editor UI; aspect is a preset value.
- NG6 — No batch/bulk multi-carousel management screen.

## 4. Prerequisites (blocking — must land first or in parallel)

- **P0 — Media serving for generated images (HARD BLOCKER).** The carousel viewer and recreate loop
  require the browser to fetch generated images and receive replacements. Today no route serves media
  and results are placeholder strings (`server.py:89-102`). Provide either object storage
  (`REEL_BUCKET_*`, presigned URLs) **or** an authed, org-scoped image route
  (`GET /api/v1/carousels/{id}/slides/{idx}` streaming from the run dir). Recommended: object storage
  to match the existing upload design.
- **P1 — Per-request image-model tier.** `generate_first_frame` must accept an explicit `model=`
  (default `REEL_AF_IMAGE_MODEL`; HQ recreate uses `REEL_AF_IMAGE_MODEL_HQ`) instead of only the
  module-level constant (`images.py:23`).
- **P2 — Text-document input seam.** A backend path that builds an `Essence` from provided text,
  bypassing the URL `_fetch` and reusing the `extract` distillation (`agents/extract.py`,
  `agents/compose.py`), so a research doc is a first-class input.
- **P3 — Carousel preset + dispatch.** A `carousel` preset (aspect, slide count, style) and a
  carousel dispatch that is **not** gated by the composite overlay guard (`app.py:632`).

## 5. Users & Primary Flows

### Flow A — Automatic (Create from research, one shot)
1. In `reel-af` UI, user selects **Create from research → Automatic**, enters a research **query**,
   clicks one button.
2. `reel-af` calls `meta_deep_research.execute_deep_research` async with `ui/defaults.json` defaults
   (only `query`).
3. On research completion, `reel-af` **automatically** creates **both a video and a carousel** from
   the returned document (OD-3 resolved: Automatic produces both).
4. Progress is shown across **both** stages (researching → creating); terminal state shows results.

### Flow B — Full control (Create from research)
1. User selects **Create from research → Full control**, enters a **query**, clicks one button.
2. `reel-af` runs deep-research (defaults) and, on completion, **displays the full research
   document** in the UI.
3. The rendered research is available in an **editable textarea** (user can copy/paste/edit the
   text that will drive creation).
4. User makes a **video | carousel multi-select** (one or both).
5. When ≥1 output type is selected, the **Create** button becomes active.
6. On **Create**, `reel-af` sends the (edited) research text to the create process for the selected
   output(s), stamping `source_research_run_id` for provenance.

### Flow C — Carousel review + recreate
1. After a carousel is created, the UI shows a **vertical scrollable area** with each image **in
   order**.
2. Any single image can be **sent to recreate with a note**; the model receives the **original
   prompt + the note**.
3. Recreate uses a **more expensive/higher-quality image model** to increase output quality.
4. The recreated image **returns to the existing UI** and replaces the image in place.
5. The scrollable area shows **Cancel** and **Create** buttons in the **top-right**; **Create**
   finalizes the approved carousel, **Cancel** discards it.

### Flow D — Push (deep-research "Send to reels")
1. In the DR UI result view, user clicks **Send to reels**.
2. DR hands the finished research to `reel-af` **by reference** (shared `research_run` row →
   `reel_job.source_research_run_id`), opening/deep-linking the `reel-af` Create-from-research screen
   pre-loaded with that research.
3. If the SuperTokens session is not shared across domains, `reel-af` authenticates the user and
   **re-stamps** ownership before creation.

## 6. Handoff & API Contracts

### 6.1 Cross-node research call (Pull)
- reel-af → `POST /api/v1/execute/async/meta_deep_research.execute_deep_research` with
  `{"input": {"query": "<q>", ...defaults}}`; poll `GET /api/v1/executions/{id}`; optionally consume
  SSE notes for staged progress. Reuse `web/control_plane.py` `dispatch_async`/`get_execution`.

### 6.2 Research provenance (both directions)
- A completed research run is recorded as a `research_run` row (shared `deepresearch` Postgres,
  `pg.py:40`). Creation stamps `reel_job.source_research_run_id` (reserved column,
  `reel_jobs.py:54`, `pg.py:43`) — flip it from always-`None` to the real id.

### 6.3 Create-from-text reasoners (new)
- `research_to_reel(text, ...)` / `text_to_reel` — video from a text document (reuses P2 seam →
  `compose_script` → existing `_render_downstream`).
- `research_to_carousel(text, preset="carousel-default", slide_count=N, model=None)` — returns an
  ordered slide manifest `{run_id, slides: [{idx, image_prompt, image_ref, status}]}`.

### 6.4 UI routes (new, all authed via `_api_router` + `identity.resolve` + access guard)
- `POST /api/v1/research/run {query, mode}` → dispatches DR, returns `{research_run_id, execution_id}`.
- `GET  /api/v1/research/{execution_id}` → `{status, markdown, html, sources}` (proxied/reconciled).
- `POST /api/v1/carousels {source_text, source_research_run_id?, preset}` → creates carousel job.
- `GET  /api/v1/carousels/{id}` → `{status, slides:[{idx, image_ref, prompt, status}]}`.
- `POST /api/v1/carousels/{id}/slides/{idx}/recreate {note}` → HQ regen; returns replaced slide.
- `POST /api/v1/carousels/{id}/finalize` (Create) / `POST /api/v1/carousels/{id}/cancel` (Cancel).
- `GET  /api/v1/carousels/{id}/slides/{idx}` (or presigned URL) → serves the image (P0).

### 6.5 Allowlist / config
- Add carousel + article/text targets to `ALLOWLISTED_TARGETS` (`reel_jobs.py:26`); add a `carousel`
  `kind` to `#config presets[]` (`index.html:23`).

## 7. Acceptance Criteria (ISC — atomic, binary-testable)

### Backend — carousel pipeline
- [ ] ISC-1: `generate_first_frame` accepts an explicit `model=` argument.
- [ ] ISC-2: `generate_first_frame` defaults `model` to `REEL_AF_IMAGE_MODEL` when unset.
- [ ] ISC-3: A `research_to_carousel` reasoner exists and is `@reel.reasoner()`-registered.
- [ ] ISC-4: `research_to_carousel` returns an ordered list of slides indexed 0..N-1.
- [ ] ISC-5: Each returned slide carries its generating `image_prompt`.
- [ ] ISC-6: Each returned slide carries an `image_ref` resolvable to a fetchable image.
- [ ] ISC-7: Slide count equals the preset/`slide_count` input.
- [ ] ISC-8: Carousel dispatch is not blocked by the composite `overlay` guard.
- [ ] ISC-9: A `carousel-default` preset exists in `presets.json`.
- [ ] ISC-10: The `carousel-default` preset outputs 4:5 portrait at 1080×1350 (per OD-1).
- [ ] ISC-11: A per-slide failure marks that slide `failed` without aborting the batch.
- [ ] ISC-12: A failed slide is individually retryable via the recreate path.

### Backend — text-document input seam
- [ ] ISC-13: A text document can be submitted as creation input without a URL.
- [ ] ISC-14: Text input builds an `Essence` without calling the URL `_fetch`.
- [ ] ISC-15: Over-long research text is chunked/summarized before prompt generation.
- [ ] ISC-16: Empty/whitespace-only text input is rejected with a clear error.

### Backend — recreate loop
- [ ] ISC-17: A recreate request accepts a free-text `note`.
- [ ] ISC-18: Recreate composes the model input as original prompt **plus** the note.
- [ ] ISC-19: Recreate uses the higher-quality model (`REEL_AF_IMAGE_MODEL_HQ`).
- [ ] ISC-20: A recreate replaces exactly one slide at its index, preserving order.
- [ ] ISC-21: Recreate returns the new image reference for in-place UI update.
- [ ] ISC-A1 (anti): Recreate never regenerates or reorders sibling slides.

### Handoff / provenance
- [ ] ISC-22: reel-af dispatches `execute_deep_research` async with only-`query` + defaults.
- [ ] ISC-23: reel-af polls and surfaces research status until terminal.
- [ ] ISC-24: A completed research run is persisted as a `research_run` row.
- [ ] ISC-25: A carousel/video created from research stamps `source_research_run_id`.
- [ ] ISC-26: DR exposes a single "Send to reels" control on the result view.
- [ ] ISC-27: "Send to reels" hands off by `research_run` reference, not a giant query string.
- [ ] ISC-A2 (anti): No research/generation logic is added to the deep-research node beyond the button.

### UI — Create-from-research
- [ ] ISC-28: A "Create from research" mode is selectable alongside DROP FILE / FROM URL.
- [ ] ISC-29: Automatic mode exposes a query box and a single create action.
- [ ] ISC-30: Automatic mode auto-creates both a video and a carousel when research completes.
- [ ] ISC-31: Full-control mode displays the returned research document.
- [ ] ISC-32: Full-control mode shows the research in an editable textarea.
- [ ] ISC-33: A video|carousel multi-select is present in full-control mode.
- [ ] ISC-34: The Create button is disabled until ≥1 output type is selected.
- [ ] ISC-35: Create sends the current (edited) textarea text as the creation input.
- [ ] ISC-36: Both stages (researching, creating) show distinct progress states.

### UI — carousel review + recreate
- [ ] ISC-37: A vertical scrollable area lists carousel images in generation order.
- [ ] ISC-38: Each image exposes a recreate control accepting a note.
- [ ] ISC-39: Submitting a recreate shows an in-progress state on that image.
- [ ] ISC-40: A recreated image replaces the original in place on completion.
- [ ] ISC-41: A Cancel button is present in the top-right of the scroll area.
- [ ] ISC-42: A Create button is present in the top-right of the scroll area.
- [ ] ISC-43: Create finalizes the approved carousel to a terminal succeeded state.
- [ ] ISC-44: Cancel discards the carousel and its draft images.
- [ ] ISC-45: New UI reuses existing film-house CSS tokens/components.

### Media serving (P0)
- [ ] ISC-46: Generated carousel images are fetchable via presigned object-storage URLs (per OD-2).
- [ ] ISC-47: Image references are org-scoped; cross-org fetch is denied (404).
- [ ] ISC-48: A missing/expired image reference fails closed with a clear error.

### Auth / tenancy / safety
- [ ] ISC-49: Every new route calls `identity.resolve` and an access-guard check.
- [ ] ISC-50: Identity fields in a request body are rejected on all new routes.
- [ ] ISC-51: Carousel and recreate routes verify the carousel's `org_id` matches the caller.
- [ ] ISC-52: Carousel create honors the existing `Idempotency-Key` pattern.
- [ ] ISC-53: An HQ recreate shows a premium-model confirm before running (per OD-5).
- [ ] ISC-54: A configurable per-carousel HQ-regeneration cap bounds recreate spend (per OD-5).
- [ ] ISC-A3 (anti): reel-af node calls to the control plane carry no Cookie/Authorization identity.

## 8. Open Decisions

**Resolved 2026-07-11:**
- **OD-1 — Carousel aspect ratio → RESOLVED: 4:5 Instagram portrait (1080×1350)** as the
  `carousel-default`. Requires a new crop target in `generate_first_frame` (today crops 9:16
  720×1280). 9:16 and 1:1 may exist as alternate presets later.
- **OD-2 — Media serving → RESOLVED: object storage + presigned URLs** (`REEL_BUCKET_*`, matching the
  existing upload design). No filesystem streaming route; images survive container restarts.
- **OD-3 — Automatic-mode default output → RESOLVED: both video + carousel** on one click. Automatic
  runs research once, then creates a video **and** a carousel from the returned document.

**Resolved 2026-07-11 (cont.):**
- **OD-5 — Recreate cost guard → RESOLVED: soft confirm + configurable cap.** Before an HQ recreate,
  the UI shows a "this uses a premium model" confirm; a configurable per-carousel HQ-regeneration cap
  bounds spend. (See ISC-53.)

**Deferred (owned elsewhere):**
- **OD-4 — Cross-domain session.** Being resolved in a separate work-stream. This plan builds the
  reel-af receiving side to **re-stamp ownership** on the receiving UI (the safe default that works
  under both shared- and separate-domain outcomes); wire-up of a shared parent-domain cookie, if
  chosen, is out of scope here.

## 9. Rollout / Sequencing

1. **P1 + P2** (image-model param, text→Essence seam) — smallest, unblock backend.
2. **P0 media serving** — parallel infra track (hard prerequisite for UI review).
3. **Carousel pipeline** (`research_to_carousel` + `carousel-default` preset).
4. **Create-from-research UI** (Automatic + Full-control) + research proxy routes + provenance.
5. **Carousel review UI** + recreate loop (HQ model) + finalize/cancel.
6. **DR "Send to reels"** push button + by-reference handoff (after OD-4 resolved).

## 11. TDD Plan Coverage Tracker

The 56 criteria are decomposed into **6 TDD plans** (`/create_tdd_plan` output). Every ISC is
assigned to exactly one plan. Status: `pending` → `writing` → `done`. Plans live beside this PRD in
`thoughts/searchable/shared/plans/`.

| # | Plan (epic) | ISCs covered | Plan file | Status |
|---|---|---|---|---|
| 1 | Carousel generation pipeline (backend core) | ISC 1–16 | `2026-07-11-tdd-01-carousel-pipeline-backend.md` | **done** (12 behaviors, all LEAF) |
| 2 | Recreate loop + cost guard (backend) | ISC 17–21, A1, 53, 54 | `2026-07-11-tdd-02-recreate-loop-cost-guard.md` | **done** (6 behaviors, all LEAF) |
| 3 | Media serving — StoragePort + object storage (P0) | ISC 46–48 | `2026-07-11-tdd-03-media-serving-storageport.md` | **done** (5 behaviors, 1 BLOCKING) |
| 4 | Cross-node handoff + research provenance | ISC 22–27, A2 | `2026-07-11-tdd-04-research-handoff-provenance.md` | **done** (5 behaviors; 2 BLOCKING closures) |
| 5 | Create-from-research workflow (routes + UI) | ISC 28–36 | `2026-07-11-tdd-05-create-from-research.md` | **done** (3 behaviors; 2 automated, 7 manual/E2E) |
| 6 | Carousel review UI + authed carousel routes | ISC 37–45, 49–52, A3 | `2026-07-11-tdd-06-carousel-review-and-routes.md` | **done** (8 behaviors; 2 BLOCKING closures) |

**Seam ownership (avoid cross-plan drift):**
- Plan 1 **defines** the carousel pipeline (`research_to_carousel`), the text→`Essence` seam, the
  `carousel-default` preset, and `generate_first_frame(model=...)`. Plans 2/6 **depend on and
  reference** these — they do not redefine them.
- Plan 3 **defines** the `StoragePort` abstraction + presigned-URL adapter. Plans 1/6 reference it
  (a fake `StoragePort` in their unit tests; real adapter is Plan 3).
- Plan 6 **defines** the authed carousel routes + `CarouselRepoPort`. Plan 5 references the research
  routes it needs; Plan 4 owns research provenance stamping.

**Loop progress log:**
- 2026-07-11: tracker created; 6 plans scoped; Wave A (Plans 1, 3) dispatched.
- 2026-07-11: Plan 3 **done**. New seam surfaced: media route needs a slide→ref+org lookup, so
  Plan 3 introduced a minimal `SlideRefResolverPort` (fake here; **real impl owned by Plan 6**);
  `default_deps().slides` is a fail-closed placeholder until Plan 6 wires it. Plan 6 prompt must
  implement `SlideRefResolverPort`.
- 2026-07-11: Plan 1 **done**. Owns `research_to_carousel`, text→Essence, `carousel-default`,
  `generate_first_frame(model=, crop=)`, `_crop_to_4x5`, and a `regenerate_slide` retry primitive
  (Plan 2 builds the HQ/note recreate on top). Wave B (Plans 2, 4, 5, 6) dispatched.
- 2026-07-11: Plan 2 **done** (new module `src/reel_af/recreate.py`; owns `recreate_slide`,
  `compose_recreate_prompt`, `resolve_hq_model`, `HqRecreateGuard`/`HQ_RECREATE_CAP`). **Reconcile
  flag:** Plan 2 enforces only an in-memory cap; the **persisted cross-request cap** (repo-backed
  `HqRecreateGuard`) is deferred to Plan 6's `CarouselRepoPort`. Verify Plan 6 covers it; else file a
  follow-up bead.
- 2026-07-11: Plan 5 **done** (`POST /api/v1/research/create` fan-out; targets
  `reel-af.reel_research_to_reel` + `reel-af.reel_research_to_carousel` added to ALLOWLISTED_TARGETS;
  2 automated + 7 manual/E2E).
- 2026-07-11: Plan 6 **done** (implements the real `SlideRefResolverPort`, closing Plan 3's
  placeholder). **Cross-plan reconcile flags (resolve at implementation):** (a) Plan 6 adds
  `StoragePort.delete` (+fake) for cancel — Plan 3 should reabsorb it into the StoragePort spec;
  (b) slide-manifest persistence-on-completion is shared wiring with the Plan 4/5 poll path — confirm
  the slide-write and `source_research_run_id` stamp land in the same step; (c) the **persisted**
  HQ-recreate cap (Plan 2 reconcile flag) belongs in Plan 6's `CarouselRepoPort` — confirm coverage
  or file a follow-up bead.
- 2026-07-11: Plan 4 **done**. **ALL 6 TDD PLANS COMPLETE — loop terminated (tracker fully green).**
  Coverage audit: every ISC-1..54 + A1/A2/A3 appears in ≥1 plan (5,221 lines total). Plan 4's B4
  closure exposed a real latent bug to fix during implementation: `insert_or_get_queued` binds
  `source_research_run_id` (`web/pg.py:203-215`) but `get_by_execution` never SELECTs it
  (`web/pg.py:256-268`) — red-at-seam proven. Recommended implementation order (seam dependencies):
  **Plan 1 → Plan 3 → Plan 2 → Plan 4 → Plan 6 → Plan 5.** Cross-plan reconcile flags (a/b/c above)
  to resolve at implementation time.

## 12. Plan Enrichment Pass (System Map + Observability)

Second loop: each TDD plan gets a **System Map** section (`/system_map` — bounded-context boundary
diagram + EBNF grammar + seam table + index; stable IN#/OUT#/EV#/C#/S# IDs; gaps marked) and an
**Observability** section (`/observability-fundamentals` — wide-event/OTel spans with high-cardinality
who/what/where attributes per operation). Status: `pending` → `done`.

| # | Plan file | System Map | Observability | Status |
|---|---|---|---|---|
| 1 | `2026-07-11-tdd-01-carousel-pipeline-backend.md` | ✓ 3 contexts, 5 seams | ✓ 5 spans | **done** |
| 2 | `2026-07-11-tdd-02-recreate-loop-cost-guard.md` | ✓ 4 contexts, 3 seams | ✓ 5 spans | **done** |
| 3 | `2026-07-11-tdd-03-media-serving-storageport.md` | ✓ 3 contexts, 6 seams | ✓ 3 spans | **done** |
| 4 | `2026-07-11-tdd-04-research-handoff-provenance.md` | ✓ 2 contexts, 3 seams | ✓ 5 spans | **done** |
| 5 | `2026-07-11-tdd-05-create-from-research.md` | ✓ 4 contexts, 4 seams | ✓ 2 span types | **done** |
| 6 | `2026-07-11-tdd-06-carousel-review-and-routes.md` | ✓ 6 contexts, 7 seams | ✓ 5 spans | **done** |

**Enrichment progress log:**
- 2026-07-11: enrichment tracker created; system-map + observability sections dispatched for all 6 plans.
- 2026-07-11: **ALL 6 PLANS ENRICHED — loop complete.** Each plan now carries a `## System Map`
  (bounded contexts + seams + EBNF + seam table + INDEX, gaps `class gap`-marked) and a
  `## Observability` (wide-event/OTel spans with high-cardinality who/what/where + 3am queries).
  Orphan-check PASS on all six (diagram IDs ↔ EBNF 1:1). **Recurring gaps the maps independently
  surfaced** (implementation must resolve): G-a `_fetch` reach-around in the text-distillation
  context (Plan 1); G-b `get_by_execution` never SELECTs `source_research_run_id` — provenance reads
  silently null (Plan 4); G-c `StoragePort.delete` used by Plan 6 cancel but absent from Plan 3's
  spec; G-d persisted HQ-recreate cap (Plan 2 in-memory only) deferred to Plan 6; G-e vanilla-JS UI
  boundary untestable (no JS harness) in Plans 5/6.

## 13. Review + Enhance Pass (one plan at a time)

Third loop: each TDD plan is reviewed with `/review_plan` (pre-implementation architectural review —
Contracts/Interfaces/Promises/Data-Models/APIs/Workflow-Closure → `<plan>-REVIEW.md`), then **enhanced
in a fresh-context subagent** using that review. Strictly sequential. Status: `pending` →
`reviewed` → `done`.

| # | Plan file | Review (`-REVIEW.md`) | Enhance | Status |
|---|---|---|---|---|
| 1 | `2026-07-11-tdd-01-carousel-pipeline-backend.md` | ✓ (3 crit, Minor Rev) | ✓ +2 behaviors (8b/9c), closure fixed | **done** |
| 2 | `2026-07-11-tdd-02-recreate-loop-cost-guard.md` | ✓ (2 crit, Minor Rev) | ✓ +Behavior 2b, register-after-success, +7 tests | **done** |
| 3 | `2026-07-11-tdd-03-media-serving-storageport.md` | ✓ (0 crit/7 warn, Ready) | ✓ all 7 warnings applied | **done** |
| 4 | `2026-07-11-tdd-04-research-handoff-provenance.md` | ✓ (3 crit/11 warn, Approved w/ changes) | ✓ readiness gate, conftest spec, row-first ordering | **done** |
| 5 | `2026-07-11-tdd-05-create-from-research.md` | ✓ (3 crit/9 warn, Approve w/ amend) | ✓ UUID+_CP_STRIP+tenancy, wire-key reconciled, partial-failure contract | **done** |
| 6 | `2026-07-11-tdd-06-carousel-review-and-routes.md` | ✓ (5 crit/~11 warn, Approve w/ amend) | ✓ 5 crit fixed, ownership resolved, +7 tests | **done** |

**Review+enhance progress log:**
- 2026-07-11: tracker created; sequential review→enhance started with Plan 1 (review dispatched).
- 2026-07-11: Plan 1 **done**. Review: 3 critical (Minor Revision) — reasoner deps arrive `None` under
  JSON-`input` invocation + missing `OPENROUTER_API_KEY` gate; no concrete `prompt_planner`; wrong
  registration cite. Enhance fixed all 3 (+ Behavior 8b dep/gate test, Behavior 9c
  `plan_carousel_prompts`, real `app.reel.reasoners` registry assertion), corrected the all-LEAF
  closure claim. Plan 2 review dispatched.
- 2026-07-11: Plan 2 **done**. Review: 2 critical — unowned dep-resolution/gate (recreate crashes on
  `None.generate_image`) + cap-charged-before-generation mis-billing. Enhance added Behavior 2b
  (`RecreateDepsUnresolvedError` fail-clean), chose **register-after-success** cap accounting, aligned
  status enum to Plan 1's `"ok"|"failed"[,error]`, +7 tests. Plan 3 review dispatched.
- 2026-07-11: Plan 3 **done**. Review: 0 critical / 7 warnings (Ready). First review run stopped
  mid-research without writing the file; resumed the agent to finish it. Enhance applied all 7
  (StoragePort.delete seam note, put last-write-wins, redirect double-status fix, org-prefixed ref,
  concealment test-now/prod-at-Plan-6); BLOCKING image-route closure judged sound, untouched. Plan 4
  review dispatched.
- 2026-07-11: Plan 4 **done**. Review: 3 critical / 11 warnings (Approved w/ changes) — silent
  schema-readiness gap (research_run INSERT would 500), underspecified test harness (missing
  OTHER_ORG/OTHER_USER + fake-repo provenance store), dispatch-before-record orphan risk. Enhance
  fixed all 3 (readiness-gate extension, full conftest spec, **row-first** ordering + terminal
  monotonicity); GAP1 SELECT-omission closure kept intact. **Follow-up:** orphan-reaper downscoped to
  a bead (row-first already closes the window). Plan 5 review dispatched.
- 2026-07-11: Plan 5 **done**. Review: 3 critical / 9 warnings (Approve w/ amend, Data Models ❌) —
  `source_research_run_id` not UUID-coerced / not in `_CP_STRIP` (leaks to reasoner) / not
  tenancy-checked, and a Plan 4↔5 wire-key mismatch. Enhance fixed all 3 + **canonical cross-plan
  wire-key: `research_run_id` on the API wire → `source_research_run_id` DB column** (Plans 4 & 6 must
  agree); +partial-failure `{"jobs":[...]}` contract (502 only when zero enqueued), +distinct-job_id
  and no-provenance-leak tests. Plan 6 review dispatched (convergence: SlideRefResolverPort impl,
  StoragePort.delete, CarouselRepoPort persisted cap, recreate-route dep/gate ownership, wire-key).
- 2026-07-11: Plan 6 **done** — **REVIEW+ENHANCE LOOP COMPLETE (6/6).** Review: 5 critical /
  ~11 warnings (Approve w/ amend). Enhance resolved all 5 and **ended the circular delegations** with
  explicit ownership: persisted atomic HQ cap = Plan 6 (`register_hq_recreate` + SQL + cross-request
  closure test); real `ObjectStorage.delete` = Plan 6 (extends Plan 3); recreate None-dep resolution
  + `OPENROUTER_API_KEY` gate = Plan 6; wire-key = `research_run_id` (UUID-coerce/400,
  tenancy-check/404, both keys in `_CP_STRIP`); +7 tests. Consistency check: all 6 REVIEW files +
  changelogs present; wire-key consistent across Plans 4/5/6. **~16 criticals + ~50 warnings found
  and fixed across the 6 plans.** Only outstanding follow-up: orphan-reaper (Plan 4, row-first
  already closes the window) → bead. Loop terminated.

## 10. References

- reel-af reasoners/render: `src/reel_af/app.py:87,385,463,613,680,725`;
  `render/images.py:23,60,89`; `render/hooks.py:43,261,339`; `agents/visual.py:130`;
  `agents/extract.py`; `agents/compose.py`; `render/presets.py:31`;
  `render/config/presets.json`.
- reel-af web: `web/server.py:89,102,148,195,212,320`; `web/index.html:12-64,325-328,503,586,613`;
  `web/deps.py:97,173,177`; `web/reel_jobs.py:18,23,26,32,54`; `web/pg.py:40-46`;
  `web/control_plane.py:33-37`; `web/uploads.py:76-83`.
- deep-research: `main.py:63,270-298,3038`; `ui/app.py:88-92,228-271,292,356,413`;
  `ui/defaults.json`; `launch_adapter.py:68-73`.
- Related: `deploy/RAILWAY-RUNBOOK.md` §7; `thoughts/searchable/shared/plans/2026-07-10-tdd-video-ingest-youtube-vimeo.md`.
