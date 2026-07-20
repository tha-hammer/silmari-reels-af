---
date: 2026-07-20T18:24:30-04:00
researcher: tha-hammer
git_commit: 51fb8ec4d797cf651bea4b27d60b3c9880a4ab50
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "A1 reel planner → reel-af render reasoner: plan-file production and the plan+source→video handoff"
tags: [research, codebase, reel-af, a1-planner, dsl-hooks, render, artifacts, object-storage, handoff]
status: complete
last_updated: 2026-07-20
last_updated_by: tha-hammer
---

# Research: A1 reel planner → reel-af render reasoner (plan files + source → video)

**Date**: 2026-07-20T18:24:30-04:00
**Researcher**: tha-hammer
**Git Commit**: 51fb8ec4d797cf651bea4b27d60b3c9880a4ab50
**Branch**: reel-af-a1-producer-impl
**Repository**: silmari-reels-af

## Research Question

There is a planning/reasoning step referred to as "A1" that plans the reel (or multiple
reels). The plan files and the source video are sent to the reel-af reasoner to create
the actual video. Example plan files: `/tmp/reel-af/coherence-joinfix-run/`. Document how
this works as it exists today: what "A1" produces, the schema of the plan files, and how
those files plus the source video are consumed to render the reel.

## Summary

The system is two distinct AgentField reasoners connected by a set of plan-file artifacts:

1. **A1 planner** — the reasoner `transcript_to_plan` (`src/reel_af/app.py:1748`), which
   transcribes a source, runs the `reel_af.planner` pipeline (`src/reel_af/planner/pipeline.py:68`
   `plan()`), and writes **eight** artifacts into a run directory. Only three are the
   "core" hand-off contract: `composite.ts.md`, `transcript.words.json`, and
   `hook-plan.json`. The other five (`mined-candidates.json`, `accepted-candidates.json`,
   `strategy.json`, `blueprint.json`, `script-coherence.json`) are diagnostic sidecars.
   The planner produces **data only** — it never renders media.

2. **reel-af render reasoner** — `dsl_hooks_to_reels` (`src/reel_af/app.py:1599`), which
   takes `source_url` + the three core refs (`composite_ref`, `words_ref`, `hook_ref`) +
   a `clip_idx`, compiles the composite timeline into a `FootageReel` cut list, downloads
   and cuts the real source footage, stitches it with ffmpeg (joins + transitions), runs a
   finish pass (banner/captions/generated image cut-ins), uploads the mp4, and returns a
   browser-deliverable `download_url`.

The two are decoupled by object storage: `transcript_to_plan` calls
`publish_a1_artifacts` (`src/reel_af/storage.py:179`) to upload the three core artifacts to
`plans/{run_id}/` and rewrite the returned refs to presigned HTTPS URLs; the render
reasoner (running as a separate execution, potentially on a different node) fetches those
URLs. "Multiple reels from one source" is expressed as multiple entries in
`hook-plan.json`'s `clips[]` array; each is rendered by a separate `dsl_hooks_to_reels`
call with a different `clip_idx` (`src/reel_af/app.py:1589` `_load_hook_clip`). In the
example run and in the current `build_hook_plan`, exactly one clip is emitted.

The same DSL compiler (`compile_composite`, `src/reel_af/dsl/compile.py:64`) that the
render reasoner uses is also run by the planner as a **planning-time gate**
(`src/reel_af/planner/pipeline.py:203`), so compile refusals (e.g. `JOIN_REFUSED`) fail at
planning and trigger a bounded LLM repair rather than surfacing only at render time.

## Detailed Findings

### The example plan files (`/tmp/reel-af/coherence-joinfix-run/`)

The example directory is a `transcript_to_plan` run for YouTube id `wPcKNuUG3NM`. Its
eight files map 1:1 to the pipeline stages:

| File | Size | Stage that wrote it | Shape (observed) |
|---|---|---|---|
| `transcript.words.json` | 387 KB | S0 transcribe | `{schema_version, segments[], words[{start,end,w}]}` — 4373 words |
| `mined-candidates.json` | 34 KB | S1 mine | list of 63 raw candidates `{quote, emotion, is_claim, payoff_worthy, value_score, source_window_*}` |
| `accepted-candidates.json` | 55 KB | verbatim-accept / arrange pool | list of 80 `{candidate_id, quote, start_s, end_s, word_range, quality, value_score, ...}` |
| `strategy.json` | 3.6 KB | S2 strategize | `{arc{promise,thread,completion_criteria,required/optional/excluded_candidate_ids}, hook, duration_range_s, duration_policy, template_="ProblemAgitateSolve", cta, engagement_primary}` |
| `blueprint.json` | 15 KB | S3 arrange | `strategy` fields + `beats[]` (each `{candidate_id, role, span_quote, max_len_s, interrupt_out, cutin?, engagement?, completion_criterion_ids, rationale}`) + `loop`, `cap_rationale`, `completion_rationale` |
| `script-coherence.json` | 6.2 KB | coherence pass | `{coherent, overall_rationale, transitions[{from_beat_index,to_beat_index,verdict,fix_action,why_present,rationale}]}` |
| `composite.ts.md` | 1.4 KB | serialize/compile | timecoded lines + `[join]` / `[trans <effect> <dur>]` markers |
| `hook-plan.json` | 1.1 KB | compile | `{schema_version, workflow:"dsl_hooks", source_url, source_id, model, duration_bounds_s, clips[{idx, start_s, end_s, composite_ref, hook, idea, title, excerpt, idempotency_key, target, cut_ins[]}]}` |

