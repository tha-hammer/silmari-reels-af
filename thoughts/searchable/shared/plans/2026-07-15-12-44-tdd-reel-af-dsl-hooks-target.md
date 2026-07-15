---
date: 2026-07-15T12:44:00-04:00
planner: CopperFinch (Claude Opus 4.8)
repository: silmari-reels-af
branch: dsl-hooks-target
base_commit: f72adb4
status: reworked-after-review
revision: 2 (applies BlueBay review 2026-07-15T13:05, verdict needs-rework)
slice: "SLICE A — reel-af DSL-hooks render + web target"
grounding_research: /home/maceo/Dev/A1_workspace-blueprint/thoughts/searchable/shared/research/2026-07-15-09-03-a1-reel-af-video-pipeline-seams.md
tags: [tdd, plan, dsl, hooks, reel-af, footage-reel, delivery, web-target]
---

# reel-af DSL-Hooks Target — TDD Implementation Plan (Slice A)

## Overview

Slice A wires reel-af's **already-existing** DSL render primitives into a
web-dispatchable AgentField target, `reel-af.reel_dsl_hooks_to_reels`, that
consumes A1-produced artifacts (`composite.ts.md`, `transcript.words.json`,
`hook-plan.json`) and delivers a browser-downloadable vertical reel — or fails
closed with typed diagnostics.

This is a **wiring + fail-closed slice, not a renderer slice** — and revision 2
enforces that framing rather than merely asserting it. The parser, resolver,
aligner, compiler, `FootageReel` model, segment downloader, stitcher, finisher,
and overlay library all exist on `f72adb4` and are verified below with
`file:line`. What does not exist is: a `CompileContext`, a delivery-required
policy, a workflow-scoped marker-rejection policy, a surviving record of
malformed markers, a cut-in consumer, and a web target.

