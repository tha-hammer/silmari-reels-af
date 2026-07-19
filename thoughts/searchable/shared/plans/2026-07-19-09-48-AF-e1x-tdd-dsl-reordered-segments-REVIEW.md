---
date: 2026-07-19T00:00:00-04:00
reviewer: Codex
plan_under_review: thoughts/searchable/shared/plans/2026-07-19-09-48-AF-e1x-tdd-dsl-reordered-segments.md
repository: silmari-reels-af
branch: reel-af-a1-producer-impl
scope: pre-implementation plan review; contracts, interfaces, closure tests, code-hygiene forecast
verdict: needs-targeted-revision
tags: [review, tdd-plan, reel-af, dsl, AF-e1x, source-intervals, crossfade]
---

# Plan Review: DSL Reordered Source Segments Plus AF-e1x

## Verdict

**Needs targeted revision before implementation.** The plan's central architecture is sound:
it correctly separates composite order from source-time order, keeps exact raw-span collapse
fail-closed, adds a compiler-owned final no-replay guard, and leaves `FootageReel` plus the
stitcher on composite adjacency. Those conclusions match the current code:
`compile_composite()` still calls the raw guard, clamp, segment builder, joins, and transition
builder in order (`src/reel_af/dsl/compile.py:85-112`); `FootageReel` validates transition
adjacency and duration budgets (`src/reel_af/dsl/models.py:311-376`); the stitcher trims
absolute source intervals while folding transitions in segment order
(`src/reel_af/render/footage_stitch.py:409-500`).

The required revision is narrow but blocking: **the plan does not specify how marker-boundary
metadata survives inserted segments and joins.** The proposed Green for joins changes
`_should_merge()` only, but current `_apply_joins()` ignores the marker's target boundary when
choosing a pair to merge and scans for the first mergeable adjacent same-source pair
(`src/reel_af/dsl/compile.py:606-624`). Current transition markers are also computed before joins
and then consumed after joins without remapping (`src/reel_af/dsl/compile.py:474-488`,
`src/reel_af/dsl/compile.py:108-112`). A two-segment join test can pass while multi-segment
DSL marker semantics remain wrong.

## Review Summary

| Category | Status | Issues Found |
|---|---:|---|
| Contracts | Needs revision | 1 critical, 1 warning |
| Interfaces | Needs revision | 2 warnings |
| Promises | Needs revision | 2 warnings |
| Data Models | Acceptable | 0 critical |
| APIs | Acceptable | No external API change |
| Closure Tests | Needs revision | 1 critical, 1 warning |
| Code-Hygiene Forecast | Needs revision | 3 forecast warnings |

## Contract Review

### Well-Defined

- **Raw aligner contract is correct.** Plan lines 67-117 and Seam 1 narrow
  `_verify_injective_spans()` to exact duplicate span rejection. This matches the real split in
  current code: duplicate detection at `src/reel_af/dsl/compile.py:320-327`, chronology rejection
  at `src/reel_af/dsl/compile.py:328-334`.
- **Compiler-owned no-replay policy is in the right layer.** Plan lines 152-180 add
  `_verify_no_source_interval_overlap()` after joins and before transitions. Keeping this out of
  `FootageReel` is correct because `validate_renderable()` currently validates renderability but
  not cross-segment source reuse (`src/reel_af/dsl/models.py:524-581`).
- **Transition contract remains correctly composite-order based.** Plan lines 580-623 match
  `FootageReel` and stitcher behavior: transition count/index adjacency is `(i, i + 1)`, and
  non-color xfade duration is strictly less than both adjacent segment durations
  (`src/reel_af/dsl/models.py:321-376`, `src/reel_af/render/footage_stitch.py:837-852`).

### Missing or Unclear

- **CRITICAL - Join marker boundary ownership is not specified.** Behavior 6 says to change
  `_should_merge()` so reordered source-time pairs are refused (plan lines 212-240). That is
  necessary but insufficient. Current `_apply_joins()` collects `(before_segment_index, Join)`
  markers, but when applying each marker it scans `joined` from the beginning and merges the first
  adjacent same-source pair that `_should_merge()` accepts; `orig_before_idx` is not used to target
  the specific DSL boundary (`src/reel_af/dsl/compile.py:606-624`). A plan implementation could pass
  `test_join_refuses_reordered_source_time_pair` with two segments and still merge the wrong pair in
  a three-or-more-segment reel. The final overlap guard will not catch a wrong-but-non-overlapping
  merge, so this is a contract gap, not just a test gap.

- **WARNING - Floating-point overlap tolerance is unspecified.** Seam 3 allows equality at a boundary
  and rejects positive overlap (plan lines 562-578), but it does not define an epsilon. The research
  closure adapter uses `epsilon=1e-6`; the compiler plan should either adopt a named constant such as
  `SOURCE_INTERVAL_EPSILON_S` or explicitly require exact comparisons. Without a named policy,
  tiny float drift can become a false `SOURCE_TIME_OVERLAP` or an inconsistent test expectation.