The example `composite.ts.md` shows the DSL surface directly — timecoded verbatim beats
separated by marker lines:
```
00:06:01.842  The reality of all of this is the AIs are stupid.
[join]
00:06:06.185  And so you either pay now or you pay later.
...
00:06:19.914  And a real engineer comes in and says, yeah, you need to start over from scratch.
[trans fadeblack 0.6]
00:25:11.938  If you want to improve your results, you have to force the AI to do a couple of things...
```
The `[trans fadeblack 0.6]` marker is the single deliberate "problem → solve" pivot; the
`~1128s` source jump it spans is documented in the paired `script-coherence.json`
transition rationale as the only hard cut in the reel.

The example `hook-plan.json` has one clip with
`idempotency_key = "a1:wPcKNuUG3NM:98e4e3e2f36e:clip:1"` and
`target = "reel-af.reel_dsl_hooks_to_reels"`.

### A1 planner: `transcript_to_plan` (`src/reel_af/app.py:1748-1798`)

- Signature (`app.py:1749-1758`): `transcript_to_plan(source_url, register="educational",
  target_duration_bounds_s=None, out_dir=None, *, llm=None, transcribe=None,
  artifact_writer=None)`. Duration input is a **min/max bounds dict**, never a scalar target.
- Guards `source_url` is browser-deliverable, else `{"error": DSL_HOOKS_ERROR_INVALID_SOURCE_URL}`
  (`app.py:1765-1766`).
- `run_id = uuid.uuid4().hex[:12]` (`app.py:1772`); work dir = `out_dir` or
  `runs_dir("transcript-to-plan", run_id)` (`app.py:1773`, `src/reel_af/planner/paths.py:33-50`).
  Output root precedence: explicit `out_dir` → `REEL_AF_OUTPUT_ROOT` env →
  `cfg.output_root` (`render/config/planner.json`) → `"resources"` (`paths.py:15-30`).
- Transcribes via `reel_af.planner.ingest.transcribe` (`app.py:1774-1777`), then calls
  `reel_af.planner.pipeline.plan(...)` (`app.py:1779-1786`).
- On success, invokes the default `artifact_writer` =
  `reel_af.storage.publish_a1_artifacts(result, run_id=run_id)` (`app.py:1787-1795`).
- Never raises: any exception → `{"error": DSL_HOOKS_ERROR_ARTIFACT_UNAVAILABLE, "detail": ...}`
  (`app.py:1797-1798`).

### Planner pipeline stages (`src/reel_af/planner/pipeline.py:68-257`)

`plan()` is a mine → verbatim-accept → strategize → arrange → script-coherence →
retention-lint → real-DSL-compile-gate → serialize loop. Attempt budget is
`cfg.max_repair_passes + max_coherence_repairs + 1` (`pipeline.py:124`).

