---
date: 2026-07-21T07:43:07-04:00
reviewer: CoralRaven
repository: silmari-reels-af
branch: reel-af-a1-producer-impl
plan_under_review: thoughts/searchable/shared/plans/2026-07-20-19-04-AF-7nu-tdd-a1-multi-clip-planning.md
scope: pre-implementation review_plan pass; contracts, interfaces, data models, dispatch and storage seams
verdict: needs-targeted-revision
beads: [AF-7nu, silmari-agentfield-system-ndf]
status: complete
---

# Plan Review: AF-7nu A1 Multi-Clip Planning

## Verdict

Needs targeted revision before implementation.

The main architecture is sound. The plan keeps `dsl_hooks_to_reels()` single-reel-per-dispatch,
puts N-clip fan-out in the A1 producer/orchestration layers, requires each render dispatch to pass
the selected clip's own top-level `composite_ref`, preserves publication-time idempotency keys, and
keeps `clip_count=1` backward-compatible.

The revisions needed are narrow but blocking: the new planner dispatch helper's payload shape is
ambiguous against existing call sites that already wrap `{"input": ...}`, and direct
`planner.pipeline.plan()` validation for invalid `clip_count` is implied but not pinned by tests.
There is also one should-fix serializer test gap for non-sequential clip indexes.

## Review Summary

| Category | Status | Issues Found |
|---|---:|---|
| Contracts | Needs revision | 2 critical, 1 warning |
| Interfaces | Needs revision | 1 critical, 1 warning |
| Promises | Ready with warning | 1 warning |
| Data Models | Needs minor revision | 1 warning |
| APIs | Ready | 0 blocking |
| CodeCleanup Gates | Ready | 0 blocking |

## Contract Review

### Well-Defined

- Renderer cardinality is correctly locked. The plan says the renderer stays
  single-reel-per-dispatch and must not return multiple videos (`...multi-clip-planning.md:23-30`,
  `:112-119`, `:489-559`, `:829-830`). This matches current `dsl_hooks_to_reels()`, which loads one
  top-level `composite_ref`, one words ref, one hook plan, selects one clip by `clip_idx`, and
  returns one `download_url` (`src/reel_af/app.py:1599-1745`).
- Per-clip composite ownership is correctly identified. The plan explicitly notes that
  `clip_idx` does not choose the composite because the renderer resolves the top-level
  `composite_ref` before loading the hook clip (`...multi-clip-planning.md:53-56`; current code at
  `src/reel_af/app.py:1651-1656`).
- Publication is pointed at the right seam. Current storage collapses every hook clip to one
  published composite URL (`src/reel_af/storage.py:137-145`, `:207-229`), and Behavior 6 correctly
  requires a local-ref to hosted-URL map for every distinct `clips[*].composite_ref`
  (`...multi-clip-planning.md:353-393`).
- Planner all-or-nothing is defined for gate failures. Behaviors 4 and 5 require compile failure or
  insufficient non-overlapping spans to return a typed planner error and write no partial core
  artifacts (`...multi-clip-planning.md:287-318`, `:320-351`).

### Critical Issues