## Interface Review

### Well-Defined

- **Diagnostic model change is local.** Adding `SOURCE_TIME_OVERLAP` to the `DiagnosticCode`
  `Literal` is the correct interface point (`src/reel_af/dsl/models.py:175-191`).
- **Final verifier signature is close.** `_verify_no_source_interval_overlap(segments, diagnostics)`
  can enumerate output indexes from `segments`, ignore `BlackSegment`, and group by each
  `SourceSegment.source_url` (`src/reel_af/dsl/models.py:266-284`).

### Missing or Unclear

- **WARNING - Normalizer source grouping is described but not representable by the current aligned
  object.** Plan lines 139-142 say `_normalize_source_intervals(aligned, diagnostics)` should group
  by source key, and Seam 1 includes `source_url` on aligned segments. Current `_AlignedSegment`
  has only `seg`, `start_s`, `end_s`, `text`, and `seg_id`
  (`src/reel_af/dsl/compile.py:265-273`). In the current single-source compiler that is fine, but
  the helper interface should make the single-source assumption explicit: either pass
  `source.source_url` into `_normalize_source_intervals()` or define the aligned interval adapter as
  source-less until final `SourceSegment` validation.

- **WARNING - Normalization failure return is underspecified.** Plan line 145 says "Return a boolean
  or diagnostic result." The compile pipeline needs a concrete contract equivalent to the raw guard:
  `True` means an error diagnostic was emitted and compile returns `_error_result_from(diagnostics)`,
  or `False` means continue. Leaving this open invites inconsistent call-site handling around
  `src/reel_af/dsl/compile.py:94-100`.

## Promise Review

### Well-Defined

- **Composite-order preservation is explicit.** Behaviors 3, 7, and Seam 2 all require the returned
  aligned stream to keep original list order while source-time projection is internal.
- **Fail-closed promises are in the right order.** The plan calls the final source-overlap verifier
  after `_apply_joins()` and before `_build_transitions()` (plan lines 171-173), matching the point
  where all emitted `SourceSegment`s exist and before render-facing effects are built.
- **Renderer promise is conservative.** Behavior 8 says renderer failures after compiler changes are
  evidence of invalid compiler output, not a reason to loosen stitcher validation (plan lines 300-304).

### Missing or Unclear

- **WARNING - Multiple extend markers need an ordering promise.** Plan lines 200-205 say source-time
  neighbors are computed before applying extends. Current `_apply_extends()` mutates intervals while
  iterating marker attachments (`src/reel_af/dsl/compile.py:362-401`). The plan should say whether
  source-neighbor bounds are computed once from raw aligned intervals, recomputed after each mutation,
  or computed from immutable base intervals. The likely safe promise is: compute immutable source-time
  neighbor bounds from the pre-extend aligned set, apply all extends within those bounds, then run
  normalization plus final overlap verification.

- **WARNING - Join force semantics need one explicit sentence.** Plan lines 231-236 forbid
  out-of-order same-source min/max joins even for `[join force]`, while preserving existing force
  behavior for intentionally supported cases. State that same-source `force` may override the gap
  limit only after the forward-order check has passed. That prevents a future reader from treating
  `force` as "ignore all source-time policy."

## Data Model Review

### Well-Defined

- **No schema migration is implied.** `FootageReel` does not need new fields; source intervals already
  live on `SourceSegment` (`src/reel_af/dsl/models.py:266-274`), and transition semantics already live
  on `Transition` (`src/reel_af/dsl/models.py:287-302`).
- **New diagnostic code is enough.** The plan's `SOURCE_TIME_OVERLAP` code is a type-surface addition
  only, consistent with existing diagnostic strings in `DiagnosticCode` (`src/reel_af/dsl/models.py:175-191`).
- **Persisted version pins are not touched.** The plan avoids changing `schema_version`/`dsl_version`,
  which is correct because this is a compiler policy change, not a rendered reel schema change
  (`src/reel_af/dsl/models.py:83-86`, `src/reel_af/dsl/models.py:311-319`).

## API Review

No external API changes are proposed. The public compile API remains `compile_composite(doc, words,
source, ..., context=None)` and the render closure remains `plan_pairwise_stitch()` /
`build_footage_filtergraph()`. That is appropriate for this plan.

## Closure-Test Review

### Well-Defined

- **The closure uses the real stitch planning seam.** Behavior 8 invokes `plan_pairwise_stitch()` or
  `build_footage_filtergraph()`, which validates renderability and segment assets, then derives real
  trim and fold steps (`src/reel_af/render/footage_stitch.py:409-500`).