| Stage | Function (file:line) | Artifact | Notes |
|---|---|---|---|
| S0 transcribe | `ingest.transcribe` (`planner/ingest.py:257`), ASR chain `planner/transcribe.py:745` | `transcript.words.json` | `WordsSidecar` — word timings + segments |
| S1 mine (windowed) | loop `pipeline.py:90-97` → `llm.mine` (`planner/llm.py:383`) → BAML `MineCandidates` (`baml_src/mine.baml:1`) | `mined-candidates.json` | windows built by `_transcript_windows` (`pipeline.py:342`); per-window cap `_limit_candidate_spans` (`pipeline.py:422`); window metadata `_with_window_metadata` (`pipeline.py:428`) |
| verbatim-accept / diversity cap | `enforce_verbatim` (`planner/verbatim.py:45`), aligns each candidate to word timings via `reel_af.dsl.aligner.align`, keeps ≥ `cfg.verbatim_floor`; `_cap_candidates_with_source_diversity` (`pipeline.py:451`) | `accepted-candidates.json` | actually the **contextual arrange pool** (`arrange_candidates`, `pipeline.py:234`), which adds `ctx_`-prefixed bridge candidates via `contextual_candidate_pool` (`planner/script_coherence.py:44`) |
| S2 strategize | `pipeline.py:111` → `llm.strategize` (`llm.py:395`) → BAML `StrategizeReel` (`baml_src/strategize.baml:1`) | `strategy.json` | `ReelStrategy`: arc, hook, `duration_range_s`, `duration_policy`, `template_`, cta |
| S3 arrange (repair loop) | `pipeline.py:127-238` → `llm.arrange` (`llm.py:411`) → BAML `ArrangeReel` (`baml_src/arrange.baml:1`); beats resolved to source time by `resolve_timecodes` (`serialize.py:58`) | `blueprint.json` | `ReelBlueprint`: `beats[]`, `arc`, `hook`, `loop`, `cta`, `completion_rationale`, `cap_rationale` |
| script-coherence + bounded repair | `pipeline.py:155-178` → `llm.check_script_coherence` (`llm.py:439`) → BAML `CheckScriptCoherence` (`baml_src/script_coherence.baml:1`); repairs capped at `MAX_SCRIPT_COHERENCE_REPAIR_PASSES=2` (`pipeline.py:54`) | `script-coherence.json` | `ScriptCoherenceReport`; failure → `PLANNER_SCRIPT_COHERENCE_FAILED` |
| retention lint R1–R12 | `lint_blueprint` (`planner/lint.py:32`); R7 content-length `_lint_r7` (`lint.py:232`) → `estimate_blueprint_duration_s` (`lint.py:316`) + `validate_arc_completion` (`lint.py:339`) | — (inline diagnostics only) | R2 register cadence lookup `cfg.r2_cadence_s` (`lint.py:119`) |
| real DSL-hooks compile GATE | `serialize_composite` (`serialize.py:145`) then `_compile_render_composite` (`pipeline.py:620`) → `compile_composite(... delivery_required=True)` at `pipeline.py:203` | `composite.ts.md` | compile error → `_render_compile_repair_hint` (`pipeline.py:633`, special-cases `JOIN_REFUSED`) or terminal `PLANNER_RENDER_COMPILE_FAILED` |
| build hook-plan | `build_hook_plan` (`serialize.py:170`) at `pipeline.py:219-227` | `hook-plan.json` | all eight written together by `_write_triple` (`pipeline.py:260-302`) |

**Register** (`Register = Literal["entertainment","educational","b2b"]`,
`planner/models.py:41`): passed as a string into the mine prompt (`baml_src/mine.baml:6`);
its concrete deterministic effect is the R2 cadence ceiling
(`entertainment:3.0 / educational:5.0 / b2b:9.0`, `render/config/planner.json:61-65`,
consumed at `lint.py:119`). Mining few-shots are fixed regardless of register.

**Content-driven length** (no scalar target): bounds → `DurationPolicy`
(`_duration_policy`, `pipeline.py:536`), `ArcPlan` + `DurationRange` proposed by the LLM
(`baml_src/types.baml:174-187`), enforced deterministically by
`validate_arc_completion` (`lint.py:339`) and `_lint_r7` (`lint.py:232`): the effective cap
is a hard gate, advisory min/max are soft warnings.

### Artifact publication / handoff (`src/reel_af/storage.py:179-249`)

- Core triple constant `_CORE_A1_ARTIFACTS` (`storage.py:23-27`) =
  `composite_ref→composite.ts.md`, `words_ref→transcript.words.json`,
  `hook_ref→hook-plan.json`. Sidecars `_A1_SIDECAR_REF_KEYS` (`storage.py:28-36`) are popped
  from the published dict (`storage.py:247-248`) — never uploaded.
- No bucket (`REEL_BUCKET_NAME` unset) → no-op passthrough, refs stay local (`storage.py:194-196`).
- All-or-error: reads all three local files (`_read_core_artifact`, `storage.py:105`), any
  missing/empty ref or `OSError` aborts before upload; uploads under fixed keys
  `plans/{run_id}/{composite.ts.md,transcript.words.json,hook-plan.json}`
  (`storage.py:207,216,230`), presigns each (`_put_and_presign_artifact`, `storage.py:155`);
  `_hosted_http_url` (`storage.py:98`) guarantees the returned refs are http(s), never a bare key.
- TTL `REEL_ARTIFACT_TTL_S` → falls back to `REEL_DELIVERY_TTL_S` → default 86400s
  (`storage.py:22,43-48,204`).
- Rewrites each `clip["composite_ref"]` inside the hook-plan body to the published
  composite URL (`_hook_body_with_published_composite`, `storage.py:137-152`) and asserts no
  local path/`file://` remains, but never recomputes `idempotency_key`.