**Net effect of Slice A**: a dispatched DSL-hooks job renders **fully** —
real-footage stitch + finish captions + hook banner — and delivers a browser
URL or fails closed. A1 zoom/visual cut-ins are **mapped and fail-closed
validated (B9a)** but **not yet rendered as overlays**; the overlay render stage
is deferred to a follow-up build (see What We're NOT Doing → *Deferred: B9b*).
Two of revision 1's behaviors were cut on operator decision after review: the
cut-in **render** stage and the durable orphan **table**. Both rested on premises
the code contradicts (BLOCKING-1, BLOCKING-3).

**Scope is reel-af only.** A1 artifacts are treated as **input fixtures produced
elsewhere**; this plan builds reel-af's *consumer* of them.

Every behavior below is derived from the research's "Required Tests" section
(research lines 511-548) and the "Worker contract" steps 1-11 (research lines
255-272).

**17 behaviors**: B1-B8, **B9a**, B10-B14, **B15a**, B16, B17. (B9b and B15b are
deferred, not renumbered — their absence is deliberate and traceable.)

### Revision Log (rev 2 — applies BlueBay review in full)

| Finding | Resolution |
|---|---|
| BLOCKING-1 | B9 **split**. B9a (pure mapper) stays; **B9b (overlay render) deferred** to a follow-up build. D7/R1 rewritten — the time-base claim was backwards. |
| BLOCKING-2 | B2 retargeted to `composite.py` + `models.py`; `read_composite` swallows `MarkerError`. Parametrize narrowed. Latent silent-failure defect named as B2's motivation. |
| BLOCKING-3 | B15 **split**. B15a (log `target` + guard `_dispatch_one`) stays; **B15b (durable table + repair) deferred** — outage paradox + no migrations in this repo. |
| BLOCKING-4 | B3 threads `context`; `context is None → return False`; workflow-scoped `DSL_HOOKS_UNSUPPORTED_VERBS`; B3→B4 dependency named. |
| BLOCKING-5 | B17 classified **BLOCKING**; `REAL_WORKER_RESULT` defined as a live invocation; `_require_ffmpeg()` added; regeneration command added; false precedent dropped. |
| BLOCKING-6 | B14 adds `_poll_response_body` (`server.py:260-270`); strips `result`; accepts a locally-derived error. |
| SHOULD-FIX-1..6 | Tautological compat test deleted; line refs corrected; parity parametrized over all non-DSL-hooks targets; `_is_browser_deliverable` delegates to `_is_valid_url`; status-vs-code resolved; `fetch_segment` overstatement fixed. |
| NICE-TO-HAVE | Leaked tool XML stripped; 16→**18** dsl test files; two impossible CodeCleanup constraints removed. |

---

## Decisions (research-vs-code reconciliation)

The grounding research was written against reel-af `afd6025` at a **different
path**. This worktree is `f72adb4`. Six research assumptions do not hold on this
base. Each is recorded here as **research assumed X → actual on f72adb4 is Y →
this plan does Z**, with `file:line`. These are load-bearing: a plan that
inherited the research's assumptions would specify code that cannot compile.

### D1 — `CompileContext` does not exist

- **Research assumed**: `compile_composite(resolved_doc, words, SourceRef(...), CompileContext(...))`
  (research line 262, line 1066).
- **Actual on f72adb4**: `CompileContext` **does not exist anywhere in the repo**
  (repo-wide grep: zero matches). The real signature is
  `src/reel_af/dsl/compile.py:50-56`:
  ```python
  def compile_composite(
      doc: CompositeDoc,
      words: WordsSidecar,
      source: SourceRef,
      *,
      relevant_dir: Path | None = None,
  ) -> CompileResult:
  ```
  `SourceRef` **does** exist (`src/reel_af/dsl/models.py:174-178`:
  `source_url: str`, `source_id: str | None`).
- **This plan does**: create `CompileContext` as a NEW pydantic model and thread
  it as an **optional keyword-only** arg, `context: CompileContext | None = None`.
  Default `None` reproduces **exactly today's behavior**, so all **18**
  `tests/dsl/test_*.py` files stay green with **no signature break and no
  migration note**. The DSL-hooks worker always passes a context. Behavior 4.
- **Safer than rev 1 claimed** (review D1): `compile_composite` has **zero
  production callers**. `src/reel_af/render/footage_stitch.py:17-32` imports from
  `reel_af.dsl.models` **only** — it never imports `compile_composite`. A
  repo-wide grep for `reel_af.dsl` outside `src/reel_af/dsl/` returns exactly that
  one hit. The compat risk is therefore confined entirely to tests.

### D2 — `DSL_MARKER_INVALID` is not a real diagnostic code

- **Research assumed**: a typed `DSL_MARKER_INVALID` failure (research line 517,
  line 1087).
- **Actual on f72adb4**: no such code exists. The real declaration is
  `DiagnosticCode` (`src/reel_af/dsl/models.py:143-158`), which contains
  **`INVALID_MARKER`** (`models.py:151`) — declared but emitted nowhere
  (`grep -rn "INVALID_MARKER"` returns exactly one hit: the declaration). The
  diagnostic model is `Diagnostic` (`models.py:161-168`), not `DslDiagnostic`.
- **Type-kind correction (review D2)**: `DiagnosticCode` is a
  **`Literal[...]` type alias**, *not* an `Enum` (`models.py:143`). There is no
  `DiagnosticCode.INVALID_MARKER` to import. The established codebase idiom is a
  **bare string literal at the emission site**, type-checked against the `Literal`
  by the `Diagnostic` model — cf. `compile.py:143`: `code="UNRESOLVED_HOLE"`.
- **This plan does**: align to the REAL names. Use `INVALID_MARKER` as a bare
  string at the emission site, following the existing idiom. Do **not** invent
  `DSL_MARKER_INVALID`. Do **not** attempt "no literal outside `DiagnosticCode`" —
  that constraint is **not implementable** against a `Literal` and has been removed
  from B2's Refactor checklist. Converting `DiagnosticCode` to a `StrEnum` would
  satisfy it but is a cross-cutting change to 14 codes and every emission site —
  **out of scope**. Behavior 2 proves `INVALID_MARKER` actually fires.

### D3 — `_check_unsupported` is a stub that never rejects anything

- **Research assumed**: "Unsupported target-workflow markers fail with typed
  diagnostics instead of being rendered by another path" (research lines 142-143),
  and a required resolver test proving exactly that (research lines 518-520).
- **Actual on f72adb4**: `src/reel_af/dsl/compile.py:132-133` is a pure stub:
  ```python
  def _check_unsupported(doc: CompositeDoc, diagnostics: list[Diagnostic]) -> bool:
      return False
  ```
  Consequently `UNSUPPORTED_INSERT` and `UNSUPPORTED_FIND` (`models.py:144-145`)
  are **declared but never emitted**. Called at `compile.py:62`, before
  `_check_unresolved`, alignment, and every render effect — so the fail-closed
  *ordering* this plan needs is **already correct**.
- **Reframed (review D3 — rev 1 misread this).** The stub is **not a forgotten
  rejection path**. `[insert relevant]`, `[insert file]`, and `[find relevant]` are
  **supported, implemented, tested features** (via `src/reel_af/dsl/relevant.py`),
  and `tests/dsl/test_compile_unsupported.py` exists to pin exactly that — it
  asserts on green today:
  - `:55` — `[insert relevant 5]` → `assert "UNSUPPORTED_INSERT" not in codes`
  - `:85` — `[insert file rel_01]` → `assert "UNSUPPORTED_INSERT" not in codes`
  - `:123` — `[find relevant 30 x5]` → `assert "UNSUPPORTED_FIND" not in codes`

  `UNSUPPORTED_INSERT`/`UNSUPPORTED_FIND` are **vestigial** — left behind when
  those features landed. `_check_unsupported` returns `False` because **nothing is
  unsupported on the default workflow**. Rev 1's Green would have rejected them
  unconditionally and broken all three tests.
- **This plan does**: add a **workflow-scoped rejection policy** — a genuinely new
  concept, not a stub fill-in. `_check_unsupported(doc, diagnostics, *, context=None)`;
  **`context is None` → `return False`** (byte-for-byte today's behavior, keeping
  those three tests and all 18 dsl test files green). Rejection is gated on the
  DSL-hooks workflow only, via a workflow-keyed constant
  `DSL_HOOKS_UNSUPPORTED_VERBS` — **not** a global set. Behavior 3
  (**depends on B4**).

### D4 — `validate_renderable` exists but is far weaker than specified

- **Research assumed**: renderability validation "stronger than structural schema
  validation: finite spans, `start_s < end_s`, duration math, transition
  adjacency/count, allowed transition primitives, supported black segments, and
  source segment asset resolution" (research lines 159-161, C28 line 1092).
- **Actual on f72adb4**: `validate_renderable` **does** exist
  (`src/reel_af/dsl/models.py:376-405`) but checks only four things: no segments
  (`:382-384`), transition count vs `segments-1` (`:390-396`), `duration_s <= 0`
  (`:398-400`), `duration_s > MAX_REEL_DURATION_S` (`:402-405`). It is duck-typed
  via `getattr`. It is invoked at `compile.py:109-117`.
  The **stronger** checks live in `FootageReel._validate_reel`
  (`models.py:236-284` — adjacency, xfade bounds, derived-duration match), i.e.
  they hold only because pydantic construction already ran.
  Critically **`SourceSegment` does not enforce `start_s < end_s`**
  (`models.py:181-189`: only `start_s: ge=0`, `end_s: gt=0` independently), and
  nothing checks **finite** spans (NaN/inf) or **segment asset resolution**.
- **This plan does**: strengthen `validate_renderable` with the missing
  postconditions — finite spans, `start_s < end_s`, allowed transition
  primitives, supported black segments — and add asset-resolution validation at
  the stitch boundary. Behavior 6.

### D5 — Poll does NOT leak node-local paths; the real gap is different

- **Research/brief assumed**: "a result with only node-local `video_path` yields
  ... a browser URL" that must be suppressed (research lines 299-301).
- **Actual on f72adb4**: `_resolve_result_ref` (`web/server.py:224-238`) already
  refuses to return a raw path — a bare `video_path` is wrapped into an opaque
  `cp-execution://{execution_id}/result/video_path` URI (`:236-237`). So "a
  node-local path does not leak as a browser URL" is **true of `result_ref`** —
  and **false of the poll response body** (see the third gap below). Three real
  gaps:
  1. Such a job is still reconciled as **`succeeded`** (`server.py:697`) with a
     non-browser-deliverable ref — the job *claims success* while undeliverable.
  2. `download_url` and `url` are returned **verbatim with zero scheme
     validation** (`server.py:228-231`), unlike `object_uri`/`uri`/`path` which
     are scheme-gated to `http(s)://`, `s3://`, `gs://` (`:232-235`).
  3. **The poll body leaks the node-local path anyway** (review BLOCKING-6).
     `_poll_response_body` (`web/server.py:260-270`) begins
     `payload = dict(cp_body)` (`:261`) — the CP body is copied **wholesale** into
     the response, so `result: {"video_path": "/tmp/node/out.mp4"}` **is** returned
     to the caller today. Suppressing `result_ref` does **not** suppress the raw
     `result` dict. Separately, `payload["error"]` is populated **only** from
     `_execution_error(cp_body)` (`:266-269`) — i.e. a **CP-reported** error — so a
     locally-derived `delivery_unavailable` cannot be surfaced without extending
     that function.
- **This plan does**: for the DSL-hooks target only, a job reaches a delivered
  outcome **only when** the result ref is a browser-deliverable `http(s)` URL;
  otherwise it is terminal with the error code `delivery_unavailable`, **and the
  whole `result` dict is stripped from the poll body**. Add http(s) validation on
  `download_url`/`url` on the DSL-hooks path by **delegating to the existing
  `_is_valid_url`** (`reel_jobs.py:151-156`) — not a second, weaker `startswith`
  idiom (review SHOULD-FIX-4; `startswith` would accept `"https://"` with no host).
  Scoped per-target via `DELIVERY_REQUIRED_TARGETS` so the other four allowlisted
  targets keep today's fail-soft behavior **unchanged**; the parity test is
  parametrized over **all** of them (review SHOULD-FIX-3). Behavior 14.
- **Status vs error code — settled once (review SHOULD-FIX-5).**
  `delivery_unavailable` is an **error code**, never a status. The DB status
  remains **`failed`**. Reason: `update_from_execution` (`pg.py:358-370`) writes a
  `ReelJobStatus`-typed status guarded by
  `and status not in ('succeeded','failed','cancelled')` — the terminal set is
  **hardcoded in SQL**. A new `delivery_unavailable` *status* would require the
  `ReelJobStatus` literal, that SQL predicate, **and a root-owned schema change** —
  i.e. D6's blocker again. Everywhere this plan says "terminal
  `delivery_unavailable`", read: **status `failed`, error code
  `delivery_unavailable`**.

### D6 — Orphan dispatch is partly handled; two real gaps remain

- **Research assumed**: reel-af "must emit an operator-visible `orphaned_dispatch`
  record" and existing cleanup "is not sufficient" (research lines 453-462).
- **Actual on f72adb4**: `web/server.py:339-347` **already** logs an
  `orphaned_dispatch` line carrying `job_id`, `execution_id`, `org_id`,
  `created_by` (= `ctx.user_id`), `client_request_id`, `err`, then raises
  `RepositoryUnavailable`. Two precision corrections (review D6/SHOULD-FIX-6): it
  is a **`%s`-formatted string, not structured key/value fields** ("structured" was
  generous), and it **omits `target`**. Two genuine gaps:
  1. The record is **ephemeral (log-only), not queryable**. No existing table suits
     it (`web/events.py` is the INT-02 CloudEvents consumer;
     `deepresearch.processed_messages` is keyed to CloudEvent ids).
  2. The fan-out path `_dispatch_one` (**defined `web/server.py:424`**; the
     unguarded attach is the **call at `:443`** — rev 1 conflated the two) calls
     `attach_execution_id` with **no try/except at all**. **Sharper than rev 1
     stated**: every other failure in `_dispatch_one` returns a `_DispatchOutcome`
     (`:434-442`), so an `HttpError` at `:443` **escapes the function entirely**,
     violating its own documented contract (`:425-427`: "Returns a disposition
     instead of raising") and **aborting the whole fan-out** at its call site
     `:468`. It is not merely a silent orphan — it is a contract breach that takes
     down sibling dispatches.
- **This plan does (operator decision after review BLOCKING-3)**: keep the
  **zero-migration half** and defer the table.
  - **B15a (this slice)**: add `target` to the existing log (`server.py:342-346`),
    and wrap `_dispatch_one`'s attach (`:443`) in the same `RepositoryUnavailable`
    handler so the fan-out path stops breaching its contract.
  - **B15b (deferred)**: the durable record + repair path. It rested on two
    premises the code contradicts — the **outage paradox** (the trigger *is*
    Postgres unavailability, so writing a row to that same Postgres is unreachable
    exactly when it matters) and the fact that **this repo owns no migrations at
    all** (`web/pg.py:1-12`: root-owned; `migrations/deepresearch/` lives in the
    monorepo root). A new `FEATURE_SCHEMA` entry (`pg.py:49-68`), verified by
    `_assert_schema` (`:92-107`), would **503 the entire feature surface** until the
    root migration landed — and `.github/workflows/ci.yml` has **no migration
    gate**. See What We're NOT Doing → *Deferred: B15b*.

### D7 — The cut-in consumer fork (two time bases)

- **Research assumed**: A1 zoom/visual cut-ins "map to
  `reel_af.render.overlays.CutInOverlay` metadata", a module "dormant pending a
  consumer wiring" (research lines 165-171, gap G7 line 1202).
- **Actual on f72adb4**: confirmed dormant — **no module under `src/` imports
  `overlays.py`** (repo-wide grep; only `tests/dsl/test_overlays.py` does). Its
  docstring names the awaited consumer precisely
  (`src/reel_af/render/overlays.py:8-9`):
  > "Note: Implemented and tested but currently dormant — awaiting the cut-in
  > consumer (footage_stitch overlay integration) to land before being wired in."

  Crucially, `finish_reel` **already has a different overlay path**: it composes
  via `image_cutins.build_image_overlay_filtergraph`
  (`src/reel_af/render/image_cutins.py:135-176`), wired as `deps.build_overlay_graph`
  at `finish.py:107-108` and invoked at `finish.py:264`. The two are **not** the
  same subsystem:

  | | `overlays.CutInOverlay` | `image_cutins.ImageCutIn` |
  |---|---|---|
  | time base | **ABSOLUTE SOURCE TIME** (corrected) | final-reel-relative |
  | kinds | `zoom` + `visual` | images only (no zoom) |
  | stage | per-segment file in/out | post-stitch, finish stage |
  | consumer | **none (dormant)** | `finish_reel` (live) |

- **Time-base correction (review D7 — rev 1 had this backwards).** Rev 1 claimed
  `CutInOverlay`'s time base is "source-segment-relative". **It is absolute source
  time.** Three independent proofs:
  - `overlays.py:307-316` — the decisive one:
    ```python
    start_s = max(0.0, cut_in.at_s - segment_start_s)
    end_s = min(segment_duration_s, cut_in.until_s - segment_start_s)
    ```
    You only subtract a segment origin from an **absolute** time. *Relative* is the
    computed **output**, not the input.
  - `overlays.py:67` — `normalize_cut_ins` docstring: "sort cut-ins by **absolute
    source time**."
  - `overlays.py:121-124` — `build_overlay_filtergraph` docstring: "**Cut-in windows
    are absolute source times and are clamped to the segment duration.**"

  **Three consequences, all of which reshape B9:**
  1. **The mapper is near-identity on the time fields.** A1 cut-ins are absolute
     source time; `CutInOverlay` is absolute source time. The absolute→relative
     conversion already exists at `overlays.py:307-316` and is applied internally by
     `build_overlay_filtergraph`. B9a is therefore validation + typing, not
     arithmetic.
  2. **Rev 1's R1 was false.** A boundary-spanning cut-in **has** a defined
     representation: `max()/min()` **clamps it to each overlapping segment**, so it
     renders on both. That is the library's designed semantics. Rejecting it would be
     a **policy divergence from the library**, and detecting it would require
     re-deriving `_relative_window`'s arithmetic in the mapper — violating B9a's own
     no-duplication checkbox. **Dropped.**
  3. **The genuinely missing rejection is a different one**: a cut-in outside
     *every* segment is **silently dropped** today, filtered at `overlays.py:137-141`
     via `_relative_window(...) is not None`. **That** is the real `CUTIN_INVALID`
     case worth closing, and it is the only one B9a keeps.
- **This plan does (operator decision after review BLOCKING-1)**: **split B9.**
  - **B9a (this slice)**: the **pure** cut-in → `overlays.CutInOverlay` mapper +
    `CUTIN_INVALID` (`models.py:155`, currently unemitted) for any A1 cut-in falling
    outside **every** source segment's span — research-sanctioned: "or are explicitly
    rejected with a typed diagnostic" (research line 534). Pure, testable now, **no
    render, touches zero lines of `footage_stitch.py`**.
  - **B9b (deferred)**: the per-segment overlay **render** stage. The seam rev 1
    named **does not exist** — `footage_stitch` builds one monolithic
    `filter_complex` for one ffmpeg exec, with no per-segment files to hook. See
    What We're NOT Doing → *Deferred: B9b*.
  - Keep `finish_reel`'s existing `image_cutins` path (LLM-picked image moments)
    **separate**; do not merge the two models.

---

## Current State Analysis

All symbols verified by direct read on `f72adb4`. `git log --oneline -1` →
`f72adb4 Merge pull request #19 from tha-hammer/feat/descriptive-reel-filenames`.

### EXISTS — reuse, do not reinvent

| Symbol | Location | Notes |
|---|---|---|
| `read_composite(text, *, source_path)` | `src/reel_af/dsl/composite.py:65` | → `CompositeDoc`; **swallows `MarkerError`** at `:99-101` + `:121-125` (B2) |
| `read_composite_file(path)` | `src/reel_af/dsl/composite.py:199` | file variant |
| `load_words(path)` | `src/reel_af/dsl/compile.py:614` | → `WordsSidecar` |
| `compile_composite(doc, words, source, *, relevant_dir)` | `src/reel_af/dsl/compile.py:50-56` | **no context param** (D1) |
| `CompileResult{status, plan, diagnostics}` | `src/reel_af/dsl/models.py:297-302` | `status: "ok"\|"warning"\|"error"` |
| `FootageReel` | `src/reel_af/dsl/models.py:226-291` | `schema_version="1"`, `dsl_version="2"` |
| `FootageReel._validate_reel` | `src/reel_af/dsl/models.py:236-284` | adjacency, xfade bounds, duration match |
| `SourceRef{source_url, source_id}` | `src/reel_af/dsl/models.py:174-178` | **exists** |
| `WordsSidecar` | `src/reel_af/dsl/models.py:76-110` | allows `start == end` (`:89-92`) — see NOT DOING |
| `validate_renderable(reel)` | `src/reel_af/dsl/models.py:376-405` | **weak** (D4) |
| `RenderabilityError` | `src/reel_af/dsl/models.py:372-373` | |
| `Diagnostic{code,message,severity,source,context}` | `src/reel_af/dsl/models.py:161-168` | not `DslDiagnostic` (D2) |
| `DiagnosticCode` (14 codes) | `src/reel_af/dsl/models.py:143-158` | incl. `INVALID_MARKER`, `CUTIN_INVALID` |
| `parse_marker(line, *, source)` | `src/reel_af/dsl/parser.py:36-74` | verbs `insert\|find\|extend\|join\|trans` |
| `serialize_marker` | `src/reel_af/dsl/parser.py:261-272` | re-emits `?` / `=> value` |
| `MarkerError` | raised `src/reel_af/dsl/parser.py:74` | unknown verb |
| `resolve_text(text, choose)` | `src/reel_af/dsl/resolver.py:55-111` | `?` holes; writes `=>` audit trail |
| `align(text, words, *, source)` | `src/reel_af/dsl/aligner.py:81-111` | → `AlignedSpan \| UnmatchedSpan` |
| `MATCH_QUALITY_FLOOR = 0.85` | `src/reel_af/dsl/models.py:19` | fuzzy floor |
| `UNMATCHED_SEGMENT` fail-closed | `src/reel_af/dsl/compile.py:186-194` | **already fails closed** |
| `UNRESOLVED_HOLE` emission | `src/reel_af/dsl/compile.py:143` | already fails closed |
| `download_segments(reel, out_dir, fetch, *, timeout_s)` | `src/reel_af/render/footage_stitch.py:88` | injected `SegmentFetchFn` (`:85`) |
| `stitch_footage_reel(reel, segment_assets, out_dir, run_id, *, timeout_s)` | `src/reel_af/render/footage_stitch.py:318` | takes a **pre-fetched** asset map |
| 1080x1920 normalize | `src/reel_af/render/footage_stitch.py:196-197` | `scale/crop`; consts `models.py:28-29` |
| `finish_reel(base, ctx, cfg, *, deps, raw, out_dir)` | `src/reel_af/render/finish.py:204` | `raw=True` early-return `:217-219` |
| `FinishContext` | `src/reel_af/render/finish.py:55-70` | **no cut-in field** |
| `FinishDeps` + `default_deps()` | `src/reel_af/render/finish.py:73-90`, `:93-125` | deps pattern to mirror |
| `CutInOverlay` | `src/reel_af/render/overlays.py:35-53` | `type/at_s/until_s/line/image_prompt/zoom_focus`; **absolute source time** (D7) |
| `normalize_cut_ins` | `src/reel_af/render/overlays.py:66` | sorts by absolute source time; reuse in B9a |
| `build_overlay_filtergraph` | `src/reel_af/render/overlays.py:111` | **dormant**; assumes sole graph over one input (`:145-147`, `:116`) |
| `_relative_window` | `src/reel_af/render/overlays.py:307-316` | abs→relative; **clamps** partial overlaps |
| out-of-segment cut-ins **silently dropped** | `src/reel_af/render/overlays.py:137-141` | the real `CUTIN_INVALID` gap (B9a) |
| `render_overlay_clip(segment_path, ...)` | `src/reel_af/render/overlays.py:200` | needs **a file per segment** (`:214-215`) — B9b only |
| `build_footage_filtergraph` | `src/reel_af/render/footage_stitch.py:151-315` | **pure string builder**; one `filter_complex` (`:295`), one ffmpeg exec (`:338`) — **no per-segment file boundary** |
| `upload_reel(local_path, *, run_id, filename, ...)` | `src/reel_af/storage.py:43-73` | returns `None` when unconfigured |
| `reel_output_name(...)` | `src/reel_af/naming.py:31` | descriptive filenames |
| `build_submission(target, body, ...)` | `web/reel_jobs.py:235-353` | if/elif chain; extend here |
| `ALLOWLISTED_TARGETS` | `web/reel_jobs.py:58-60` | `{TOPIC, COMPOSITE, TEXT_REEL, TEXT_CAROUSEL}` |
| `ReelSubmission` | `web/reel_jobs.py:107-116` | frozen dataclass |
| `FORBIDDEN_IDENTITY_FIELDS` | `web/reel_jobs.py:91-104` | 10 keys |
| `_reject_forbidden_identity` | `web/reel_jobs.py:145-148` | called `:251` (top) + `:259` (input) |
| `_is_valid_url` | `web/reel_jobs.py:151-156` | `scheme in (http,https)` + netloc |
| `validate_overrides` / `TUNABLES` | `web/tunables.py:115-131`, `:24-42` | override-allowlist model |
| `_handle_submit` | `web/server.py:310-350` | effect ordering to preserve |
| `_handle_poll` | `web/server.py:657+` | succeeded branch `:697` |
| `_resolve_result_ref` | `web/server.py:224-238` | verbatim `download_url`/`url` `:228-231`; scheme gate `:232-235`; `video_path` wrap `:236-237` (D5) |
| `_poll_response_body` | `web/server.py:260-270` | `payload = dict(cp_body)` `:261` **leaks whole result**; `error` only from CP `:266-269` (D5, B14) |
| `orphaned_dispatch` log | `web/server.py:339-347` | `%s`-format, log-only, omits `target` (D6) |
| `_dispatch_one` | **def** `web/server.py:424` | **unguarded** attach at **call `:443`**; escapes `_DispatchOutcome` contract, aborts fan-out at `:468` (D6) |
| `mark_stale_queued` | `web/pg.py:372-387` | sweeps `status='queued' AND execution_id IS NULL` — the B15b follow-up predicate |
| `FEATURE_SCHEMA` / `_assert_schema` | `web/pg.py:49-68` / `:92-107` | new table ⇒ `SchemaUnavailable` 503 across feature surface (D6) |
| `tests/dsl/test_compile_unsupported.py` | `:55`, `:85`, `:123` | pins `[insert relevant]`/`[insert file]`/`[find relevant]` as **supported** (D3) |
| `AppDeps` + `default_deps()` | `web/deps.py:322-343`, `:346-401` | test seam |
| `PgReelJobRepo` | `web/pg.py:223` | idempotent on `(org_id, created_by, client_request_id)` `:245` |
| A1-shaped fixtures | `tests/dsl/fixtures/v1_supported.ts.md`, `tests/dsl/fixtures/source.words.json` | already DSL v2 |

### MISSING — must be created

| Symbol | Why |
|---|---|
| `CompileContext` | D1 — does not exist |
| `TARGET_DSL_HOOKS` | no DSL-hooks target constant |
| `DSL_HOOKS_ALLOWED_INPUT_KEYS` | no canonicalization branch |
| `DSL_HOOKS_FINISH_OVERRIDE_KEYS` | render-override allowlist for this target |
| `dsl_hooks_to_reels` reasoner | no DSL wiring in `app.py` at all (grep: zero `reel_af.dsl` imports) |
| `A1_DELIVERY_UNAVAILABLE` + `DELIVERY_REQUIRED_TARGETS` | D5 |
| `CompositeDoc.invalid_markers` (or a diagnostics sink) | D2/B2 — swallowed `MarkerError` must survive `read_composite` |
| `DSL_HOOKS_UNSUPPORTED_VERBS` (workflow-keyed) | D3 — **not** a global set |
| pure cut-in → `CutInOverlay` mapper + `CUTIN_INVALID` | D7 / B9a |
| `hook-plan.json` fixture + reader | A1 artifact consumer |
| Strengthened `validate_renderable` postconditions | D4 |
| `_check_unsupported(…, *, context)` workflow-scoped policy | D3 |
| `_poll_response_body` result-strip + locally-derived error | D5 / B14 |
| `target` in the `orphaned_dispatch` log; `_dispatch_one` guard | D6 / B15a |

### Key constraint — target-id derivation

The AgentField SDK derives `reasoner_id = "<router prefix>_" + func.__name__`
(`sdk/python/agentfield/agent.py:3294-3339`), and the call target is
`f"{node_id}.{reasoner_id}"`. The router is `AgentRouter(prefix="reel")`
(`src/reel_af/app.py:98`), node is `reel-af`.

> **Therefore `reel-af.reel_dsl_hooks_to_reels` requires a function literally
> named `dsl_hooks_to_reels`** decorated `@reel.reasoner()`. Naming it
> `reel_dsl_hooks_to_reels` would yield `reel-af.reel_reel_dsl_hooks_to_reels`.

### Key constraint — current delivery contract

No reasoner treats a missing `download_url` as an error. All four conditionally
spread it (`app.py:501`, `:661`, `:848`, `:1323`) and always keep `video_path` as
fallback. `research_to_reel` even does
`"reel_ref": download_url or final.get("video_path", "")` (`app.py:1336`) — the
exact anti-pattern this target must avoid. **The DSL-hooks target deliberately
diverges** (D5).

---

## Desired End State

### Observable Behaviors

> **Status vs error code (settled in D5).** `delivery_unavailable` is an **error
> code**; the DB status is **`failed`**. The terminal status set is hardcoded in
> SQL (`pg.py:358-370`) and adding a status would need a root-owned schema change.

1. **Given** an A1-shaped `composite.ts.md` + `transcript.words.json`, **when**
   the DSL-hooks worker runs, **then** it compiles to a renderable `FootageReel`
   and renders a 1080x1920 finished mp4 (real-footage stitch + captions + hook
   banner) delivered as an `http(s)` URL.
2. **Given** any compile/resolve/align/renderability failure, **when** the worker
   runs, **then** it stops before stitch/finish and returns a typed diagnostic —
   never an invented clip.
3. **Given** a submitted DSL-hooks job whose result has no browser-deliverable
   `http(s)` URL, **when** polled, **then** the job is status `failed` with error
   code `delivery_unavailable` — never `succeeded` — **and the whole `result` dict
   is absent from the poll body**.
4. **Given** an article/topic/clip-plan/local-path/upload-handle-shaped input,
   **when** submitted to `TARGET_DSL_HOOKS`, **then** it is rejected **before**
   any DB row or CP dispatch.
5. **Given** a CP dispatch accepted but not attached, **when** submit **or
   fan-out** runs, **then** the `orphaned_dispatch` log carries `target`, and
   `_dispatch_one` honors its `_DispatchOutcome` contract instead of aborting the
   whole fan-out.
6. **Given** a malformed DSL marker, **when** compiled, **then** `INVALID_MARKER`
   is emitted — instead of today's **silent drop**.
7. **Given** an A1 zoom/visual cut-in, **when** it is mapped, **then** it becomes a
   validated `CutInOverlay`, **or** — if it falls outside **every** source segment
   — is explicitly rejected with `CUTIN_INVALID` instead of today's silent drop.
   **Overlay rendering is out of this slice** (B9b deferred): mapped cut-ins are
   validated but not yet burned in.

---

## What We're NOT Doing

**All of A1_workspace-blueprint** (next cycle): ingest, the `hook-plan.json`
*producer*, `meta.json` v2, the `composite.ts.md` producer,
`POST /api/video-runs`, `dispatch-manifest.json`, `run.lock`, atomic-write/lock
tests, A1 duration-bounds enforcement. A1 artifacts are **input fixtures** here.

**Non-target reel-af paths** (must fail closed, not be built):
`reel-af.reel_article_to_reel`, `reel-af.reel_topic_to_reel`, deterministic
`clip-plan.json` dispatch, YouTube `?t=&reel_end=` article seeds, readability
extraction, topic generation.

**Explicitly deferred with rationale:**

- **Deferred: B9b — the per-segment cut-in overlay RENDER stage** (operator
  decision after review BLOCKING-1). Slice A maps and validates cut-ins (B9a); it
  does **not** burn them in. Follow-up build notes, so the next slice starts from
  fact rather than rediscovery: **the seam is worker-level in `app.py`**, between
  the two existing calls — **not** in `footage_stitch.py`, which has **no
  per-segment file boundary** (`build_footage_filtergraph` `:151-315` is a pure
  string builder emitting one `filter_complex` at `:295`, consumed by exactly one
  ffmpeg exec at `:338`). `download_segments` already writes one file per segment
  (`footage_stitch.py:116`: `out_dir / f"{segment_id}.mp4"`) and
  `stitch_footage_reel(reel, segment_assets, ...)` consumes that map, so
  `overlays.render_overlay_clip` (`overlays.py:200`) can be inserted **between
  them** — file in, file out, exactly as `overlays.py` was designed, touching zero
  lines of `footage_stitch.py`. Splicing overlay chains *into* the monolithic graph
  instead is not recommended: `build_overlay_filtergraph` builds its own base chain
  from `[0:v]` (`overlays.py:145-147`) and indexes its own inputs from
  `visual_input_start=1` (`:116`), assuming it is the sole graph over a single
  input — splicing means rewriting every input index and discarding its base chain,
  i.e. reimplementing it, while fighting `MAX_FILTER_GRAPH_CHARS` (`:296-300`).
  **The core risk, which needs its own test, is double normalization**: downloaded
  segments are raw, untrimmed source in source coordinates, and
  `render_overlay_clip` re-encodes to a 1080x1920 canvas; `footage_stitch` then
  trims/scales/crops **again** (`:193-198`) using
  `trim_start_s = segment.start_s - asset.source_start_s` (`:191`). Double
  normalization plus post-overlay trim coordinates is a genuine correctness hazard
  and is the single biggest implementation risk in the cut-in work — which is why
  it is its own build, not a rider on this one. Data availability is **not** a
  blocker: `segment.start_s` (absolute source time) is already in scope at
  `footage_stitch.py:191`, satisfying `build_overlay_filtergraph`'s
  `segment_start_s` requirement.
- **Deferred: B15b — the durable orphan record + repair path** (operator decision
  after review BLOCKING-3). Two independent blockers, either alone fatal. **(a) The
  outage paradox**: the orphan fires when `attach_execution_id` raises, which
  `deps.py:90-91` converts to `RepositoryUnavailable` (503) — the trigger *is*
  database unavailability, so writing the record to that same Postgres fails
  exactly when it matters. Rev 1's Red test hid this behind a fake
  (`FakeReelJobRepo(attach_error=...)` asked to durably persist after raising "db
  down") — a false-green **at the design level**, which the suite would have
  certified. **(b) No migrations exist in this repo**: `web/pg.py:1-12` states
  reels-af "never owns or vendors the migrations
  (`migrations/deepresearch/` at the monorepo root)" — a different repo. A new table
  must be declared in `FEATURE_SCHEMA` (`pg.py:49-68`), which `_assert_schema`
  (`:92-107`) verifies against `information_schema.columns`, raising
  `SchemaUnavailable` → **503 across the entire feature surface** until the root
  migration lands; `.github/workflows/ci.yml` has **no migration gate**.
  **Recommended follow-up (BlueBay's approach — preferred over a new table)**: a
  **CP-reconciling sweep**. The orphan's precondition is that the job row *already
  exists* (`insert_or_get_queued` `:323` precedes dispatch `:329` and attach `:340`),
  so the orphan state is exactly `status='queued' AND execution_id IS NULL` — and
  `mark_stale_queued` (`pg.py:372-387`) **already sweeps that predicate**. The
  durable, queryable, org-scoped record of an orphaned dispatch **is the `reel_job`
  row itself**. A sweep that re-queries the control plane for executions belonging
  to stale-queued rows and attaches them is a **real repair** (it recovers the job,
  rather than merely recording its loss), reuses the existing predicate and reaper,
  and needs **no new table**.
- **Tightening `WordsSidecar` to strict `start < end`.** `models.py:89-92` allows
  `start == end`. The research only says reel-af *"should align ... before
  accepting additional non-A1 producers"* (research lines 381-383) — a
  conditional, future-facing should. Tightening it is a validation behavior
  change affecting **all** DSL producers, not just A1, and is not required by any
  Slice A behavior. Deferred; tracked as **Risk R3**.
- **Changing `_resolve_result_ref` globally.** Per D5, scoped per-target instead.
- **Merging `overlays.py` and `image_cutins.py`.** Per D7, kept separate.
- **Promoting the research's staged closure-adapter scaffold** (research lines
  1415-1423) — explicitly excluded by the research itself.
- **Named whole-job/per-subprocess render timeouts** (research lines 488-491) —
  the research marks these as required "before production enablement", a
  hardening gate outside this wiring slice. Tracked as **Risk R4**.
- **CP cancellation** (research lines 464-469) — no CP cancel route exists in v1.
- **`web/index.html` UI preset.** The research specifies a *hidden* API target
  (research line 1159: "adds hidden API support for `TARGET_DSL_HOOKS` only").

---

## Testing Strategy

- **Framework**: pytest, `asyncio_mode = "auto"` (`pyproject.toml:53-58`).
- **Run all**: `uv run --extra dev python -m pytest tests/ -q`
- **Run one**: `uv run --extra dev python -m pytest tests/web/test_dsl_hooks_submit.py -q`
- **Lint**: `uv run --extra dev ruff check src/ tests/ web/` (line-length 100,
  `select = ["E","F","I","N","W"]`). No mypy config exists in this repo.
- **Markers**: only `integration` (needs `TEST_DATABASE_URL`) —
  `pyproject.toml:56-58`.

**Test types**

- **Unit**: DSL parse/resolve/align/compile diagnostics; `validate_renderable`
  postconditions; cut-in mapping; `build_submission` canonicalization; delivery
  scheme validation.
- **Integration**: `read_composite`/`load_words`/`compile_composite` from real
  fixtures; `stitch_footage_reel` geometry; `finish_reel` burn-in (real ffmpeg);
  submit/poll through `create_app`.
- **Closure (BLOCKING)**: see Workflow Closure.

**Mocking / seams**

- **Web**: `make_deps(...)` → `AppDeps` with `FakeReelJobRepo`, `FakeControlPlane`,
  `FakeIdentity`, `FakeUploadStore` (`tests/web/conftest.py:1-598`); app built via
  `server.create_app(deps, enable_supertokens=False)`. "Rejected before row/CP" is
  asserted as `repo.inserted == []` and `cp.dispatch_calls == []`
  (`tests/web/test_submit.py:25-52`).
- **DSL**: `fixture_path`/`read_fixture`/`source_words_sidecar`, and
  `lavfi_mp4_factory` for real synthetic mp4s (`tests/dsl/conftest.py:1-87`).
- **ffmpeg**: two established patterns — `requires_ffmpeg` skipif
  (`tests/util.py:26-29`) for ordinary render tests, and **fail-closed**
  `_require_ffmpeg()` → `pytest.fail(...)` (`tests/test_finish_closure.py:37-39`)
  for BLOCKING closure gates. **Closure tests use the fail-closed variant** — a
  skipped closure test is UNVERIFIED, never green.
- **Burn-in assertions**: reuse `_band()`/`_band_variance()`/`_band_mean()` frame
  probes (`tests/test_finish_closure.py:123-192`).
- **S3**: `FakeS3` + `client_factory=lambda: s3` + `monkeypatch.setenv("REEL_BUCKET_NAME", ...)`
  (`tests/test_storage.py:13-24`).
- **Property tests**: `hypothesis` is already a dev dep and in use
  (`tests/dsl/test_aligner.py`, `tests/test_naming.py:52`).

**New test files**

```
tests/dsl/fixtures/a1_hook_plan.json          (new A1-shaped fixture)
tests/dsl/fixtures/a1_composite.ts.md         (new, hook/payoff shaped)
tests/dsl/test_invalid_markers.py             (B2)
tests/dsl/test_compile_context.py             (B4)
tests/dsl/test_unsupported_markers.py         (B3)
tests/dsl/test_renderability_postconditions.py(B6)
tests/dsl/test_cutin_mapping.py               (B9a — pure mapper, no render)
tests/dsl/test_a1_artifact_parity.py          (B17, BLOCKING)
tests/render/test_dsl_hooks_stitch.py         (B7)
tests/test_dsl_hooks_finish_closure.py        (B8, BLOCKING)
tests/test_dsl_hooks_worker_closure.py        (B5 + B16, BLOCKING)
tests/web/test_dsl_hooks_submit.py            (B10-B13)
tests/web/test_dsl_hooks_poll.py              (B14, BLOCKING)
tests/web/test_orphaned_dispatch.py           (B15a — log + fan-out guard)
```

**Existing tests that must stay green with no edits** (the compat contract):
- all **18** `tests/dsl/test_*.py` (B4/D1)
- `tests/dsl/test_compile_unsupported.py:55/:85/:123` (B3/D3 — these pin
  `[insert relevant]`/`[insert file]`/`[find relevant]` as **supported**)
- `tests/dsl/test_overlays.py` (B9a)
- `tests/web/test_submit.py`, `tests/web/test_poll.py` (B10-B14)

**Note on B5 vs CT-1's FORBIDDEN SPAN** (review): B5 monkeypatches
`stitch_footage_reel` and `finish_reel` — both in CT-1's forbidden span, both in
CT-1's file. This is **acceptable and intentional**: B5's assertion is
`calls == []`, a *negative* assertion that requires patching to observe
non-invocation, and it is a different test from CT-1. **Leave a comment in the
file** so a future reader doesn't "fix" the apparent violation.

---

## Workflow Closure

Derived from the research's **Target Workflow Closure Map** (research lines
1214-1291), not invented.

### Production Operation Chain

```
A1 artifacts (fixtures) -> _handle_submit(TARGET_DSL_HOOKS) -> insert_or_get_queued
  -> HttpControlPlane.dispatch_async -> [ASYNC: CP -> AgentField node]
  -> dsl_hooks_to_reels -> read_composite/load_words -> parse -> resolve/align
  -> compile_composite(+CompileContext) -> validate_renderable
  -> map_cut_ins (B9a: validate only, NO render — B9b deferred)
  -> download_segments -> stitch_footage_reel -> finish_reel
  -> upload_reel -> result{download_url}
  -> [ASYNC: CP completion] -> _handle_poll -> _resolve_result_ref
  -> delivery policy -> update_from_execution -> poll response
```

**Structural note.** The AgentField CP/worker drain is **outside this repo**
(research lines 1460-1463). A single in-process test therefore cannot span
submit→worker→poll without mocking the CP, which the framework forbids inside a
span. The chain is honestly split into **two BLOCKING closure tests joined by a
golden fixture parity test**, so no seam is faked away:

- **CT-1 (worker span)** — trigger the reasoner, run the REAL
  compile→validate→stitch→finish→upload chain, observe the result dict.
- **CT-2 (web span)** — trigger `_handle_submit`, drive the async edge with the
  real `_handle_poll` driver, observe the poll response + repo state.
- **Parity (B17) — also BLOCKING** (review BLOCKING-5). CT-2's CP body fixture is
  **generated from CT-1's actual output**, so the two spans meet on a real payload,
  not an invented one. Because B17 is what makes the split honest, it **inherits
  the classification of what it guarantees**: a load-bearing verification cannot be
  weaker than the tests it joins. Without it, CT-1 and CT-2 can both be green while
  the payload contract between them has silently diverged — the exact failure the
  split exists to prevent.

---

### Closure Test CT-1: "A dispatched DSL-hooks job produces a downloadable reel, or fails terminal `delivery_unavailable`." — **BLOCKING**

Reason: OBSERVABLE is reached through cross-module boundaries (dsl → render →
storage) and a new registration point (`@reel.reasoner()`).

- **SOURCE (seed only)**: `tests/dsl/fixtures/a1_composite.ts.md`,
  `a1_hook_plan.json`, `source.words.json`; synthetic source mp4 via
  `lavfi_mp4_factory` (`tests/dsl/conftest.py:44-86`).
- **TRIGGER (start)**: `app.dsl_hooks_to_reels(...)` : `src/reel_af/app.py` (new)
  — boundary = **highest_new_connector** (the reasoner registration is the
  topmost connector this slice adds on the worker side).
- **DRIVERS (async edges)**: none inside this span — the worker body is awaited
  directly. `asyncio.to_thread(upload_reel, ...)` is awaited, not slept on.
- **OBSERVE (assert via)**: the reasoner's returned result dict — `download_url`
  present and `http(s)`, **or** `error == A1_DELIVERY_UNAVAILABLE`.
- **FORBIDDEN SPAN** (never called/seeded/mocked by the test): `parse_marker`,
  `resolve_text`, `align`, `compile_composite`, `validate_renderable`,
  `download_segments`, `stitch_footage_reel`, `finish_reel`, the cut-in mapper.
  Two things are injected, and their provenance differs (review SHOULD-FIX-2 —
  rev 1 overstated this):
  - **`uploader` is pre-existing** (`app.py:1315-1316`:
    `if uploader is None: from reel_af.storage import upload_reel as uploader`) — a
    real seam with a production default.
  - **`fetch_segment` is NEW — created by this slice**, not inherited. `app.py` has
    **zero** dsl/footage_stitch coupling today (`grep -n "dsl" src/reel_af/app.py`
    → no matches). `SegmentFetchFn` (`footage_stitch.py:85`) is a real seam **in
    `footage_stitch`**, but not at the reasoner boundary CT-1 triggers from. It is
    still legitimate — a production parameter with a production default, mirroring
    `uploader` — but it is added here, and the closure argument must say so.
- **RED-AT-SEAM proof**: force `upload_reel` to return `None` (a reachable
  production state — `storage.py:43-73` returns `None` when unconfigured) → the
  test must go red unless the result is status `failed` + `delivery_unavailable`.
  (Rev 1's cut-in RED-AT-SEAM proof **presupposed B9b's seam** and is removed;
  cut-in rendering is out of this slice.)
- **DRIVABILITY**: store seam present (`uploader` kwarg, `client_factory`);
  fetch seam present (`SegmentFetchFn`); span fully synchronous → **no clock
  needed**.
- **EXECUTION (must run)**: needs real ffmpeg/ffprobe. CI installs ffmpeg via apt
  (`.github/workflows/ci.yml`). Uses the **fail-closed** `_require_ffmpeg()` →
  `pytest.fail(...)` pattern (`tests/test_finish_closure.py:37-39`), **never**
  `skipif`. If ffmpeg is absent the closure test FAILS RED.

### Closure Test CT-2: "A polled DSL-hooks job surfaces a bucket URL, or terminal `delivery_unavailable`." — **BLOCKING**

Reason: OBSERVABLE crosses the async CP edge and a new canonicalization
connector.

- **SOURCE (seed only)**: the submit HTTP body (A1 artifact refs + `source_url`);
  `FakeControlPlane` scripted with the **golden CP body generated by CT-1**.
- **TRIGGER (start)**: `web.server._handle_submit` : `web/server.py:310` —
  boundary = **outermost entrypoint** (via `create_app(...).test_client()`).
- **DRIVERS (async edges)**: CP-dispatch→completion → real synchronous driver
  `web.server._handle_poll` : `web/server.py:657`. No sleeps.
- **OBSERVE (assert via)**: the `_handle_poll` production response body + the
  reconciled repo row (`update_from_execution`, `web/pg.py:358-370`) — **never** a
  raw store read.
- **FORBIDDEN SPAN**: `build_submission`, `_resolve_cp_input`,
  `insert_or_get_queued`, `dispatch_async`, `attach_execution_id`,
  `_resolve_result_ref`, the delivery policy. Test seeds none of them.
- **RED-AT-SEAM proof**: remove `TARGET_DSL_HOOKS` from `ALLOWLISTED_TARGETS`
  (`reel_jobs.py:58-60`) → submit 400s and the test goes red at the connector;
  disable the delivery policy → the `video_path`-only case wrongly reports
  `succeeded` and the test goes red.
- **DRIVABILITY**: store seam present (`FakeReelJobRepo` via `AppDeps`); clock
  seam present (`FixedClock`, `tests/web/conftest.py`). Both pre-existing.
- **EXECUTION (must run)**: pure fakes, no infra — runs in the default CI job.
  Postgres SQL-contract coverage rides the existing `integration` marker.

---

## Behavior 1: Compile an A1-shaped composite + words to a `FootageReel`

**STATUS: ✅ DONE** — 9 tests. A1 fixtures compile to a 4-segment renderable reel (characterization held).

### Test Specification
**Given** an A1-shaped `composite.ts.md` and `transcript.words.json` fixture,
**when** `read_composite_file` → `load_words` → `compile_composite` runs,
**then** `CompileResult.status != "error"` and `plan` is a `FootageReel` with
`schema_version == "1"` and `dsl_version == "2"`.

**Edge cases**: empty composite (`EMPTY_COMPOSITE`, `compile.py:60`); words with
only `segments` and no `words` (fallback path, `aligner.py:197-230`).

**Files touched**: `tests/dsl/fixtures/a1_composite.ts.md` (new),
`tests/dsl/fixtures/a1_hook_plan.json` (new), `tests/dsl/test_a1_artifact_parity.py` (new).

### TDD Cycle

#### 🔴 Red
**File**: `tests/dsl/test_a1_artifact_parity.py`
```python
def test_a1_artifacts_compile_to_renderable_footage_reel(fixture_path):
    doc = read_composite_file(fixture_path("a1_composite.ts.md"))
    words = load_words(fixture_path("source.words.json"))
    result = compile_composite(doc, words, SourceRef(source_url=A1_SOURCE_URL))

    assert result.status != "error", [d.code for d in result.diagnostics]
    assert result.plan is not None
    assert result.plan.schema_version == "1"
    assert result.plan.dsl_version == "2"
    validate_renderable(result.plan)  # must not raise
```

#### 🟢 Green
No production change expected — this behavior **characterizes existing code**.
Only the A1-shaped fixtures are new. If it fails, the failure is the finding.

#### 🔵 Refactor
- [ ] No duplication: fixture loading goes through the existing
      `fixture_path`/`read_fixture` fixtures (`tests/dsl/conftest.py`), not new helpers.
- [ ] Reveals intent: fixture names state the A1 shape (`a1_*`).
- [ ] Fits existing patterns: mirrors `tests/dsl/test_fixture_scaffold.py`.

### Success Criteria
**Automated:**
- [ ] Red for the right reason (missing fixture), then green
- [ ] `uv run --extra dev python -m pytest tests/dsl/test_a1_artifact_parity.py -q`
- [ ] `uv run --extra dev ruff check tests/`

**Manual:**
- [ ] Fixture is genuinely A1-shaped (hook/payoff, not a toy)

---

## Behavior 2: Malformed markers survive `read_composite` and emit `INVALID_MARKER`

**STATUS: ✅ DONE** — 181 dsl green. N-1 applied: `[]` also undiagnosable via read_composite (regex needs non-empty body), replaced with `[extend sideways 0.5]`.

> **Motivation — this closes a latent silent-failure defect, and that makes B2
> more valuable than rev 1 claimed** (review BLOCKING-2). **Today a typo'd marker
> is silently ignored and the reel renders wrong, with no diagnostic and no
> warning.** `[bogus 1.0]` or a mistyped `[trans fade 1.0` is dropped on the floor
> by `read_composite`, the transition never happens, and nothing tells anyone. B2
> is the right place to close that.

### Test Specification
**Given** a composite containing a malformed marker, **when** it is read and
compiled, **then** the malformed marker **survives into `CompositeDoc`** and
`compile_composite` emits a typed **`INVALID_MARKER`** diagnostic (**not**
`DSL_MARKER_INVALID` — D2), `status == "error"`, `plan is None`.

**Why rev 1's Green was infeasible**: `read_composite` **swallows `MarkerError` in
both marker paths** and never appends the marker, so the `CompositeDoc` handed to
`compile_composite` carries **no record it ever existed** —
`src/reel_af/dsl/composite.py:99-101` (inline: `except MarkerError: pass`) and
`:121-125` (standalone: `except MarkerError: trailing_ok = False; continue`).
**No change confined to `compile.py` can turn this Red green.** Rev 1's Red was red
for *"no diagnostic exists at all"*, and its Green could not have fixed it.

**Edge cases**: `exclude=a,b`; `=> value` round-trip via `serialize_marker`
(`parser.py:261-272`).

**Cases deliberately EXCLUDED from the parametrize** (review BLOCKING-2 — rev 1
had three cases with three different mechanisms, one undiagnosable in principle):
- **`"[insert"`** — **removed**. No closing bracket ⇒ `_MARKER_LINE_RE` never
  matches ⇒ it is never treated as a marker at all, just ignored non-timecode text.
  **No implementation can emit `INVALID_MARKER` for it** without a heuristic
  "looks-like-a-broken-marker" scanner. Out of scope.
- **`"[trans notaprimitive 1.0]"`** — **removed from this test**. It may raise
  `MarkerError` in `_parse_trans` **or** parse cleanly and fail later as
  **`INVALID_TRANSITION`** (`models.py:153`) — a *different* code. Asserting
  `INVALID_MARKER` for it is a coin flip on parser internals. If wanted, cover it
  separately asserting `INVALID_TRANSITION`.

**Property**: `parse_marker(serialize_marker(m)) == m` for all valid markers —
hypothesis property test (unaffected by the above).

**Files touched**: `src/reel_af/dsl/composite.py`, `src/reel_af/dsl/models.py`
(the `CompositeDoc` model), `src/reel_af/dsl/compile.py`,
`tests/dsl/test_invalid_markers.py`.

### TDD Cycle

#### 🔴 Red
```python
@pytest.mark.parametrize("text", ["[bogus 1.0]", "[insert relevant ? => nope 5]"])
def test_malformed_marker_emits_INVALID_MARKER(text):
    doc = read_composite(f"00:00:01.000  line\n{text}\n")
    assert doc.invalid_markers            # RED: swallowed today, doc has 0 record

    result = compile_composite(doc, WORDS, SOURCE)
    assert result.status == "error"
    assert "INVALID_MARKER" in {d.code for d in result.diagnostics}
    assert result.plan is None

def test_malformed_marker_is_not_silently_dropped():
    """The latent defect: today this renders with no transition and no warning."""
    doc = read_composite("00:00:01.000  a\n[bogus 1.0]\n00:00:05.000  b\n")
    assert doc.markers == [] and doc.invalid_markers    # proves the silent drop
```

#### 🟢 Green
**File**: `src/reel_af/dsl/models.py` — add to `CompositeDoc` (or a diagnostics
sink parameter on `read_composite`):
```python
invalid_markers: list[InvalidMarker] = Field(default_factory=list)
```
**File**: `src/reel_af/dsl/composite.py:99-101` and `:121-125` — record the
swallowed `MarkerError` (text + `SourceLocus` + message) instead of discarding it.
Preserve today's parse-continuation behavior; only the record is new.
**File**: `src/reel_af/dsl/compile.py` — emit `INVALID_MARKER` from
`doc.invalid_markers` alongside `_check_unsupported`/`_check_unresolved` at
`compile.py:62-68`, reusing `_error_result`.

#### 🔵 Refactor
- [ ] No duplication: reuse `_error_result(code, message, diagnostics)`
      (`compile.py:123-125`) and the existing `SourceLocus`.
- [ ] Complexity down: the two `except MarkerError` sites record via **one** shared
      helper, not two copies.
- [ ] Reveals intent: `invalid_markers` names exactly what it holds.
- [ ] Follows the emission idiom: **bare string** `code="INVALID_MARKER"` at the
      site, cf. `compile.py:143` (**D2** — `DiagnosticCode` is a `Literal`, so
      "no literal outside `DiagnosticCode`" is **not implementable** and rev 1's
      checkbox is removed).

### Success Criteria
**Automated:**
- [ ] Red proves the marker is swallowed (`doc.invalid_markers` doesn't exist yet)
- [ ] Green; property test passes
- [ ] `read_composite`'s existing parse behavior unchanged for **valid** markers —
      all 18 `tests/dsl/test_*.py` green
- [ ] Full suite green: `uv run --extra dev python -m pytest tests/ -q`

**Manual:**
- [ ] Confirm a typo'd marker now surfaces a diagnostic rather than rendering wrong

---

## Behavior 3: Workflow-scoped marker rejection (DSL-hooks only)

**STATUS: ✅ DONE** — 6 new tests green; `test_compile_unsupported.py` 4/4 still green (BLOCKING-4 honored).

> **DEPENDS ON B4 — hard dependency, must land first** (review BLOCKING-4). B3's
> Red passes `context=DSL_HOOKS_CONTEXT`, so it **cannot even be written** until
> `CompileContext` exists. An implementer taking B3 standalone will be stuck.

> **This is a new policy, not a stub fill-in** (D3). `UNSUPPORTED_INSERT`/
> `UNSUPPORTED_FIND` are **vestigial**: `[insert relevant]`, `[insert file]`, and
> `[find relevant]` are **supported, tested features**. The stub returns `False`
> because nothing is unsupported *on the default workflow*. What Slice A adds is a
> **workflow-scoped** rejection policy.

### Test Specification
**Given** a composite containing a marker unsupported **by the DSL-hooks
workflow**, **when** compiled **with a DSL-hooks context**, **then** a typed
`UNSUPPORTED_INSERT`/`UNSUPPORTED_FIND` diagnostic is emitted, `status == "error"`,
`plan is None`, and **rendering stops**. **Given the same composite with no
context**, **then** behavior is **byte-for-byte unchanged** (`return False`).

**Edge cases**: `[insert file ...]` referencing an arbitrary local file — refused
**on this workflow only**; `[find relevant]` — refused on this workflow, still
**supported** by default.

**Files touched**: `src/reel_af/dsl/compile.py`, `tests/dsl/test_unsupported_markers.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_unsupported_insert_stops_render_on_dsl_hooks_workflow():
    doc = read_composite("00:00:01.000  line\n[insert file /etc/passwd]\n")
    result = compile_composite(doc, WORDS, SOURCE, context=DSL_HOOKS_CONTEXT)
    assert result.status == "error"
    assert "UNSUPPORTED_INSERT" in {d.code for d in result.diagnostics}
    assert result.plan is None

def test_no_context_leaves_default_workflow_untouched():
    """The compat contract: [insert file] is a SUPPORTED feature by default."""
    doc = read_composite("00:00:01.000  line\n[insert file rel_01]\n")
    result = compile_composite(doc, WORDS, SOURCE, relevant_dir=REL_DIR)
    assert "UNSUPPORTED_INSERT" not in {d.code for d in result.diagnostics}
```
**Red today for a specific reason**: `_check_unsupported` is `return False`
(`compile.py:132-133`), so nothing is ever rejected — on any workflow (D3).

#### 🟢 Green
**File**: `src/reel_af/dsl/compile.py:132-133` — thread the context; **`None` short-circuits**:
```python
DSL_HOOKS_UNSUPPORTED_VERBS: frozenset[str] = frozenset({"insert_file", "find"})  # workflow-keyed

def _check_unsupported(
    doc: CompositeDoc,
    diagnostics: list[Diagnostic],
    *,
    context: CompileContext | None = None,
) -> bool:
    if context is None:
        return False                       # byte-for-byte today's behavior
    found = False
    for att in doc.markers:
        code = _unsupported_code_for(att.marker, context.workflow)  # pure lookup
        if code is None:
            continue
        diagnostics.append(Diagnostic(
            code=code, message=f"marker unsupported on {context.workflow}: {att.marker.kind}",
            severity="error", source=att.source,
        ))
        found = True
    return found
```
Call site `compile.py:62` becomes `_check_unsupported(doc, diagnostics, context=context)`.

#### 🔵 Refactor
- [ ] **Pure control expressions**: `_unsupported_code_for` is a pure lookup; no
      diagnostic appends inside a compound condition (CodeCleanup).
- [ ] Mirrors `_check_unresolved` (`compile.py:136-149`) in shape — including its
      mutate-the-`diagnostics`-arg style. **Do not** invent a new idiom here; match
      the neighbor.
- [ ] Named constants: the unsupported set is a constant **keyed by workflow**
      (`DSL_HOOKS_UNSUPPORTED_VERBS`), **not** a global set — a global set is what
      would break the three green tests.

### Success Criteria
**Automated:**
- [ ] Red proves the stub never rejects on any workflow (D3)
- [ ] **`tests/dsl/test_compile_unsupported.py:55/:85/:123` still green with no
      edits** — the load-bearing regression proof
- [ ] All 18 `tests/dsl/test_*.py` green
- [ ] `_check_unresolved` behavior unchanged
- [ ] `uv run --extra dev python -m pytest tests/dsl/ -q`

---

## Behavior 4: `CompileContext` supplies what `.ts.md` + words cannot

**STATUS: ✅ DONE** — CompileContext+CutInSpec added; all 18 pre-existing dsl files green with no edits (D1 contract).

### Test Specification
**Given** a composite + words alone, **when** compiled **without** context,
**then** context-dependent features are unavailable; **when** compiled **with**
`CompileContext`, **then** `source_url`/`video_id`, delivery-required policy,
1080x1920 geometry, hook duration bounds, render defaults, and cut-in metadata
are all supplied explicitly and never inferred.

**Edge cases**: `context=None` (default) → **byte-for-byte today's behavior**
(the backward-compat guarantee of D1); context with cut-ins but no `hook_ref`.

**Files touched**: `src/reel_af/dsl/models.py`, `src/reel_af/dsl/compile.py`,
`tests/dsl/test_compile_context.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_compile_context_supplies_non_inferable_data():
    ctx = CompileContext(
        source_url=A1_SOURCE_URL, video_id="abc123",
        delivery_required=True,
        canvas_width=CANVAS_WIDTH, canvas_height=CANVAS_HEIGHT,
        min_hook_clip_s=A1_MIN_HOOK_CLIP_S, max_hook_clip_s=A1_MAX_HOOK_CLIP_S,
        cut_ins=[{"type": "zoom", "at_s": 3.0, "until_s": 5.0, "line": "...", "zoom_focus": "upper"}],
    )
    result = compile_composite(DOC, WORDS, SOURCE, context=ctx)
    assert result.status != "error"
    assert result.plan.source_url == A1_SOURCE_URL
```

> **Rev 1's `test_context_defaults_none_preserves_today_behavior` is DELETED**
> (review SHOULD-FIX-1). It asserted
> `compile_composite(D,W,S) == compile_composite(D,W,S, context=None)` — but both
> sides take **the same code path by construction**, since the default *is* `None`.
> It passes the instant the parameter exists and proves nothing; pre-change it is
> red for `TypeError: unexpected keyword argument`, a red for the **wrong reason**.
> **The D1 guarantee is carried entirely by the success criterion below** — "all 18
> `tests/dsl/test_*.py` still green with no edits" — which is the real proof. If a
> stronger in-test proof is ever wanted, capture a characterization snapshot of
> today's `CompileResult` **before** the change and assert against that.

#### 🟢 Green
**File**: `src/reel_af/dsl/models.py`
```python
class CompileContext(BaseModel):
    """Data the .ts.md + words sidecar cannot supply (research C27)."""
    model_config = ConfigDict(extra="forbid")

    workflow: str = DSL_HOOKS_WORKFLOW      # keys the B3 rejection policy
    source_url: str
    video_id: str | None = None
    delivery_required: bool = True
    canvas_width: int = CANVAS_WIDTH
    canvas_height: int = CANVAS_HEIGHT
    min_hook_clip_s: float = A1_MIN_HOOK_CLIP_S
    max_hook_clip_s: float = A1_MAX_HOOK_CLIP_S
    cut_ins: list[CutInSpec] = Field(default_factory=list)
```
**File**: `src/reel_af/dsl/compile.py:50-56` — add `context: CompileContext | None = None`
as the **last keyword-only** param (D1). The `workflow` field is what B3 keys its
workflow-scoped rejection on — without it, B3 would need a global set.

#### 🔵 Refactor
- [ ] No shallow wrapper: `CompileContext` carries validation, not pass-through.
- [ ] Named constants: `A1_MIN_HOOK_CLIP_S = 10`, `A1_MAX_HOOK_CLIP_S = 180`
      (research lines 128-130) as module constants; reuse `CANVAS_WIDTH/HEIGHT`
      from `models.py:28-29` — **do not** redeclare (they are also duplicated in
      `overlays.py:22-23`; see Risk R2).
- [ ] Export `CompileContext` from `dsl/__init__.py` (`:3-29`) alongside `SourceRef`.

### Success Criteria
**Automated:**
- [ ] Red (type missing), then green
- [ ] **All 18 `tests/dsl/test_*.py` files still green with no edits** — this **is**
      the D1 contract and the only real proof of it (SHOULD-FIX-1)
- [ ] `footage_stitch.py:17-32` unaffected — it imports from `reel_af.dsl.models`
      only and never imports `compile_composite` (zero production callers)
- [ ] `uv run --extra dev python -m pytest tests/ -q`

**Manual:**
- [ ] No caller is forced to change (verifies "optional" was honored)

---

## Behavior 5: `CompileResult.status == "error"` never reaches stitch/finish

**STATUS: ✅ DONE** — compile errors + INVALID_MARKER both prove `calls == []` (no stitch/finish).

### Test Specification
**Given** a compile that errors, **when** the worker runs, **then** it returns a
terminal typed failure and **neither** `stitch_footage_reel` **nor** `finish_reel`
is ever called.

**Edge cases**: `status == "error"` with `plan is not None`; `status == "ok"` with
`plan is None` (both must be refused — research line 263 requires rejecting
`plan is None` independently of status).

**Files touched**: `src/reel_af/app.py`, `tests/test_dsl_hooks_worker_closure.py`.

### TDD Cycle

#### 🔴 Red
```python
async def test_compile_error_never_reaches_stitch_or_finish(monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod, "stitch_footage_reel", lambda *a, **k: calls.append("stitch"))
    monkeypatch.setattr(app_mod, "finish_reel", lambda *a, **k: calls.append("finish"))

    result = await dsl_hooks_to_reels(**BAD_COMPOSITE_REFS)

    assert result["error"] == "dsl_compile_failed"
    assert result["diagnostics"]
    assert calls == []          # the load-bearing assertion
```

#### 🟢 Green
Guard clause in the worker **before** any render side effect:
```python
result = compile_composite(doc, words, source, context=ctx)
if result.status == "error" or result.plan is None:
    return _typed_failure("dsl_compile_failed", result.diagnostics)
```

#### 🔵 Refactor
- [ ] **Guard clauses before side effects** (CodeCleanup constraint) — no nesting.
- [ ] Mirrors the `{"error": ...}` return convention (`app.py:437`, `:1256`), never raises.
- [ ] Named constant for the error code, not an inline literal.

### Success Criteria
**Automated:**
- [ ] Red proves the guard is absent, then green
- [ ] `uv run --extra dev python -m pytest tests/test_dsl_hooks_worker_closure.py -q`

---

## Behavior 6: `FootageReel` renderability postconditions (stronger than schema)

**STATUS: ✅ DONE** — additive; nan proven rejected at construction (inf-shaped gap only).

### Test Specification
**Given** a `FootageReel`, **when** `validate_renderable` runs, **then** it
enforces — beyond today's four checks (D4) — finite spans, `start_s < end_s`,
duration math, transition adjacency/count, allowed transition primitives,
supported black segments, and `schema_version`/`dsl_version`.

**Edge cases**: `start_s == end_s` (**passes today** — `SourceSegment`
`models.py:181-189` only checks `ge=0`/`gt=0` independently); `float("inf")` /
`float("nan")` spans; a transition primitive outside `XfadeEffect` (`models.py:38-52`).

**Property**: for all reels, `validate_renderable(r)` raising ⇒ the reel is not
stitchable — add a hypothesis test over generated span tuples.

**Files touched**: `src/reel_af/dsl/models.py`, `tests/dsl/test_renderability_postconditions.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_zero_length_span_is_not_renderable():
    reel = _reel_with_segment(start_s=5.0, end_s=5.0)     # passes SourceSegment today
    with pytest.raises(RenderabilityError):
        validate_renderable(reel)

def test_non_finite_span_is_not_renderable():
    with pytest.raises(RenderabilityError):
        validate_renderable(_reel_with_segment(start_s=0.0, end_s=float("inf")))
```

#### 🟢 Green
**File**: `src/reel_af/dsl/models.py:376-405` — extend `validate_renderable` with
the missing postconditions, keeping the existing `RenderabilityError` contract so
`compile.py:109-117` keeps mapping it to `NON_RENDERABLE_REEL`.

#### 🔵 Refactor
- [ ] No duplication: **do not** re-implement checks `FootageReel._validate_reel`
      (`models.py:236-284`) already guarantees; add only the genuinely missing
      ones and say so in a comment naming the constraint.
- [ ] Complexity down: one flat sequence of guard checks, no nesting.
- [ ] Reveals intent: each check reads as its postcondition name.

### Success Criteria
**Automated:**
- [ ] Red proves `start_s == end_s` and non-finite spans pass today (D4)
- [ ] Green; **all existing DSL tests still pass** (tightening must not break valid reels)
- [ ] `uv run --extra dev python -m pytest tests/dsl/ -q`

**Manual:**
- [ ] No currently-valid A1 fixture becomes invalid

---

## Behavior 7: `stitch_footage_reel` normalizes source footage to 1080x1920

**STATUS: ✅ DONE** — 4 tests, real ffmpeg: 1920x1080 and 720x720 → 1080x1920.

### Test Specification
**Given** source segments of arbitrary geometry (e.g. 1920x1080 landscape),
**when** `download_segments` → `stitch_footage_reel` runs, **then** the base reel
probes as exactly **1080x1920**.

**Edge cases**: black segments (`BlackSegment`, `models.py:192-196`) — no asset
fetched (`footage_stitch.py:97`); duplicate `segment_id` →
`SegmentAssetValidationError` (`footage_stitch.py:109`); a segment whose asset
fails to resolve → `MISSING_SEGMENT_ASSET`.

**Files touched**: `tests/render/test_dsl_hooks_stitch.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_landscape_source_normalizes_to_vertical(lavfi_mp4_factory, tmp_path):
    landscape = lavfi_mp4_factory(width=1920, height=1080, seconds=6)
    assets = download_segments(REEL, tmp_path, fetch=lambda req: landscape)
    out = asyncio.run(stitch_footage_reel(REEL, assets, tmp_path, run_id="t"))

    assert _probe_dimensions(out) == (1080, 1920)
```

#### 🟢 Green
Characterization — `footage_stitch.py:196-197` already does
`scale=...:force_original_aspect_ratio=increase,crop=1080:1920`. If red, that is
the finding.

#### 🔵 Refactor
- [ ] No duplication: reuse `lavfi_mp4_factory` (`tests/dsl/conftest.py:44-86`).
- [ ] Fits patterns: mirrors `tests/test_stitch.py`.

### Success Criteria
**Automated:**
- [ ] Green; uses `requires_ffmpeg` skipif (this is not a closure gate)
- [ ] `uv run --extra dev python -m pytest tests/render/test_dsl_hooks_stitch.py -q`

---

## Behavior 8: `finish_reel` burns hook banner + captions + image cut-ins — **BLOCKING (CT-1 leg)**

**STATUS: ✅ DONE (BLOCKING, ran)** — 5 tests, real ffmpeg pixel probes: banner+captions+image cut-ins burned in.

> **Do not conflate these cut-ins with A1's** (D7). The "image cut-ins" here are
> `finish_reel`'s **own LLM-picked image moments** — `image_cutins.ImageCutIn`,
> final-reel-relative, images-only, already live via `finish.py:107-108`/`:264`.
> They are **in scope** and the research requires this test (line 530-531). A1's
> **zoom/visual** cut-ins are a different subsystem (`overlays.CutInOverlay`,
> absolute source time); they are **mapped only** in this slice (B9a) and **not
> rendered** (B9b deferred). Slice A therefore ships reels with hook banner +
> captions + finish's image cut-ins burned in, but **without** A1 zoom/visual
> overlays.

### Test Specification
**Given** a stitched base reel, **when** `finish_reel` runs with DSL-hooks
defaults, **then** the hook banner, safe-zone captions, and image cut-ins are
**burned into the final mp4** — verified by frame pixel probes, not by mock
assertions. **No `raw`/`fast` opt-out is exposed on this workflow.**

**Edge cases**: `raw=True` must be **unreachable** from the DSL-hooks path
(`finish.py:217-219` early-returns the unfinished base — that must never be this
target's output).

**Files touched**: `tests/test_dsl_hooks_finish_closure.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_dsl_hooks_finish_burns_banner_captions_cutins(tmp_path):
    _require_ffmpeg()                       # fail-closed, never skip (BLOCKING)
    out = asyncio.run(finish_reel(base, ctx, cfg, deps=_probe_deps()))

    assert _band_variance(out, at_s=1.0, band="hook")    > BANNER_VARIANCE_FLOOR
    assert _band_variance(out, at_s=4.0, band="caption") > CAPTION_VARIANCE_FLOOR
    assert _band_mean(out, at_s=CUTIN_T, band="full")   != _band_mean(base, at_s=CUTIN_T, band="full")

def test_dsl_hooks_path_never_exposes_raw_optout():
    assert "raw" not in DSL_HOOKS_FINISH_OVERRIDE_KEYS
```

#### 🟢 Green
Call `finish_reel` from the worker with `raw=False` always; the override
allowlist for this target **excludes** `raw`/`fast`.

#### 🔵 Refactor
- [ ] No duplication: reuse `_band`/`_band_variance`/`_band_mean`
      (`tests/test_finish_closure.py:123-192`) — import, don't copy.
- [ ] Named constants for variance floors, not magic numbers.

### Success Criteria
**Automated:**
- [ ] **Fail-closed** if ffmpeg absent (`pytest.fail`, never `skipif`)
- [ ] Green: `uv run --extra dev python -m pytest tests/test_dsl_hooks_finish_closure.py -q`

**Manual:**
- [ ] Eyeball one produced mp4 — banner/captions/cut-ins visibly present

---

## Behavior 9a: A1 cut-ins map to validated `CutInOverlay`, or are typed-rejected

**STATUS: ✅ DONE** — 12 tests. `src/reel_af/render/` provably untouched (git status clean). overlays.py now has its first src/ consumer.

> **Scope (operator decision after review BLOCKING-1)**: this is the **pure
> mapper only**. **No render.** It touches **zero** lines of `footage_stitch.py`.
> The overlay **render** stage (B9b) is deferred — see What We're NOT Doing.

### Test Specification
**Given** `hook-plan.json` cut-ins (`zoom` with `zoom_focus`, `visual` with
`image_prompt` — research lines 349-364), **when** mapped against a compiled
`FootageReel`, **then** each becomes a validated `overlays.CutInOverlay`; **and** a
cut-in falling outside **every** source segment's span is rejected with a typed
**`CUTIN_INVALID`** diagnostic instead of being **silently dropped** (today's
behavior at `overlays.py:137-141`).

**The mapping is near-identity on the time fields** (D7): A1 cut-ins are absolute
source time and `CutInOverlay` is absolute source time. This behavior is
**validation + typing**, not arithmetic.

**Edge cases**: cut-in outside every segment → `CUTIN_INVALID` (**the real gap**);
`visual` without `image_prompt` (already refused by `overlays.py:51-52`);
`until_s <= at_s` (already refused by `overlays.py:49-50`).

**Explicitly NOT rejected** (review D7 consequence 2): a cut-in **spanning a
segment boundary**. `_relative_window` (`overlays.py:307-316`) **clamps** it to
each overlapping segment via `max()`/`min()`, so it renders on both — that is the
library's **designed semantics**. Rejecting it would be a policy divergence from
the library, and detecting it would require re-deriving `_relative_window`'s
arithmetic in the mapper, violating this behavior's own no-duplication checkbox.
Rev 1 called this "no valid representation"; that was **wrong**.

**Files touched**: `src/reel_af/dsl/compile.py` (cut-in spec on context),
a new pure mapper module, `tests/dsl/test_cutin_mapping.py`.
**`footage_stitch.py` is NOT touched.**

### TDD Cycle

#### 🔴 Red
```python
def test_a1_cutins_map_to_validated_CutInOverlay():
    mapped, diags = map_cut_ins(A1_CUT_INS, reel=REEL)
    assert [o.type for o in mapped] == ["zoom", "visual"]
    assert isinstance(mapped[0], CutInOverlay)
    assert mapped[0].zoom_focus == "upper"
    assert (mapped[0].at_s, mapped[0].until_s) == (103.0, 105.0)   # absolute, unchanged
    assert diags == []

def test_cutin_outside_every_segment_is_typed_rejected_not_dropped():
    """Today overlays.py:137-141 filters it out silently."""
    _, diags = map_cut_ins([CUTIN_PAST_END_OF_REEL], reel=REEL)
    assert "CUTIN_INVALID" in {d.code for d in diags}

def test_boundary_spanning_cutin_is_ACCEPTED_and_clamped_by_the_library():
    """Not a rejection case — overlays.py:307-316 clamps to each segment."""
    mapped, diags = map_cut_ins([CUTIN_SPANNING_TWO_SEGMENTS], reel=REEL)
    assert diags == []
    assert len(mapped) == 1
```

#### 🟢 Green
A **pure** mapper: hook-plan cut-in dicts → validated `CutInOverlay`, emitting
`CUTIN_INVALID` for cut-ins outside every segment. Time fields pass through
unchanged (both are absolute source time). Reuse `overlays.normalize_cut_ins`
(`overlays.py:66`) for validation + ordering. **`finish.py`'s `image_cutins` path
is untouched** (D7).

#### 🔵 Refactor
- [ ] No duplication: reuse `overlays.normalize_cut_ins`; **do not** re-derive
      `_relative_window`'s clamping arithmetic — that is the library's job.
- [ ] Complexity down: a pure function returning `(overlays, diagnostics)` — no
      side effects, no raising.
- [ ] Reveals intent: the name says *validate + type*, not "translate time bases"
      (there is no translation — D7).

### Success Criteria
**Automated:**
- [ ] Red proves out-of-segment cut-ins are silently dropped today
      (`overlays.py:137-141`)
- [ ] Green; `tests/dsl/test_overlays.py` still passes unchanged
- [ ] **`footage_stitch.py`, `finish.py`, `image_cutins.py` untouched** — assert by
      diff review
- [ ] `uv run --extra dev python -m pytest tests/dsl/ -q`

**Manual:**
- [ ] Confirm no render behavior changed — Slice A does not burn in cut-ins

---

## Behavior 10: `TARGET_DSL_HOOKS` allowlist + canonicalization

**STATUS: ✅ DONE** — part of 74 submit tests.

### Test Specification
**Given** `POST /api/v1/execute/async/reel-af.reel_dsl_hooks_to_reels` with a
valid A1 body, **when** submitted, **then** it is allowlisted and canonicalized
to `ReelSubmission(target=TARGET_DSL_HOOKS, params={target, source_mode:"dsl_hooks", clip_idx}, cp_input={source_url, composite_ref, words_ref, hook_ref, clip_idx})`.

**Edge cases**: unknown key under `input` → `unsupported_input_field`;
`source_url` empty/non-HTTP(S) → `invalid_url`; non-int `clip_idx`.

**Files touched**: `web/reel_jobs.py`, `tests/web/test_dsl_hooks_submit.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_dsl_hooks_target_is_allowlisted_and_canonicalized():
    sub = build_submission(TARGET_DSL_HOOKS, {"input": VALID_A1_INPUT})
    assert sub.target == TARGET_DSL_HOOKS
    assert sub.params == {"target": TARGET_DSL_HOOKS, "source_mode": "dsl_hooks", "clip_idx": 1}
    assert sub.cp_input == {
        "source_url": A1_SOURCE_URL, "composite_ref": COMPOSITE_REF,
        "words_ref": WORDS_REF, "hook_ref": HOOK_REF, "clip_idx": 1,
    }
    assert sub.source_handle is None
```

#### 🟢 Green
**File**: `web/reel_jobs.py`
```python
TARGET_DSL_HOOKS = "reel-af.reel_dsl_hooks_to_reels"           # near :23-36
DSL_HOOKS_SOURCE_MODE = "dsl_hooks"
DSL_HOOKS_ALLOWED_INPUT_KEYS = (
    frozenset({"source_url", "composite_ref", "words_ref", "hook_ref", "clip_idx", "overrides"})
    | _METADATA_INPUT_KEYS
)
ALLOWLISTED_TARGETS = frozenset({..., TARGET_DSL_HOOKS})        # :58-60
```
plus a dedicated `if target == TARGET_DSL_HOOKS:` branch before the unreachable
guard (`reel_jobs.py:351-353`), mirroring the composite branch's shape.

#### 🔵 Refactor
- [ ] Named constants: no inline target/mode literals (CodeCleanup).
- [ ] Fits patterns: `<TARGET>_ALLOWED_INPUT_KEYS | _METADATA_INPUT_KEYS`
      (`reel_jobs.py:71-87`); `BadRequest(msg, code=...)` rejects.
- [ ] **Do not reorder** the existing branch chain — append only (externally-owned
      target ids; research lines 577-580).

### Success Criteria
**Automated:**
- [ ] Red (`unsupported_target`), then green
- [ ] `uv run --extra dev python -m pytest tests/web/test_dsl_hooks_submit.py -q`
- [ ] Existing `tests/web/test_submit.py` unchanged and green

---

## Behavior 11: Submit maps artifact refs + `source_url` to identity-free `cp_input`

**STATUS: ✅ DONE** — identity-free cp_input verified at the dispatch boundary.

### Test Specification
**Given** a valid DSL-hooks submit, **when** dispatched, **then** the CP body is
`{"input": {source_url, composite_ref, words_ref, hook_ref, clip_idx}}` and
carries **no** identity field and **no** metadata key.

**Edge cases**: allowlisted finish render-overrides included only when non-empty
(mirroring `reel_jobs.py:313-314`).

**Files touched**: `web/reel_jobs.py`, `tests/web/test_dsl_hooks_submit.py`.

### TDD Cycle

#### 🔴 Red
```python
def test_dsl_hooks_dispatch_body_is_identity_free():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(DSL_HOOKS_URL, json={"input": VALID_A1_INPUT})

    assert resp.status_code == 200
    (target, body), = cp.dispatch_calls
    assert target == TARGET_DSL_HOOKS
    assert set(body["input"]) == {"source_url", "composite_ref", "words_ref", "hook_ref", "clip_idx"}
    assert not (set(body["input"]) & FORBIDDEN_IDENTITY_FIELDS)
    assert "client_request_id" not in body["input"]
```

#### 🟢 Green
Construct `cp_input` explicitly from validated locals — never by copying
`raw_input`. Allowlist finish overrides via a `DSL_HOOKS_FINISH_OVERRIDE_KEYS`
set, mirroring `web/tunables.py:115-131`.

#### 🔵 Refactor
- [ ] **No side effects in conditionals** — build `cp_input` after validation.
- [ ] No duplication: reuse `validate_overrides`; don't fork `TUNABLES`.

### Success Criteria
- [ ] Red, then green; `uv run --extra dev python -m pytest tests/web/ -q`

---

## Behavior 12: Submit rejects filesystem paths + forbidden identity **before row/CP**

**STATUS: ✅ DONE** — 10 identity fields × 2 placements + 15 path shapes, all no-row/no-CP.

### Test Specification
**Given** a body with a forbidden identity field (top-level **or** under `input`)
or an arbitrary filesystem path as `source_url`/an artifact ref, **when**
submitted, **then** it is rejected 400 with **no DB row** and **no CP dispatch**.

**Edge cases**: `file:///etc/passwd`; `/etc/passwd`; `../../secret`;
`s3://bucket/key` as `source_url` (not HTTP(S) → reject); identity nested under
`input`.

**Property**: for all strings not matching `^https?://` with a netloc,
`source_url` is rejected — hypothesis test.

**Files touched**: `web/reel_jobs.py`, `tests/web/test_dsl_hooks_submit.py`.

### TDD Cycle

#### 🔴 Red
```python
@pytest.mark.parametrize("bad", ["/etc/passwd", "file:///etc/passwd", "../../x", "s3://b/k", ""])
def test_filesystem_paths_rejected_before_row_and_cp(bad):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    resp = _client(deps).post(DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, "source_url": bad}})

    assert resp.status_code == 400
    assert repo.inserted == []          # no row
    assert cp.dispatch_calls == []      # no dispatch

@pytest.mark.parametrize("field", sorted(FORBIDDEN_IDENTITY_FIELDS))
def test_forbidden_identity_rejected_top_level_and_under_input(field):
    ...  # both placements, both 400, both no-row/no-CP
```

#### 🟢 Green
Reuse `_is_valid_url` (`reel_jobs.py:151-156`) for `source_url`; add an
artifact-ref validator refusing filesystem-ish refs. Identity rejection is
**already** wired at `reel_jobs.py:251` + `:259` and runs before any branch.

#### 🔵 Refactor
- [ ] **Guard clauses before side effects** — all rejects precede row/CP by
      construction (`_handle_submit` calls `build_submission` at `server.py:315`,
      before `insert_or_get_queued` at `:323`).
- [ ] No duplication: reuse `_is_valid_url`; do not re-implement URL parsing.

### Success Criteria
**Automated:**
- [ ] Every case: 400 + `repo.inserted == []` + `cp.dispatch_calls == []`
- [ ] Property test green
- [ ] `uv run --extra dev python -m pytest tests/web/test_dsl_hooks_submit.py -q`

---

## Behavior 13: Submit fails closed against article/topic/clip-plan inputs

**STATUS: ✅ DONE** — article/topic/clip-plan/upload-handle + `?t=&reel_end=` seeds all rejected pre-row.

### Test Specification
**Given** a non-target input shape — `reel-af.reel_article_to_reel`,
`reel-af.reel_topic_to_reel`, a scoped article URL (`?t=&reel_end=`), a topic
string, a local path, or an upload handle — **when** submitted, **then** it is
rejected **before row/CP** (research lines 274-290, 542-544).

**Edge cases**: `TARGET_ARTICLE` is a real constant (`reel_jobs.py:26`) but **not**
allowlisted — must stay 400; a `source`/upload-handle key under DSL-hooks input →
`unsupported_input_field`; `?t=90&reel_end=142` on `source_url` → rejected as an
article seed.

**Files touched**: `web/reel_jobs.py`, `tests/web/test_dsl_hooks_submit.py`.

### TDD Cycle

#### 🔴 Red
```python
@pytest.mark.parametrize("target", [TARGET_ARTICLE, "reel-af.reel_topic_to_reel"])
def test_non_target_targets_rejected(target): ...   # 400, no row, no CP

@pytest.mark.parametrize("extra", [{"topic": "black holes"}, {"source": "upload-handle"},
                                   {"url": "https://x/a"}, {"clip_plan": "clip-plan.json"}])
def test_non_target_input_shapes_rejected(extra): ...

def test_scoped_article_seed_url_rejected():
    bad = "https://www.youtube.com/watch?v=abc123&t=90&reel_end=142"
    ...  # 400, no row, no CP — the DSL-hooks target is not an article seed consumer
```

#### 🟢 Green
Strict `_reject_unsupported_fields(raw_input, DSL_HOOKS_ALLOWED_INPUT_KEYS)`
plus an explicit article-seed guard on `source_url`.

#### 🔵 Refactor
- [ ] Named constant for the rejected query keys (`t`, `reel_end`) — no literals.
- [ ] No duplication: one shared "fails closed" assertion helper across B12/B13.

### Success Criteria
**Automated:**
- [ ] All cases 400 + no row + no CP
- [ ] `TARGET_ARTICLE` **still** absent from `ALLOWLISTED_TARGETS` (regression)
- [ ] `uv run --extra dev python -m pytest tests/web/ -q`

---

## Behavior 14: Poll — `download_url` success, else terminal `delivery_unavailable` — **BLOCKING (CT-2)**

**STATUS: ✅ DONE (BLOCKING CT-2, ran)** — 23 tests; red-at-seam observed (10 red when policy disabled). Parity green for all 4 non-DSL-hooks targets.

### Test Specification
**Given** a polled DSL-hooks job, **when** the CP result carries a browser-
deliverable `http(s)` `download_url`, **then** the job reconciles `succeeded`
with that URL as `result_ref`; **when** the result has only a node-local
`video_path` (or a non-`http(s)` `download_url`), **then** the job is terminal
**`delivery_unavailable`** — **not** `succeeded`, and no node-local path is
presented as a browser URL.

**Edge cases**: `download_url: "/tmp/out.mp4"` (**passes verbatim today** —
`server.py:228-231` has no scheme check → D5); `download_url: "file:///tmp/x"`;
`download_url: "https://"` (scheme but **no netloc** — must be refused, which is
why `_is_valid_url` is reused rather than `startswith`); `video_path` only → today
yields `cp-execution://...` **and `succeeded`** (D5); transient CP 5xx must **not**
write terminal state (existing behavior, `tests/web/test_poll.py:102`).

**Files touched**: `web/server.py` — **including `_poll_response_body`
(`web/server.py:260-270`)**, `web/reel_jobs.py`, `tests/web/test_dsl_hooks_poll.py`.

> **Two gaps rev 1's Green could not satisfy** (review BLOCKING-6) — its own tests
> would have failed against it:
> 1. **The poll body leaks `video_path` via `dict(cp_body)`.**
>    `_poll_response_body` (`:261`) copies the **entire** CP body into the response,
>    `result` included. Suppressing `result_ref` does **not** suppress the raw
>    `result` dict. **Rule: on the delivery-unavailable path, strip/redact the whole
>    `result` dict from the poll body** — not just `result_ref`.
> 2. **`error` is unsettable from a local condition.** `:266-269` populates
>    `payload["error"]` **only** from `_execution_error(cp_body)` — a **CP-reported**
>    error. Here the CP said `succeeded`, so there is no error in `cp_body`.
>    **`_poll_response_body` must accept a locally-derived error** for
>    `body["error"] == A1_DELIVERY_UNAVAILABLE` to pass.

### TDD Cycle

#### 🔴 Red
```python
def test_dsl_hooks_poll_succeeds_with_browser_deliverable_url():
    cp = FakeControlPlane(execution={"status": "succeeded",
                                     "result": {"download_url": "https://bucket/x.mp4"}})
    body = _poll(deps_with(cp, target=TARGET_DSL_HOOKS)).get_json()
    assert body["status"] == "succeeded"
    assert repo.updated[-1].result_ref == "https://bucket/x.mp4"

def test_dsl_hooks_poll_without_download_url_is_terminal_delivery_unavailable():
    cp = FakeControlPlane(execution={"status": "succeeded",
                                     "result": {"video_path": "/tmp/node/out.mp4"}})
    body = _poll(deps_with(cp, target=TARGET_DSL_HOOKS)).get_json()
    assert body["status"] == "failed"                      # status, not a new enum
    assert body["error"] == A1_DELIVERY_UNAVAILABLE        # needs local-error param
    assert "/tmp/node/out.mp4" not in json.dumps(body)     # never exposed

def test_delivery_unavailable_strips_the_whole_result_dict():
    """BLOCKING-6: dict(cp_body) at :261 leaks result wholesale."""
    cp = FakeControlPlane(execution={"status": "succeeded",
                                     "result": {"video_path": "/tmp/node/out.mp4",
                                                "reels": ["/tmp/node/a.mp4"]}})
    body = _poll(deps_with(cp, target=TARGET_DSL_HOOKS)).get_json()
    assert "result" not in body            # the ENTIRE dict, not just result_ref

@pytest.mark.parametrize("bad", ["/tmp/node/out.mp4", "file:///tmp/x", "https://"])
def test_non_browser_deliverable_download_url_is_refused(bad):
    """Proves the missing scheme check (D5). 'https://' has no netloc -> _is_valid_url."""
    cp = FakeControlPlane(execution={"status": "succeeded",
                                     "result": {"download_url": bad}})
    body = _poll(deps_with(cp, target=TARGET_DSL_HOOKS)).get_json()
    assert body["status"] == "failed"
    assert body["error"] == A1_DELIVERY_UNAVAILABLE

# PARITY (D5) — parametrized over ALL non-DSL-hooks targets (SHOULD-FIX-3)
@pytest.mark.parametrize(
    "target", sorted(ALLOWLISTED_TARGETS - DELIVERY_REQUIRED_TARGETS)
)
def test_existing_targets_keep_fail_soft_behavior(target):
    cp = FakeControlPlane(execution={"status": "succeeded",
                                     "result": {"video_path": "/tmp/x.mp4"}})
    body = _poll(deps_with(cp, target=target)).get_json()
    assert body["status"] == "succeeded"        # unchanged for all four
```

#### 🟢 Green
```python
A1_DELIVERY_UNAVAILABLE = "delivery_unavailable"     # an ERROR CODE, not a status
DELIVERY_REQUIRED_TARGETS = frozenset({TARGET_DSL_HOOKS})

def _is_browser_deliverable(ref: str | None) -> bool:      # pure question
    # SHOULD-FIX-4: delegate; do NOT re-implement. startswith() would accept
    # "https://" with no netloc — _is_valid_url checks scheme AND netloc.
    return isinstance(ref, str) and _is_valid_url(ref)
```
Three coordinated changes:
1. **Reconcile** (`server.py:697`, succeeded branch): if
   `job.target in DELIVERY_REQUIRED_TARGETS and not _is_browser_deliverable(ref)`
   → write terminal **status `failed`** via `update_from_execution`. All other
   targets unchanged.
2. **`_poll_response_body`** (`:260-270`): accept an optional locally-derived
   `error` param, and **strip the `result` dict** when that error is set — `dict(cp_body)`
   at `:261` is what leaks `video_path` today.
3. **Reuse** `_is_valid_url` from `reel_jobs.py:151-156` (import; do not fork).

#### 🔵 Refactor
- [ ] **Pure control expressions**: `_is_browser_deliverable` is a predicate; no
      DB write inside the condition (CodeCleanup).
- [ ] No duplication: `_is_browser_deliverable` **delegates to `_is_valid_url`** —
      honoring B12's own checkbox rather than introducing a second, weaker idiom.
- [ ] Named constants: no inline `"delivery_unavailable"` / scheme literals.
- [ ] **Do not reorder** DB status strings (externally-owned; research lines
      577-580) — which is exactly why `delivery_unavailable` is an error **code**
      and the status stays `failed` (D5).
- [ ] Parity test parametrized over `ALLOWLISTED_TARGETS - DELIVERY_REQUIRED_TARGETS`
      so any future allowlisted target inherits the check **by construction**.

### Success Criteria
**Automated:**
- [ ] Red proves `video_path`-only currently reports `succeeded` (D5)
- [ ] Red proves a non-`http(s)` `download_url` currently passes verbatim (D5)
- [ ] Red proves the whole `result` dict currently reaches the poll body (D5 gap 3)
- [ ] Green; **`tests/web/test_poll.py` unchanged and green** (no regression)
- [ ] Parity green for **all four** non-DSL-hooks targets
- [ ] `uv run --extra dev python -m pytest tests/web/ -q`

**Manual:**
- [ ] No node-local path appears anywhere in a poll response body

---

## Behavior 15a: Orphan log carries `target`; `_dispatch_one` stops aborting the fan-out

**STATUS: ✅ DONE** — 7 tests. N-2 applied: `_DispatchOutcome.outcome` (not `.disposition`). No migration added.

> **Scope (operator decision after review BLOCKING-3)**: the **zero-migration
> half**. The durable record + repair path (**B15b**) is deferred — it is
> unreachable in its own failure mode and this repo owns no migrations. See What
> We're NOT Doing → *Deferred: B15b*, which also records the recommended
> CP-reconciling-sweep alternative.

### Test Specification
**Given** a CP dispatch that returns an `execution_id` but whose
`attach_execution_id` fails, **when** **submit** runs, **then** the
`orphaned_dispatch` log line carries `target` alongside the existing
`job_id`/`execution_id`/`org_id`/`created_by`/`client_request_id`/`err`; **and
when fan-out runs**, **then** `_dispatch_one` returns a `_DispatchOutcome` rather
than letting the `HttpError` escape and abort sibling dispatches.

**Edge cases**: `_dispatch_one`'s attach (**call at `server.py:443`**, function
**defined at `:424`**) is unguarded today, so an `HttpError` escapes and aborts the
whole fan-out at its call site `:468` — breaching its own documented contract
(`:425-427`: "Returns a disposition instead of raising"). This is the genuinely
valuable half of the original B15 and needs **no schema at all**.

**Files touched**: `web/server.py` (`:342-346` log, `:443` guard),
`tests/web/test_orphaned_dispatch.py`. **No migration. No new table.**

### TDD Cycle

#### 🔴 Red
```python
def test_orphaned_dispatch_log_includes_target(caplog):
    repo = FakeReelJobRepo(attach_error=RepositoryUnavailable("db down"))
    cp = FakeControlPlane(dispatch_result=(200, {"execution_id": "exec_1"}, {}))
    resp = _client(make_deps(..., reel_jobs=repo, control_plane=cp)).post(DSL_HOOKS_URL, json=...)

    assert resp.status_code == 503                      # unchanged behavior
    (rec,) = [r for r in caplog.records if "orphaned_dispatch" in r.getMessage()]
    assert TARGET_DSL_HOOKS in rec.getMessage()         # RED: target omitted today

def test_fanout_attach_failure_returns_outcome_not_raises():
    """server.py:443 is unguarded: HttpError escapes and aborts the whole fan-out."""
    repo = FakeReelJobRepo(attach_error=RepositoryUnavailable("db down"))
    outcome = _dispatch_one(deps_with(repo), ctx, TARGET_DSL_HOOKS, submission, job_id, crid, now)
    assert outcome.ok is False                          # RED: raises today
    assert outcome.disposition == "attach_failed"

def test_fanout_sibling_dispatches_survive_one_orphan():
    """The contract breach: one bad attach must not take down the batch."""
    ...  # assert the other clips still dispatch
```

#### 🟢 Green
1. Add `target` to the existing log (`server.py:342-346`) — a one-line,
   zero-migration fix that closes the plan's stated gap.
2. Wrap `_dispatch_one`'s attach (`:443`) in the same `RepositoryUnavailable`
   handler, returning a `_DispatchOutcome` so it honors its own contract.

#### 🔵 Refactor
- [ ] No duplication: one `_log_orphaned_dispatch(...)` helper used by **both**
      `_handle_submit` and `_dispatch_one`.
- [ ] No writes inside conditionals (CodeCleanup).
- [ ] Fits patterns: `_dispatch_one` already returns `_DispatchOutcome` for every
      other failure (`:434-442`) — match that shape exactly, don't invent one.
- [ ] Note: the log is a `%s`-format string, not structured fields (D6). Keep the
      existing idiom; upgrading to structured logging is out of scope.

### Success Criteria
**Automated:**
- [ ] Red proves `target` is absent from today's log (D6)
- [ ] Red proves `_dispatch_one:443` raises rather than returning an outcome (D6)
- [ ] Green; submit's 503 behavior **unchanged** (this is not a behavior change,
      it is an observability + contract fix)
- [ ] `uv run --extra dev python -m pytest tests/web/test_orphaned_dispatch.py -q`

**Manual:**
- [ ] Confirm no schema/migration was touched — that is the point of B15a
- [ ] Note: `web/server.py:506` (carousels) has the **same** unguarded-attach shape.
      Out of scope for this slice; worth a follow-up bead.

---

## Behavior 16: `dsl_hooks_to_reels` worker contract (steps 1-11) — **BLOCKING (CT-1)**

**STATUS: ✅ DONE (BLOCKING CT-1, ran)** — 10 tests, real ffmpeg; red-at-seam observed on the delivery guard.

### Test Specification
**Given** A1 artifact refs + `source_url`, **when** `dsl_hooks_to_reels` runs,
**then** it executes the research's worker contract steps 1-11 in order and
returns either `download_url` (`http(s)`) or a typed terminal failure.

**Edge cases**: missing artifact ref → typed failure before any render; upload
returns `None` (bucket unset) → terminal `delivery_unavailable`, **not** a
`video_path` success (the deliberate divergence from `app.py:1323`/`:1336`).

**Files touched**: `src/reel_af/app.py`, `tests/test_dsl_hooks_worker_closure.py`.

### TDD Cycle

#### 🔴 Red
```python
async def test_worker_delivers_browser_url(fake_s3, lavfi_mp4_factory, tmp_path):
    _require_ffmpeg()                                    # fail-closed (BLOCKING)
    result = await dsl_hooks_to_reels(
        source_url=A1_SOURCE_URL, composite_ref=..., words_ref=..., hook_ref=..., clip_idx=1,
        fetch_segment=lambda req: lavfi_mp4_factory(...),   # production seam
        uploader=_fake_uploader,                            # production seam
    )
    assert result["download_url"].startswith("https://")
    assert "error" not in result

async def test_worker_without_bucket_is_terminal_delivery_unavailable():
    result = await dsl_hooks_to_reels(..., uploader=lambda *a, **k: None)
    assert result["error"] == A1_DELIVERY_UNAVAILABLE
    assert "download_url" not in result
    assert "video_path" not in result       # must NOT fall back (cf. app.py:1336)
```

#### 🟢 Green
**File**: `src/reel_af/app.py`
```python
@reel.reasoner()
async def dsl_hooks_to_reels(          # NOT reel_dsl_hooks_to_reels — see target-id derivation
    source_url: str, composite_ref: str, words_ref: str, hook_ref: str, clip_idx: int,
    out_dir: str | None = None, *,
    fetch_segment=None, uploader=None,      # injectable seams, mirroring research_to_reel
) -> dict:
    ...
```
Mirrors `research_to_reel`'s keyword-only seam style (`app.py:1202`) and the
`{"error": ...}` return convention — never raises past the reasoner boundary.

#### 🔵 Refactor
- [ ] **Guard clauses** for every bad path before side effects.
- [ ] No duplication: reuse `reel_output_name` (`naming.py:31`) and `upload_reel`.
- [ ] Deep module: the reasoner is a thin orchestrator over existing primitives —
      **no re-implemented render logic**.
- [ ] Named constants for every typed error code.

### Success Criteria
**Automated:**
- [ ] Function name yields exactly `reel-af.reel_dsl_hooks_to_reels`
- [ ] Fail-closed on missing ffmpeg
- [ ] `uv run --extra dev python -m pytest tests/test_dsl_hooks_worker_closure.py -q`

**Manual:**
- [ ] Registered target id confirmed in the node's reasoner listing

---

## Behavior 17: Cross-repo fixture parity (reel-af side) — **BLOCKING**

**STATUS: ✅ DONE (BLOCKING, ran)** — golden generated from a LIVE dsl_hooks_to_reels() call via --regenerate-golden; stable across runs.

> **Classified BLOCKING** (review BLOCKING-5). R5 states that B17 is what keeps the
> CT-1/CT-2 split honest — so it **inherits the classification of what it
> guarantees**. Rev 1 diagnosed the hazard and then failed to gate it: B17 was
> unclassified, had no execution guarantee, and left `REAL_WORKER_RESULT`
> undefined. If `REAL_WORKER_RESULT` were a literal, the parity test would be a
> **tautology comparing two hand-authored constants** — precisely the degradation
> R5 warns about, shipped on day one.

### Test Specification
**Given** A1-shaped `hook-plan.json` + `composite.ts.md` + `transcript.words.json`
fixtures, **when** validated against reel-af DSL models, **then** they compile to
a renderable `FootageReel`; **and** historical `clip-plan.json` / article / topic
inputs are rejected.

**Edge cases**: `hook-plan.json` `schema_version != "1"` → reject;
`transcript.words.json` `schema_version != "1"` → reject (`WordsSidecar`,
`models.py:76-110`).

**Files touched**: `tests/dsl/test_a1_artifact_parity.py`,
`tests/dsl/fixtures/a1_*`, `tests/web/fixtures/dsl_hooks_execution_result.snapshot.json`.

### TDD Cycle

#### 🔴 Red
```python
def test_a1_words_sidecar_validates_against_reel_af_model():
    assert WordsSidecar.model_validate(json.loads(read_fixture("source.words.json")))

def test_historical_clip_plan_is_not_a_dsl_hooks_input():
    with pytest.raises(BadRequest):
        build_submission(TARGET_DSL_HOOKS, {"input": {"clip_plan": "clip-plan.json"}})

async def test_ct2_golden_fixture_matches_real_worker_output(fake_s3, lavfi_mp4_factory, tmp_path):
    """PARITY: the CP body CT-2 replays must equal what the worker ACTUALLY returns.

    REAL_WORKER_RESULT is a LIVE INVOCATION, never a constant — a constant here
    would make this a tautology and silently void the CT-1/CT-2 closure guarantee.
    """
    _require_ffmpeg()                       # fail-closed: B17 is BLOCKING

    real_worker_result = await dsl_hooks_to_reels(     # <- the live call, not a literal
        source_url=A1_SOURCE_URL, composite_ref=..., words_ref=..., hook_ref=..., clip_idx=1,
        fetch_segment=lambda req: lavfi_mp4_factory(...),
        uploader=_fake_uploader,
    )
    golden = json.loads(read_fixture("dsl_hooks_execution_result.snapshot.json"))
    assert golden["result"] == _normalize(real_worker_result)   # strip run_id/timings
```

#### 🟢 Green
Generate the CT-2 golden fixture **from CT-1's real output**. Make regeneration a
**mechanical step, not a discipline** (review BLOCKING-5) — add to the plan and the
test module docstring:

```bash
# Regenerate the CT-1↔CT-2 parity fixture (never hand-edit it):
uv run --extra dev python -m pytest tests/dsl/test_a1_artifact_parity.py \
  --regenerate-golden -q
```
Implement `--regenerate-golden` as a pytest option that writes
`tests/web/fixtures/dsl_hooks_execution_result.snapshot.json` from the live
invocation above.

> **Precedent correction (review BLOCKING-5)**: rev 1 cited
> `tests/web/fixtures/execution_result.snapshot.json` as precedent for generating
> this fixture. **Verified: it has no generator** — it is only ever *read*, by
> `tests/web/test_research_event_contract.py`. It is a **hand-authored** fixture,
> i.e. precedent for exactly the wrong thing. **Do not cite it.** B17 establishes
> the generator pattern here.

#### 🔵 Refactor
- [ ] No duplication: one fixture set shared by CT-1 and CT-2 — the parity seam.
- [ ] `_normalize` strips only non-deterministic fields (`run_id`, `timings_s`);
      it must **not** strip `download_url`/`error` — those are the contract.
- [ ] The regeneration command lives in the test module docstring, so the next
      reader finds it without reading this plan.

### Success Criteria
**Automated:**
- [ ] **Fail-closed** if ffmpeg absent (`_require_ffmpeg()`, never `skipif`) — B17
      executes CT-1's worker, so it inherits CT-1's infra requirement
- [ ] `REAL_WORKER_RESULT` is a live `dsl_hooks_to_reels(...)` return value —
      **grep the test file for a hand-authored dict literal; there must be none**
- [ ] Green; snapshot regenerated via the command above, not hand-edited
- [ ] `uv run --extra dev python -m pytest tests/ -q`

**Manual:**
- [ ] Confirm the golden fixture's git diff only ever changes via regeneration

---

## Risks

- **R1 — RETIRED for Slice A; the real risk moved to B9b (deferred).** Rev 1's R1
  claimed a boundary-spanning cut-in has "no valid representation" in the
  per-segment model. **That was wrong** (review D7): `_relative_window`
  (`overlays.py:307-316`) **clamps** it to each overlapping segment via
  `max()`/`min()`, deterministically — the mapping is *better*-defined than rev 1
  believed. Since Slice A no longer renders overlays (B9b deferred), the residual
  risk in **B9a** is small: it is a pure validation/typing function whose only
  policy decision is rejecting cut-ins outside **every** segment. **The genuine
  risk — double normalization at the overlay render stage — is recorded in full
  under What We're NOT Doing → *Deferred: B9b*, where the work now lives.** Do not
  "solve" it by forcing cut-ins into `finish`'s `image_cutins` path; that path is
  images-only, final-reel-relative, and would re-open gap G7.
- **R2 — Duplicated geometry constants.** `CANVAS_WIDTH/HEIGHT` are declared
  **twice**: `dsl/models.py:28-29` and `render/overlays.py:22-23`. Transition
  primitives are declared **three times**: `models.py:38-52` (`XfadeEffect`),
  `parser.py:20-24` (`XFADE_EFFECTS`), `resolver.py:26-40`
  (`TRANSITION_PRIMITIVES`). Consuming them from a single source is desirable but
  is a **cross-cutting refactor outside this slice**; this plan reuses
  `models.py` and adds no fourth declaration.
- **R3 — `WordsSidecar` strictness.** `start == end` is accepted
  (`models.py:89-92`). Deferred (see What We're NOT Doing). B6 strengthens
  `FootageReel` spans, so a zero-length *segment* is caught even though a
  zero-length *word* is not.
- **R4 — No render timeouts.** The research requires named whole-job and
  per-subprocess timeouts "before production enablement" (research lines 488-491).
  `stitch_footage_reel` has `FFMPEG_TIMEOUT_S` and `download_segments` has
  `DOWNLOAD_TIMEOUT_S` (with a caveat at `footage_stitch.py:100`: "callers own
  timeouts"), but there is **no whole-job bound**. Out of scope; **gates
  production enablement**, not merge.
- **R5 — CT-1/CT-2 split.** The CP is out-of-repo, so no single test spans
  submit→worker→poll. The golden-fixture parity test (B17) is what keeps the two
  spans honest. If that fixture is ever hand-edited instead of regenerated, the
  closure guarantee silently degrades. **Mitigated in rev 2** (review BLOCKING-5):
  B17 is now **BLOCKING**, `REAL_WORKER_RESULT` is a **live invocation** rather
  than a constant, `_require_ffmpeg()` gates it fail-closed, and regeneration is a
  **mechanical command** rather than a discipline. Rev 1 named this risk and then
  left it ungated — the gate is the mitigation.
- **R6 — Deferred work is load-bearing for the product, not just the plan.** Slice
  A ships reels that render fully but **do not burn in cut-ins** (B9b) and an orphan
  path that is **observable but not repairable** (B15b). Both deferrals are correct
  — each rested on a premise the code contradicts — but neither is cosmetic. They
  should be scheduled, not forgotten; each has a follow-up note under What We're NOT
  Doing with the design already worked out.

---

## Implementation Order (revised per review)

1. **B4** — `CompileContext`. Unblocks everything, and safer than rev 1 claimed:
   `compile_composite` has **zero production callers**.
2. **B3** (context-gated) + **B6** — pure, cheap, correctly red.
   **B3 hard-depends on B4** (its Red passes `context=DSL_HOOKS_CONTEXT`).
3. **B2** (re-cut to `composite.py` + `CompositeDoc`) — now a real **defect fix**
   (closes today's silent-drop of malformed markers), not a stub fill-in.
4. **B1, B9a** — fixtures + the pure cut-in mapper.
5. **B7** — characterization.
6. **B8, B16** — finish burn-in + worker (**CT-1 closes**).
7. **B17** (now **BLOCKING**) — parity fixture generated from CT-1's real output.
8. **B10-B13** — web target + fail-closed submit.
9. **B14** (incl. `_poll_response_body`) — delivery policy (**CT-2 closes**).
10. **B15a** — log `target` + guard `_dispatch_one`.
11. ~~**B9b**~~ — **deferred** to a follow-up build (overlay render stage).
12. ~~**B15b**~~ — **deferred**; needs a migration/outage design decision.

Rationale: DSL correctness first (B3/B6/B2 are pure and cheap), then the worker,
then the web boundary. **B14 lands after B16** so the delivery policy is written
against the worker's **real** result shape, not a guess — rev 1's rationale,
preserved. Rev 1 sequenced B4→B3 correctly **by luck of ordering** without naming
the dependency; it is now explicit.

**Hidden dependencies, stated (review):**
- **B3 → B4** — `CompileContext` must exist before B3's Red can be written.
- **B2 → the `CompositeDoc` model** — B2 cannot be confined to `compile.py`.
- **B17 → CT-1 executing → ffmpeg** — hence `_require_ffmpeg()`.
- **B14 → `_poll_response_body`** — not just the reconcile branch.

---

## References

- **Research (implementation contract)**:
  `/home/maceo/Dev/A1_workspace-blueprint/thoughts/searchable/shared/research/2026-07-15-09-03-a1-reel-af-video-pipeline-seams.md`
  — Worker contract lines 255-272; Required Tests lines 511-548; CodeCleanup
  constraints lines 558-580; Target Workflow Closure Map lines 1214-1291.
  Its sibling `-REVIEW.md` is **stale** (reviewed a pre-amendment version); the
  "Implementation Decision and Review Closure" section (lines 52-602) closes all
  six critical issues. Not re-opened here.
- **Base**: `f72adb4` (`silmari-reels-af`, branch `dsl-hooks-target`).
- **Effect-ordering invariant** (preserved): `auth -> authorize -> reject
  forbidden identity -> canonicalize TARGET_DSL_HOOKS -> verify owned artifact
  refs -> insert/reuse row -> dispatch CP -> attach execution id -> owned poll ->
  terminal reconcile` — as implemented at `web/server.py:310-350`.
- **Patterns to follow**: deps dataclass + `default_deps()`
  (`render/finish.py:73-125`); reasoner seams (`app.py:1202`); submit branch
  (`reel_jobs.py:296-349`); override allowlist (`web/tunables.py:115-131`); web
  fakes (`tests/web/conftest.py`); fail-closed ffmpeg closure
  (`tests/test_finish_closure.py:37-39`).
- **Review**: `2026-07-15-12-44-tdd-reel-af-dsl-hooks-target-REVIEW.md` (BlueBay,
  verdict needs-rework). This revision applies all 6 BLOCKING + 6 SHOULD-FIX
  findings; see the Revision Log.