1. **Dispatch helper payload shape can double-wrap or under-wrap `input`.**

   Evidence: Behavior 8 says the pure helper returns items shaped like
   `{"input": {"source_url", "composite_ref", "words_ref", "hook_ref", "clip_idx"}}`
   (`...multi-clip-planning.md:451-460`), and the grammar repeats that as `RenderDispatchInput`
   (`:804-810`). But existing local dispatch seams are split:

   - `scripts/ingest_source.py::dispatch()` accepts raw `cp_input` and wraps it as
     `json={"input": cp_input}` (`scripts/ingest_source.py:218-219`).
   - Web `build_submission()` builds raw `cp_input` (`web/reel_jobs.py:472-487`), then
     `server.py` wraps it before calling `dispatch_async()` (`web/server.py:393-395`,
     `web/server.py:493-499`).
   - The new plan says to wire `scripts/ingest_source.py` to the helper (`...multi-clip-planning.md:130`,
     `:502-513`) but does not state whether the script keeps using its raw-input `dispatch()`
     function or bypasses it with already-wrapped bodies.

   Impact: an implementation can accidentally send `{"input": {"input": {...}}}` to the control
   plane, or change the helper to raw `cp_input` and fail the planned B8 contract. Either breaks the
   hard per-clip top-level `composite_ref` guarantee at the exact caller-loop seam.

   Required amendment: define one exact dispatch item model before implementation, for example:

   ```python
   DslHookDispatch = {
       "idx": int,
       "idempotency_key": str,
       "target": HOOKS_TARGET,
       "cp_input": {
           "source_url": str,
           "composite_ref": clip["composite_ref"],
           "words_ref": str,
           "hook_ref": str,
           "clip_idx": clip["idx"],
       },
   }
   ```

   Then make wrappers own the `{"input": cp_input}` envelope. If the helper instead returns the
   envelope, state that explicitly and update the script dispatch seam so it does not wrap again.
   Add a script-level red test that records the actual HTTP JSON body and asserts there is exactly
   one `input` layer.

2. **Direct `planner.pipeline.plan()` `clip_count` validation is implied but not tested.**

   Evidence: the plan adds `clip_count` to both `transcript_to_plan()` and
   `planner.pipeline.plan()` (`...multi-clip-planning.md:97-98`), and the grammar defines
   `clip_count` as an integer `>= 1` that is not `bool` (`:754-758`). Behavior 7 pins invalid public
   `transcript_to_plan()` values before transcription/planning/publication (`:402-417`), and only
   says in refactor prose that `plan()` can share the helper (`:430-431`). There is no direct red
   test for `plan(..., clip_count=0)`, `True`, `"2"`, etc. before mining, LLM calls, or writes.

   Impact: direct planner callers, tests, eval harnesses, or future orchestration can bypass the
   public reasoner and observe a different contract from the one in the interface grammar. Invalid
   values could also trigger side effects before failure.

   Required amendment: add
   `tests/planner/test_pipeline.py::test_plan_rejects_invalid_clip_count_before_mining_or_writing`
   or equivalent. Parametrize the same invalid values as Behavior 7, assert no LLM calls and no core
   artifacts, and require a deterministic typed error or exception contract for direct `plan()`.

## Interface Review

### Well-Defined

- `build_hook_plan()` remains backward-compatible by normalizing the current one-span call path into
  a one-item clip spec list (`...multi-clip-planning.md:184-187`). That matches current callers,
  which pass a single resolved span and one `composite_ref` (`src/reel_af/planner/pipeline.py:218-227`).
- `transcript_to_plan()` keeps writer precedence and async-writer support unchanged while adding
  `clip_count` pass-through (`...multi-clip-planning.md:421-431`; current precedence at
  `src/reel_af/app.py:1787-1795`).
- `publish_a1_artifacts()` keeps the existing single-clip core-key contract and extends it for
  per-clip composite keys (`...multi-clip-planning.md:362-393`).

### Warning

- **Hook-plan loading for dispatch is mentioned but not an interface.** The plan says
  `scripts/ingest_source.py` should "read/fetch" `hook-plan.json` after stage 1
  (`...multi-clip-planning.md:130`) and later says the helper can accept parsed dicts and
  file-loaded hook plans (`:486-487`). Published stage-1 results use HTTPS refs, and the current
  artifact resolver already has HTTP-fetch semantics (`src/reel_af/app.py:1563-1586`), but the new
  dispatch module has no named loader/fetcher contract. Add either a small
  `load_hook_plan_for_dispatch(hook_ref, fetch_bytes=...)` helper or an explicit script-only fetch
  path with tests for local path and HTTPS hook refs.

## Promise Review

### Well-Defined