- `transcript_to_plan` wires this as the default writer keyed on the same `run_id`
  (`app.py:1787-1795`), so `plans/{run_id}/` matches the planner run id.

**Web boundary** (`web/reel_jobs.py`): `TARGET_DSL_HOOKS = "reel-af.reel_dsl_hooks_to_reels"`
(`reel_jobs.py:30`); `DSL_HOOKS_ALLOWED_INPUT_KEYS` = `{source_url, composite_ref, words_ref,
hook_ref, clip_idx, overrides}` (`reel_jobs.py:106-109`); each ref must start with
`a1://` / `http://` / `https://` (`_validated_artifact_ref`, `reel_jobs.py:215-227`), never a
filesystem path. `cp_input` forwarded to the control plane is
`{source_url, composite_ref, words_ref, hook_ref, clip_idx}` (`reel_jobs.py:472-474`).

### Plan-file schemas — where each is built

| Artifact | Builder (file:line) | Key fields |
|---|---|---|
| `composite.ts.md` | `serialize_composite` (`serialize.py:145-167`) | `f"{HH:MM:SS.mmm}  {quote}"` per beat + `interrupt_to_marker_text` (`serialize.py:115`): `[join]`, `[insert black {dur}]`, `[trans {effect} {dur}]` |
| `hook-plan.json` | `build_hook_plan` (`serialize.py:170-229`) | top: `schema_version, workflow, source_url, source_id, model, duration_bounds_s, clips[]`; clip: `idx, title, idea, hook, start_s, end_s, excerpt, composite_ref, target, idempotency_key, cut_ins[]` |
| `idempotency_key` | `_idempotency_key` (`serialize.py:368-379`) | `a1:{source_id}:{sha1(f"{source_url}|{idx}|{start_s:.3f}|{end_s:.3f}|{composite_ref}")[:12]}:clip:{idx}` |
| `cut_ins[]` | `_cut_in_payload` (`serialize.py:298`) → `CutInSpec` (`dsl/models.py:221-245`) | `type: zoom|visual, at_s, until_s, line?, image_prompt?(req if visual), zoom_focus` |
| `transcript.words.json` | passthrough `WordsSidecar` (`dsl/models.py:108-142`) | `schema_version, words[{w,start,end,conf?}], segments[{text,start_s,end_s}]` |
| `blueprint.json` / `strategy.json` / `script-coherence.json` | BAML types `ReelBlueprint` / `ReelStrategy` / `ScriptCoherenceReport` (`baml_src/types.baml:242-287`), re-exported `planner/models.py:9,29-33` | see stage table |
| `mined-candidates.json` / `accepted-candidates.json` | `CandidateSpan` / `PlannerCandidate` (BAML types) | window-annotated / verbatim-aligned candidates |

### reel-af render reasoner: `dsl_hooks_to_reels` (`src/reel_af/app.py:1599-1745`)

Consume → render → deliver, terminal-on-error at every step (reasoners never raise):

1. Guard `source_url` browser-deliverable (`app.py:1645`, `_is_browser_deliverable_url`
   `app.py:1503`) else `invalid_source_url`.
2. Resolve/fetch the three refs (`_resolve_artifact_ref`, `app.py:1563`; HTTP GET
   `_default_artifact_fetch`, `app.py:1550`) — http(s) fetched to work dir, `a1://` resolved
   against `A1_ARTIFACTS_BASE_ENV`, else local path (`app.py:1651-1653`).
3. Parse `.ts.md` (`read_composite_file`, `app.py:1654` → `dsl/composite.py`), words
   (`load_words`, `app.py:1655`), select the clip by `clip_idx` (`_load_hook_clip`,
   `app.py:1589-1595,1656`). Any resolution error → `dsl_artifact_unavailable`.
4. Build `CompileContext(workflow="dsl_hooks", source_url, video_id=clip["source_id"],
   delivery_required=True, cut_ins=[CutInSpec…])` (`app.py:1664-1672`).
5. `compile_composite(...)` (`app.py:1673` → `dsl/compile.py:64`) → `FootageReel`;
   compile error / `None` plan → `dsl_compile_failed` (`app.py:1678`); `validate_renderable`
   → `dsl_compile_failed` (`app.py:1685`).
6. `map_cut_ins` validates the A1 hook-plan cut-ins against compiled spans but does **not**
   render them (B9a; `app.py:1691-1698` → `dsl/cutins.py:80`); invalid → `dsl_cutin_invalid`.
7. `download_segments` → per-segment fetch (`_default_segment_fetch`, `app.py:1520`) which
   calls `render/hooks.py` `download_source` + `cut_source_span`; `stitch_footage_reel`
   pairwise ffmpeg fold (`render/footage_stitch.py:358`); `finish_reel` burns banner /
   captions / **newly-generated** image cut-ins (`render/finish.py:204`). Render error →
   `dsl_render_failed` (`app.py:1719`).