- **The closure asserts source-time and composite-time projections independently.** Plan lines
  294-298 require no sorted source-time overlap, total duration parity, and out-of-source-order trim
  starts while preserving composite order.

### Missing or Unclear

- **CRITICAL - Add a closure or focused test for marker remapping through joins and transitions.**
  Behavior 6's proposed test has only two adjacent segments. It cannot catch the current
  `_apply_joins()` behavior that ignores `orig_before_idx`, nor can it catch stale `trans_markers`
  after a successful join. Add a test with at least three source segments and a `[join]` marker on
  the second boundary, where the first boundary is mergeable but unmarked. The expected result should
  prove only the marked boundary is considered. If a successful join is still supported, add a
  transition-marker assertion after the join so `(before_index, after_index)` still maps to the
  post-join composite boundary.

- **WARNING - The closure does not actually observe `acrossfade`.** The "Then" says xfade/acrossfade
  is emitted (plan lines 285-287), but the Red assertions only check `fold_step.effect` (plan lines
  294-298). `plan_pairwise_stitch()` exposes `audio_fade`, but actual `acrossfade` text is emitted
  by `_fold_filter()` / `build_footage_filtergraph()` (`src/reel_af/render/footage_stitch.py:563-572`).
  Either call `build_footage_filtergraph()` and assert `xfade=transition=...` plus `acrossfade`, or
  use `_fold_cmd()` in the existing stitch graph test style.

## Code-Hygiene Forecast

- **Control expressions:** The plan should keep overlap checks as named boolean predicates, not nested
  interval arithmetic inside conditionals. Prefer `has_positive_overlap = left_end > right_start + eps`
  and then a flat guard that emits the diagnostic. This follows the CodeCleanup rule that conditions
  should ask simple questions rather than do opaque work.
- **No mutation in control expressions:** `_normalize_source_intervals()` will mutate `cur.end_s`.
  Keep that mutation as a statement inside a plainly guarded block; do not combine it with assignment
  expressions or helper calls in the condition.
- **Never nesting:** The new helpers are good seams, but avoid a single polymorphic
  `_source_intervals(aligned_or_segments)` with nested `isinstance` branches. A cleaner shape is two
  small adapters, one for `_AlignedSegment` and one for final `SourceSegment`, feeding a shared pure
  interval scan.
- **Named constants:** Name the float tolerance, sort-key tuple construction, and diagnostic context
  keys once. Do not scatter `"source_url"`, `"left_segment_id"`, `"right_segment_id"`, `"overlap_s"`,
  or `1e-6` across helper and test code.
- **Maintainability recovery:** This plan moves the source-time policy into compiler helpers instead
  of weakening render validation. That is the right foundation-level fix. The one place at risk of
  becoming another patch layer is marker-boundary remapping; make it explicit now rather than adding
  special cases after tests expose stale indexes.

## Critical Issues

1. **Join marker targeting and post-join marker remapping are unspecified.**
   - Impact: a two-segment test can pass while real multi-segment composites merge the wrong pair or
     attach transitions to stale boundaries after a successful join.
   - Required amendment: target joins by the marker's original boundary, maintain an original-to-output
     boundary map through inserts/joins, and remap or rebuild transition markers after joins.

2. **Closure tests do not cover the marker-boundary contract.**
   - Impact: the highest-risk interface in this change, DSL marker boundary -> emitted segment boundary,
     remains untested.
   - Required amendment: add a three-segment join test and a successful-join transition-remap assertion
     if successful joins remain supported.

## Suggested Plan Amendments

```diff
# Behavior 6: Joins Do Not Bridge Reordered Source Clips
+ Before changing _should_merge(), define how a Join marker's original
+ before_segment_index maps to the current output boundary after inserts.
+ _apply_joins must evaluate only the pair at the marked boundary, not the first
+ mergeable same-source adjacent pair in the output list.
+ If a join succeeds, rebuild/remap transition markers against the post-join
+ segment list before _build_transitions().

# Behavior 6 Red
+ Add a three-segment test:
+   composite boundaries: 0|1 and 1|2
+   only boundary 1|2 has [join]
+   boundary 0|1 is mergeable
+   assert boundary 0|1 is not merged and only the marked boundary is evaluated.

# Behavior 3 / Seam 2
~ Specify helper contract:
~   _normalize_source_intervals(aligned, source_url, diagnostics) -> bool
~ or explicitly document that aligned normalization is single-source and source-less.
+ Define SOURCE_INTERVAL_EPSILON_S once if compiler overlap uses tolerance.

# Behavior 8
~ Observe emitted xfade/acrossfade via build_footage_filtergraph() or _fold_cmd(),
~ not only fold_step.effect.
```

## Approval Status

**Needs targeted revision.** After the join/marker-boundary amendment and the small helper-interface
clarifications above, the plan should be ready for implementation. No code changes were made during
this review.