- No batch renderer promise leaks into the plan. Behavior 9 requires N separate calls and no payload
  containing multiple `clip_idx` values (`...multi-clip-planning.md:489-524`).
- Publication promises not to recompute idempotency keys (`...multi-clip-planning.md:88-90`,
  `:118`, `:797-802`, `:825-826`), which matches the existing key being generated in
  `build_hook_plan()` from source URL, index, span, and composite ref
  (`src/reel_af/planner/serialize.py:198-205`, `:368-379`).
- `clip_count=1` compatibility is repeatedly pinned (`...multi-clip-planning.md:75-76`,
  `:184-187`, `:275-285`, `:580-585`).

### Warning

- **Filesystem write atomicity is out of scope but should be named.** Behaviors 4 and 5 cover
  planner gate failures before core writes, which is the important functional contract. Current
  `_write_triple()` writes files sequentially (`src/reel_af/planner/pipeline.py:260-302`), so the
  plan does not guarantee no partial files after an OS write error. If BrownFox intends only
  compile/span-failure all-or-nothing, say so. If true filesystem atomicity is required, add a temp
  directory plus rename plan and a write-failure test.

## Data Model Review

### Warning

- **Sequential clip indexes are required but gap cases are not negative-tested.** Desired state says
  indexes start at `1` (`...multi-clip-planning.md:69`), and the seam contract says indexes are
  unique, positive, and sequential (`:823`). Behavior 2 tests duplicate indexes, overlap,
  unresolved spans, empty `composite_ref`, and `idx < 1` (`:203-234`), but not gaps such as
  `[1, 3]` or `[2, 3]`. Add a red case proving non-sequential indexes are rejected before a plan
  dict is returned.

## API Review

No external renderer API change is proposed. The control-plane target remains
`reel-af.reel_dsl_hooks_to_reels`, and the web DSL-hooks submit path already accepts one
`source_url`, one `composite_ref`, one `words_ref`, one `hook_ref`, and one `clip_idx`
(`web/reel_jobs.py:452-487`). This matches the plan's decision to implement fan-out outside the
renderer.

## CodeCleanup Plan-Hygiene Review

The plan is consistent with the loaded CodeCleanup criteria:

- Effects stay out of renderer conditionals; renderer changes are regression tests only.
- Proposed validation helpers in serializer, planner grouping, publication, and dispatch are pure
  seams before side-effecting writes or CP dispatches.
- Bad paths are stated as guard/fail-closed behavior before render or artifact publication effects.
- New structural values should become named constants: `HOOKS_TARGET`, per-clip composite key
  templates, `PLANNER_MULTI_CLIP_INSUFFICIENT_SPANS`, and the eventual dispatch item field names.

No local CodeCleanup customizations were present at
`~/.claude/SAI/USER/SKILLCUSTOMIZATIONS/CodeCleanup/`.

## Test Coverage Assessment

Behaviors 1-10 are a strong coverage skeleton and hit the right storage, planner, dispatch, and
renderer regression seams. The suite is adequate after these amendments:

```diff
+ Add direct plan() invalid clip_count red test: no LLM calls, no core artifacts.
+ Add dispatch helper/script assertion for exactly one {"input": ...} envelope in the actual CP body.
+ Add dispatch item idempotency-key contract: preserve clip idempotency_key as metadata/header/client_request_id, or explicitly state it is not used for CP idempotency.
+ Add serializer invalid idx gap cases: [1, 3] and [2, 3].
+ Add hook-plan loader/fetch seam tests for local path and HTTPS hook_ref, if scripts/ingest_source.py owns the loop.
```

## Approval Status

- [ ] Ready for implementation
- [x] Needs targeted revision
- [ ] Needs major redesign

The plan should be ready after the targeted dispatch-shape and direct `plan()` validation amendments
are folded in. No issue in this review asks `dsl_hooks_to_reels()` to render multiple videos or
batch clips.