8. Upload (`upload_reel`, `storage.py:65`) → require browser-deliverable `download_url`,
   else terminal `delivery_unavailable` (`app.py:1728-1734`). Success returns
   `{download_url, run_id, target_workflow, clip_idx, segment_count, cut_in_count,
   duration_s, source}` (`app.py:1736-1745`).

**Multiple reels from one source**: `hook-plan.json` carries `clips[]`; `_load_hook_clip`
(`app.py:1589-1595`) linear-scans for `clip["idx"] == clip_idx` (default 1). Re-invoking the
reasoner with the same three refs + a different `clip_idx` renders a different clip; the
compile step still compiles the whole composite/words pair, and the clip supplies only
`video_id` + `cut_ins` context. Current `build_hook_plan` emits exactly one clip.

**Download / proxy** (`render/hooks.py`): `download_source` (`hooks.py:334`) dispatches —
`_is_direct_media_url` (`hooks.py:305`, host `generic` + path ends
`.mp4/.mkv/.webm/.mov/.m4v` `hooks.py:50`) → `download_direct_source` (`hooks.py:313`, plain
`urllib` GET, **no proxy/cookies/yt-dlp**); otherwise `download_crisp_source` (`hooks.py:253`,
yt-dlp with `_resolve_proxy_from_env` `YTDLP_PROXY_URL` `hooks.py:185` and cookies
`hooks.py:191`). The proxy is consulted only on the yt-dlp path.

### The DSL-hooks compiler (`src/reel_af/dsl/compile.py:64-163`)

`compile_composite(doc, words, source, *, context)` → `CompileResult{status, plan:
FootageReel|None, diagnostics[]}`. Pipeline order (per its docstring `compile.py:1-15`):
parse marker loci → `_align_segments` to word timings (`compile.py:332`) → injective-span
guard (`compile.py:354`) → `_apply_extends` (`compile.py:513`) → `_build_segment_list` +
`[insert black]` (`compile.py:560`) → `_apply_joins` (`compile.py:824`) → rebuild indexes →
`_build_transitions` (`compile.py:964`) → `FootageReel(...)` + `validate_renderable`
(`compile.py:137-160`, `dsl/models.py:526`).

- **Grammar**: tokenizer `read_composite` (`dsl/composite.py:96`), marker parser
  `parse_marker` (`dsl/parser.py:36`) → AST `Insert/Find/Extend/Join/Trans` (`dsl/ast.py:30-92`).
  Effect vocabulary `XFADE_EFFECTS` (`parser.py:20`): `dissolve, smoothleft/right/up/down,
  hblur, circleopen, radial, pixelize, fadeblack, fadewhite, fade, none`;
  `FADE_TO_COLOR_EFFECTS = {fade, fadeblack, fadewhite}` (`dsl/models.py:77`).
- **`FootageReel`** (`dsl/models.py:313-378`): `segments[SourceSegment|BlackSegment]`,
  `transitions[before_index, after_index, effect, duration_s, audio_fade]`, `duration_s`;
  validators enforce transitions == segments-1, adjacency, xfade `0 < dur < min(adjacent)`,
  fade-to-color adjacent-segment length, and derived-duration match within 0.15s.
- **Refusal codes** `DiagnosticCode` (`dsl/models.py:175-193`): `JOIN_REFUSED`,
  `UNMATCHED_SEGMENT`, `SEGMENT_SPAN_COLLAPSE`, `SOURCE_TIME_OVERLAP`, `INVALID_TRANSITION`,
  `NON_RENDERABLE_REEL`, `UNSUPPORTED_INSERT/FIND`, `EMPTY_COMPOSITE`, etc. `JOIN_REFUSED`
  (`_join_refused`, `compile.py:802`) fires on non-adjacent boundary, non-source pair,
  non-forward source-time, or source gap > `JOIN_GAP_LIMIT_S=600.0s` (unless `[join
  confirmed|force]`). `delivery_unavailable` (`dsl/models.py:54`) is an error CODE raised only
  by the render reasoner (`app.py:1734`), not by the compiler.
- **Planner-side gate**: `_compile_render_composite` (`pipeline.py:620`) runs the identical
  compiler with `delivery_required=True` during arrange (`pipeline.py:203`); the render
  reasoner runs it again at render time (`app.py:1673`).

## Code References

- `src/reel_af/app.py:1748` — `transcript_to_plan` (A1 planner reasoner)
- `src/reel_af/app.py:1599` — `dsl_hooks_to_reels` (render reasoner)
- `src/reel_af/app.py:1589` — `_load_hook_clip` (clip_idx → clip; multi-reel selector)
- `src/reel_af/app.py:1520` — `_default_segment_fetch` (per-segment download + cut)
- `src/reel_af/app.py:1736` — render reasoner success return contract
- `src/reel_af/planner/pipeline.py:68` — `plan()` orchestrator
- `src/reel_af/planner/pipeline.py:203` — real DSL-compile planning gate
- `src/reel_af/planner/pipeline.py:260` — `_write_triple` (writes all 8 artifacts)
- `src/reel_af/planner/serialize.py:145` — `serialize_composite` (builds composite.ts.md)
- `src/reel_af/planner/serialize.py:170` — `build_hook_plan` (builds hook-plan.json)
- `src/reel_af/planner/serialize.py:368` — `_idempotency_key`
- `src/reel_af/planner/lint.py:339` — `validate_arc_completion` (R7 content-length)
- `src/reel_af/storage.py:179` — `publish_a1_artifacts`
- `src/reel_af/storage.py:65` — `upload_reel`
- `src/reel_af/dsl/compile.py:64` — `compile_composite`
- `src/reel_af/dsl/composite.py:96` — `read_composite` (.ts.md tokenizer)
- `src/reel_af/dsl/parser.py:36` — `parse_marker` (marker grammar)
- `src/reel_af/dsl/models.py:313` — `FootageReel` (ComposedPlan)
- `src/reel_af/render/footage_stitch.py:358` — `stitch_footage_reel` (pairwise ffmpeg fold)
- `src/reel_af/render/finish.py:204` — `finish_reel` (banner/captions/image cut-ins)
- `src/reel_af/render/hooks.py:334` — `download_source` (proxy/direct dispatch)
- `web/reel_jobs.py:106` — `DSL_HOOKS_ALLOWED_INPUT_KEYS`

## Architecture Documentation

- **Two reasoners, one artifact contract.** The plan/render split is intentional:
  `transcript_to_plan` is data-only and delivery-agnostic; `dsl_hooks_to_reels` owns all
  media. They communicate strictly through the three core refs.
- **Object storage is the decoupling seam.** `publish_a1_artifacts` presigns the three core
  artifacts so a separate render execution (different node/pod) can fetch them; local dev
  with no bucket keeps local-path refs and co-locates the two calls.
- **One compiler, two call sites.** `compile_composite` is the single source of truth for
  what is renderable; running it as a planning gate shifts render failures left.
- **Deterministic guardrails around LLM stages.** Every BAML call is followed by
  contract checks (`planner/llm.py`) and the retention lint (`planner/lint.py`); arc
  completion and content-length are enforced in Python, not left to the model.
- **Delivery-required asymmetry.** `dsl_hooks_to_reels` treats a missing browser URL as
  terminal (`delivery_unavailable`), unlike fail-soft producers; the web poll enforces the
  same for this target only.

## Workflow Closure Map

Behavior mapped (matching the research question — "plan files and the source video are
sent to the reel-af reasoner to create the actual video"): **Given a source video URL and
its published A1 plan artifacts, reel-af renders and delivers a browser-deliverable reel
`download_url`.**

Production operation chain:

```
A1 plan artifacts in object storage (plans/{run_id}/composite.ts.md, transcript.words.json, hook-plan.json)
  -> [object-storage boundary; presigned HTTPS refs]
  -> dsl_hooks_to_reels reasoner (control-plane execution): resolve refs -> compile_composite -> download+cut source -> stitch -> finish -> upload_reel
  -> download_url (browser-deliverable https) returned in the execution result / web poll
```

Per-edge evidence:

- **Producer of the source store**: `publish_a1_artifacts` (`src/reel_af/storage.py:179-249`)
  uploads to `plans/{run_id}/` and returns presigned refs. (Sibling behavior:
  `transcript_to_plan` `app.py:1748` is the entrypoint that produces this store.)
- **Consumer/entrypoint**: `dsl_hooks_to_reels` (`src/reel_af/app.py:1599`), production-called
  via the control plane (`web/reel_jobs.py:472` builds `cp_input`;
  `web/control_plane.py:86` `dispatch_async` POSTs `/api/v1/execute/async/reel-af.reel_dsl_hooks_to_reels`).
  Registration: `@reel.reasoner()` decorator (`app.py:1598`); router mounted
  after all reasoners (`app.py:1839`).
- **Ref resolution across the boundary**: `_resolve_artifact_ref` (`app.py:1563-1586`) +
  `_default_artifact_fetch` HTTP GET (`app.py:1550-1560`).
- **Read/observable**: the reasoner return `download_url` (`app.py:1737`); at the web tier,
  `result.download_url` surfaced by the poll boundary (`web/server.py:227-241`, per the
  object-storage-delivery plan).
- **Data contract**: refs must be `a1://`/`http(s)` (`web/reel_jobs.py:215-227`);
  `hook-plan.json` must contain a `clips[]` element with `idx == clip_idx`
  (`app.py:1589-1595`); `download_url` must pass `_is_browser_deliverable_url`
  (`app.py:1503-1508,1728`).
- **Error behavior**: every stage returns a terminal `{"error": ...}` dict rather than
  raising (`dsl_artifact_unavailable`, `dsl_compile_failed`, `dsl_cutin_invalid`,
  `dsl_render_failed`, `delivery_unavailable`); `app.py:1659,1678,1697,1719,1734`.
- **Tests exercising this edge without bypassing it**: `tests/web/test_dsl_hooks_poll.py`,
  `tests/web/test_dsl_hooks_submit.py`, `tests/dsl/test_dsl_hooks_stitch.py`,
  `tests/dsl/test_a1_artifact_parity.py`, `tests/test_dsl_hooks_finish_closure.py`.

Labels / depth:
- `plan_artifacts` (depth 0, SOURCE store) — `production-called` (written by
  `publish_a1_artifacts`).
- `dsl_hooks_to_reels` (depth 1, entrypoint) — `production-called` (control-plane dispatch).
- `download_url` (depth 2, OBSERVABLE) — `production-called` (reasoner return / web poll).
- `highest_new_connector`: none — this research documents existing code; no node is
  added/changed.

### ClosureMap (structured — derive() input)

```json
{
  "behavior": "Given a source video URL and its published A1 plan artifacts, reel-af renders and delivers a browser-deliverable reel download_url.",
  "git_commit": "51fb8ec4d797cf651bea4b27d60b3c9880a4ab50",
  "repo": "/home/maceo/ntm_Dev/reel-af-a1-producer-impl/silmari-reels-af",
  "nodes": [
    { "id": "a1_plan_artifacts", "module": "src/reel_af/storage.py (publish_a1_artifacts) / object-storage plans/{run_id}/", "is_entrypoint": false, "adds_or_changes": false, "read_path": null, "seedable_store": "A1 plan artifacts: composite.ts.md + transcript.words.json + hook-plan.json (local dir, or object storage plans/{run_id}/ when REEL_BUCKET_NAME set)" },
    { "id": "dsl_hooks_to_reels", "module": "src/reel_af/app.py:1599 (@reel.reasoner reel-af.reel_dsl_hooks_to_reels)", "is_entrypoint": true, "adds_or_changes": false, "read_path": null, "seedable_store": null },
    { "id": "reel_download_url", "module": "src/reel_af/app.py:1736 reasoner return download_url", "is_entrypoint": false, "adds_or_changes": false, "read_path": "dsl_hooks_to_reels(...)['download_url'] (src/reel_af/app.py:1737)", "seedable_store": null }
  ],
  "edges": [
    { "is_async": false, "cross_boundary": true, "driver": null },
    { "is_async": false, "cross_boundary": false, "driver": null }
  ]
}
```

Notes on the map: the edge from `a1_plan_artifacts` to `dsl_hooks_to_reels` is
`cross_boundary` (object storage → agent process) but modeled `is_async: false` because the
reasoner fetches the refs synchronously when invoked (`_default_artifact_fetch`,
`app.py:1550`). In full production the render execution is itself dispatched asynchronously
via the control plane (`web/control_plane.py:86`), and the A1 plan artifacts are produced by
a separate upstream execution (`transcript_to_plan`); those are documented as sibling
behaviors rather than folded into this single seedable→observable chain.

### Closure adapter (staged proposal — `2026-07-20-18-24-a1-planner-and-reel-af-render-handoff.closure-adapter.py`)

Staged read-only as a sibling file; not wired into the repo. Speaks the 7-op contract
`apps/closure-oracle` talks to. No async edge → no `/drive` op.

```python
"""Closure adapter (STAGED PROPOSAL — not wired into the repo).
Derived from the ClosureMap for: source + A1 plan artifacts -> delivered reel download_url.
Pin: 51fb8ec4d797cf651bea4b27d60b3c9880a4ab50.
Promote into silmari-reels-af and complete each TODO(promote) before use.
Speaks the 7-op contract apps/closure-oracle already talks to (mock_adapter.py).
"""
import http.server, json, sys
ASYNC_EDGES = []                          # no async edge in this map
CONNECTOR = {e: True for e in ASYNC_EDGES}
SINK = []                                 # Phase-0 /seed_sink target

def handle(op, p):
    if op == "/reset":         SINK.clear(); CONNECTOR.update({e: True for e in ASYNC_EDGES}); return {"ok": True}
    if op == "/set_connector": CONNECTOR[p["edge"]] = p["enabled"]; return {"ok": True}
    if op == "/seed_sink":     SINK.append(p["value"]); return {"ok": True}
    if op == "/seed":
        # TODO(promote): write composite.ts.md + transcript.words.json + hook-plan.json into a
        # run dir (or object storage plans/{run_id}/) from p["data"].
        # Source store = A1 plan artifacts (src/reel_af/storage.py:179 publish_a1_artifacts;
        # writers src/reel_af/planner/serialize.py:145 serialize_composite / :170 build_hook_plan).
        return {"ok": True}
    if op == "/trigger":
        # TODO(promote): await reel_af.app.dsl_hooks_to_reels(
        #     source_url=p["args"]["source_url"], composite_ref=..., words_ref=..., hook_ref=...,
        #     clip_idx=p["args"].get("clip_idx", 1)) -> capture result["download_url"]  (src/reel_af/app.py:1599)
        return {"ok": True}
    if op == "/drive":
        if not CONNECTOR.get(p["edge"], True): return {"ok": True}  # oracle disabled = red-at-seam
        # no async edge in this map
        return {"ok": True}
    if op == "/observe":
        # TODO(promote): return json.dumps(result["download_url"]) from the /trigger execution
        #   (src/reel_af/app.py:1737); browser-deliverable per _is_browser_deliverable_url (app.py:1503)
        return {"ok": True, "value": json.dumps(SINK)}
    return {"ok": False, "error": "unknown op"}

class Hn(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        out = json.dumps(handle(self.path, json.loads(self.rfile.read(n) or "{}"))).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Hn).serve_forever()
```

## Historical Context (from thoughts/)

- `thoughts/shared/plans/2026-07-18-17-21-tdd-reel-af-planner-baml-backend.md` — the A1
  producer real-BAML-backend TDD plan (the origin of the mine/strategize/arrange stages).
- `thoughts/shared/plans/2026-07-19-reel-af-content-driven-length.md` (+ `-REVIEW`,
  `-system-map`) — AF-ezg, the removal of the scalar target duration and the ArcPlan /
  DurationPolicy / `validate_arc_completion` design.
- `thoughts/shared/plans/2026-07-20-reel-af-object-storage-delivery.md` (+ `-REVIEW`) —
  AF-egx, `publish_a1_artifacts` and the mp4 delivery contract.
- `thoughts/shared/plans/2026-07-15-12-44-tdd-reel-af-dsl-hooks-target.md` (+ `-REVIEW`) —
  Slice A, the original `dsl_hooks_to_reels` target and the composite/hook-plan contract.
- `thoughts/shared/plans/2026-07-19-09-48-AF-e1x-tdd-dsl-reordered-segments.md` (+ `-REVIEW`)
  — reordered source segments in the compiler.
- `thoughts/shared/research/2026-07-20-reel-af-upload-delivery-{code,infra}.md` — the
  delivery-path research behind AF-egx.
- `thoughts/shared/handoffs/general/2026-07-19_11-44-37_reel-af-baml-planner-script-quality.md`
  and `.../2026-07-20_15-55-03_reel-af-signed-url-ingestion-process.md` — the two most recent
  handoffs (A1 end-to-end works; then shipped-to-prod + ingestion gap).
- Spec: `specs/reels-planner.a1-producer.spec.md` — the A1 producer spec, located at
  repo-root `specs/` **outside** the `silmari-reels-af` git repo (one level up, in
  `reel-af-a1-producer-impl/`). A copy of the same filename also appears untracked in the
  `silmari-agentfield-system` repo; which is authoritative is unconfirmed.

## Related Research

- `thoughts/shared/research/2026-07-19-reel-af-content-driven-length-research.md`
- `thoughts/shared/research/2026-07-19-reel-af-output-path-{sota,code-seams,migrate-research}.md`
- `thoughts/shared/research/2026-07-19-09-48-AF-e1x-dsl-reordered-segments.md`

## Open Questions

- **Multi-clip planning**: `hook-plan.json` supports `clips[]` and `clip_idx` selects one,
  but current `build_hook_plan` (`serialize.py:170`) emits a single clip. Where (if
  anywhere) a plan producing N clips is generated today was not located in this pass.
- **Spec authority**: two copies of `reels-planner.a1-producer.spec.md` exist (repo-root
  `specs/` outside the git repo, and untracked in `silmari-agentfield-system`); the
  canonical/versioned home is unresolved (also noted in the 2026-07-20 handoff).
- **A1 hook-plan cut-ins vs. finish cut-ins**: the hook-plan `cut_ins[]` are validated but
  not rendered (B9a; `dsl/cutins.py`), while `finish_reel` generates its own image cut-ins
  from the final transcript. The intended future unification (B9b) was not researched here.
