---
date: 2026-07-19T09:48:14-04:00
researcher: Codex/BronzeFinch
git_commit: a202a6ed2505fb76597114161cca9062428cdc08
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "reel-af DSL reordered source segments and AF-e1x source-time overlap"
tags: [research, codebase, reel-af, dsl, compiler, crossfade, AF-e1x]
status: complete
related_beads: [AF-e1x, AF-9li]
---

# Research: DSL Reordered Source Segments And AF-e1x

## Research Question

How does the reel-af DSL composite compiler enforce source-segment ordering and prevent
source-time overlap/double-play, and how do crossfade transitions depend on segment adjacency
and duration? Map monotonicity and injectivity guards, the AF-e1x overlap clamp, transition/xfade
mechanics, and the FootageReel invariants. Identify exactly what must change to allow
out-of-chronological-order source clips while keeping injectivity, preventing any source moment
playing twice, and keeping crossfades renderable.

## Summary

The blocker is a compiler policy, not a renderer limitation. `compile_composite()` aligns DSL
segments in composite order, then calls `_verify_injective_spans()` before extends, clamping, segment
construction, joins, transition construction, and `FootageReel` validation
(`src/reel_af/dsl/compile.py:85-134`). `_verify_injective_spans()` currently mixes two separate
invariants: exact duplicate span rejection, which protects against aligner collapse, and
non-decreasing `start_s`, which rejects narrative reordering (`src/reel_af/dsl/compile.py:298-335`).
Only the chronology half should be relaxed.

AF-e1x is already represented in the compiler as `_clamp_contiguous_spans()`. That function fixes
caption-cue overruns by trimming each segment end to the next segment start, but it iterates adjacent
pairs in composite order and its docstring explicitly relies on previously verified non-decreasing
starts (`src/reel_af/dsl/compile.py:338-351`). Once source clips may be presented out of chronological
order, that clamp must become source-time interval normalization plus validation, independent of
composite order.

Crossfades are tied to composite adjacency and duration, not source chronology. `FootageReel`
requires `len(transitions) == len(segments) - 1`, transition indexes `(i, i + 1)`, enough duration in
the two adjacent composite segments, and derived duration math that subtracts non-fade-to-color xfade
durations (`src/reel_af/dsl/models.py:311-376`). The stitcher trims each source segment by absolute
source time, then folds transitions in composite order (`src/reel_af/render/footage_stitch.py:227-244`,
`src/reel_af/render/footage_stitch.py:250-333`). That path is already compatible with reordered source
times if the compiler emits valid non-overlapping intervals and transitions with enough footage.

## Compile Path

| Stage | Current behavior | Evidence |
|---|---|---|
| Parse and align | `compile_composite()` builds aligned segments from the composite document and transcript sidecar. | `src/reel_af/dsl/compile.py:85-87` |
| Raw span guard | `_verify_injective_spans()` runs before extends and joins. | `src/reel_af/dsl/compile.py:89-92` |
| Extend | `_apply_extends()` can grow head/tail spans, currently bounded by neighboring aligned entries in composite order. | `src/reel_af/dsl/compile.py:388-401` |
| AF-e1x clamp | `_clamp_contiguous_spans()` trims adjacent composite-order spans so cue overruns do not replay. | `src/reel_af/dsl/compile.py:96-98`, `src/reel_af/dsl/compile.py:338-351` |
| Segment construction | `_build_segment_list()` emits `SourceSegment` entries in composite order using the final aligned `start_s/end_s`. | `src/reel_af/dsl/compile.py:451-459` |
| Inserts | Relevant inserts receive an `exclude_ranges` list from aligned spans and append their own ranges, but there is no final overlap verifier for all output segments. | `src/reel_af/dsl/compile.py:446-540` |
| Joins | `_apply_joins()` may merge adjacent output `SourceSegment`s and creates a min/max source interval. | `src/reel_af/dsl/compile.py:601-640` |
| Transitions | `_build_transitions()` creates exactly one transition per adjacent composite boundary and checks xfade duration against adjacent segment durations. | `src/reel_af/dsl/compile.py:675-735` |
| Reel validation | `FootageReel` and `validate_renderable()` validate adjacency, duration math, finite positive spans, allowed primitives, and version pins. | `src/reel_af/dsl/models.py:311-376`, `src/reel_af/dsl/models.py:524-581` |

## Findings

### 1. Injectivity and monotonicity are currently coupled

`_verify_injective_spans()` first computes exact `(start_s, end_s)` pairs and errors if the set is
smaller than the segment list (`context={"kind": "injectivity"}`), then separately errors when an
aligned segment starts before the prior aligned segment (`context={"kind": "monotonicity"}`)
(`src/reel_af/dsl/compile.py:320-334`). The duplicate-span guard is the real aligner-collapse
protection and should remain fail-closed. The monotonicity branch is the narrative-reordering blocker.

The current unit tests encode this older contract directly: non-monotonic starts are expected to
return `SEGMENT_SPAN_COLLAPSE`, while increasing spans are accepted
(`tests/dsl/test_compile_injectivity.py:34-50`). The older collapse regression also asserts strictly
increasing source starts, not just distinct source spans (`tests/dsl/test_compile_collapse_regression.py:17-31`).
Those tests must be narrowed to duplicate/injective-collapse behavior and supplemented with reordered
positive cases.

### 2. AF-e1x clamp is order-dependent

`_clamp_contiguous_spans()` is the current AF-e1x mitigation. It handles cue spans such as
`[(412, 440), (432, 458), (452, 460)]` by trimming the first two ends to `432` and `452`, respectively,
which prevents seam replay in chronological composite order (`tests/dsl/test_compile_clamp_contiguous.py:31-38`).
The end-to-end fixture test checks that compiled source spans tile without overlap and that total
covered source time equals the outer span (`tests/dsl/test_compile_clamp_contiguous.py:55-68`).

That behavior depends on adjacent composite order. The implementation only compares `cur` and `nxt`
from `zip(aligned, aligned[1:])`, and its safety condition says starts are already non-decreasing
(`src/reel_af/dsl/compile.py:338-351`). If composite order becomes `[later, earlier, middle]`,
composite adjacency is no longer the right way to find the next source-time boundary. The clamp must
operate on source-time sorted intervals while preserving the original composite order of the output
segments.

### 3. Extends and joins contain hidden chronology assumptions

`_apply_extends()` uses `aligned.index(a)` and clamps a tail extend against `aligned[idx + 1].start_s`,
or a head extend against `aligned[idx - 1].end_s` (`src/reel_af/dsl/compile.py:388-401`). Those are
composite neighbors today. With reordered clips, they are not necessarily source-time neighbors, so an
extend can be clamped by the wrong segment or allowed into a different source interval unless the
compiler computes source-time neighbors or verifies intervals after extension.

Joins are another risk. `_apply_joins()` merges adjacent output source segments into one span using
`start_s=min(left.start_s, right.start_s)` and `end_s=max(left.end_s, right.end_s)`
(`src/reel_af/dsl/compile.py:630-636`). `_should_merge()` computes `gap = right.start_s - left.end_s`
and only refuses when the gap is greater than `JOIN_GAP_LIMIT_S` (`src/reel_af/dsl/compile.py:643-672`).
If a later clip is followed by an earlier source-time clip, `gap` is negative, so the current code can
allow a merge that bridges a large source interval and changes content. Reordered source clips need a
join rule that only merges source-forward, non-overlapping, near-contiguous intervals.

### 4. FootageReel validates composite adjacency, not source chronology

`SourceSegment` only has field-level constraints for `start_s >= 0` and `end_s > 0`
(`src/reel_af/dsl/models.py:266-274`). `validate_renderable()` later requires each rendered source
segment to have finite positive range via `_validate_segment_renderable()`, but it does not check
overlap between different `SourceSegment`s (`src/reel_af/dsl/models.py:524-581`).

`FootageReel` transition invariants are purely composite-index based: the transition list length must
equal `segments - 1`, each transition must be `(i, i + 1)`, each adjacent pair must have enough
duration for the transition effect, and derived reel duration must match the segment/transition math
(`src/reel_af/dsl/models.py:321-376`). That is the correct invariant to preserve for narrative order.
Out-of-chronological source times do not require non-adjacent transition indexes.

### 5. The stitcher is already source-order independent

`build_footage_filtergraph()` iterates reel segments in composite order. For each source segment it
computes a trim start from absolute segment source time minus the downloaded asset's source start,
then emits video trim and audio `atrim` filters for that one segment (`src/reel_af/render/footage_stitch.py:208-244`).
The pairwise planner mirrors the same per-segment trim math in pure code
(`src/reel_af/render/footage_stitch.py:409-452`).

Transitions are folded in composite order. The filtergraph path checks indexes `(idx - 1, idx)`,
calculates the next segment duration, and either concatenates, fade-to-colors, or applies `xfade` and
optional `acrossfade` (`src/reel_af/render/footage_stitch.py:250-333`). The pairwise path mirrors that
and verifies the final derived stitch duration (`src/reel_af/render/footage_stitch.py:454-500`). The
explicit transition index validator rejects non-adjacent/order-breaking transition references, and the
xfade validator requires `0 < duration < min(left, right)` (`src/reel_af/render/footage_stitch.py:837-852`).

Existing stitcher tests assert the important properties: trim filters are emitted, `xfade` offsets are
based on accumulated composite duration, `acrossfade` appears when audio fade is true, hard audio cut
uses `atrim=duration=...`, and too-long transitions fail
(`tests/dsl/test_footage_stitch_graph.py:48-79`, `tests/dsl/test_footage_stitch_graph.py:113-235`).

### 6. Live repro confirms the blocker

The scratchpad triple at
`/tmp/claude-1000/-home-maceo-ntm-Dev-silmari-agentfield-system/cf661f86-3cd1-4f10-ab08-aa4479a53651/scratchpad/e2e_out/`
contains a composite ordered for narrative: about `00:06:01`, then `00:19:01`, then `00:16:10`, then
`00:26:53`, then `00:05:30`, then `00:06:25`. A local compile run against the scratchpad
`composite.ts.md` and `transcript.words.json` returned:

```text
error
False
SEGMENT_SPAN_COLLAPSE error {'kind': 'monotonicity'} segment 2 aligns before segment 1 (970.9644375 < 1141.5281875)
```

This matches the suspected failure: the real triple aligns to distinct source spans, but fails because
segment 2 starts earlier in source time than segment 1.

## Required Changes

1. Split the raw aligner guard into "distinct spans only" and remove chronology from that invariant.
   Duplicate spans still fail closed as `SEGMENT_SPAN_COLLAPSE` because they indicate aligner collapse.
2. Replace `_clamp_contiguous_spans()` with order-independent source interval normalization:
   preserve the aligned list's composite order, but sort source intervals by `start_s` within each
   source when deciding where cue-end overruns must be trimmed.
3. Add a final source interval overlap verifier for emitted `SourceSegment`s, grouped by `source_url`
   and sorted by source time. This catches overlap introduced by extends, joins, or inserted clips.
4. Change extend bounds to use nearest source-time neighbors, or apply interval normalization and
   fail-closed overlap validation after extends. Tail/head extension must never grow into another
   source interval just because that interval is not a composite neighbor.
5. Change join rules so adjacent composite clips are only merged when they are same-source and
   source-forward/non-overlapping. A reordered boundary must not become one min/max source range.
6. Keep `FootageReel` transition adjacency unchanged. Crossfades remain between adjacent composite
   segments; the compiler only needs to ensure each adjacent segment still has enough duration after
   interval normalization.
7. Add closure tests that compile a reordered composite and run it through the real stitch planning
   path (`plan_pairwise_stitch()` or `build_footage_filtergraph()`), asserting both no source-time
   overlap and renderable xfade/acrossfade output.

## Workflow Closure Map

```json
{
  "behavior": "A composite DSL document can present source clips out of chronological source order while compiling to a renderable FootageReel with no source-time overlap and valid adjacent crossfades.",
  "repo_head": "a202a6ed2505fb76597114161cca9062428cdc08",
  "entrypoint": "reel_af.dsl.compile.compile_composite",
  "nodes": [
    {
      "id": "composite_doc_and_words",
      "kind": "input",
      "module": "reel_af.dsl.composite",
      "read_path": "read_composite + load_words",
      "adds_or_changes": false
    },
    {
      "id": "compile_pipeline",
      "kind": "transform",
      "module": "reel_af.dsl.compile",
      "read_path": "compile_composite",
      "adds_or_changes": true
    },
    {
      "id": "source_interval_policy",
      "kind": "guard",
      "module": "reel_af.dsl.compile",
      "read_path": "_verify_injective_spans + _clamp_contiguous_spans",
      "adds_or_changes": true
    },
    {
      "id": "footage_reel_contract",
      "kind": "contract",
      "module": "reel_af.dsl.models",
      "read_path": "FootageReel + validate_renderable",
      "adds_or_changes": false
    },
    {
      "id": "stitch_plan",
      "kind": "observable",
      "module": "reel_af.render.footage_stitch",
      "read_path": "plan_pairwise_stitch or build_footage_filtergraph",
      "adds_or_changes": false
    }
  ],
  "edges": [
    {
      "from": "composite_doc_and_words",
      "to": "compile_pipeline",
      "mode": "sync"
    },
    {
      "from": "compile_pipeline",
      "to": "source_interval_policy",
      "mode": "sync"
    },
    {
      "from": "source_interval_policy",
      "to": "footage_reel_contract",
      "mode": "sync"
    },
    {
      "from": "footage_reel_contract",
      "to": "stitch_plan",
      "mode": "sync"
    }
  ],
  "highest_new_connector": "reel_af.dsl.compile source interval policy",
  "observable_read": "reel_af.render.footage_stitch.plan_pairwise_stitch",
  "closure_test": "tests/dsl/test_compile_reordered_segments.py::test_reordered_composite_compiles_without_source_overlap_and_plans_xfade",
  "external_drivers": [],
  "notes": [
    "The renderer remains unchanged; it observes the compiler contract through FootageReel.",
    "The closure must fail on current HEAD because monotonicity rejects reordered starts.",
    "After implementation, the closure must prove no source moment is represented by more than one emitted SourceSegment."
  ]
}
```

## Verification Notes

The ResearchSemgrep helper scripts referenced by the workflow were not present in this checkout
(`rg --files -g 'verify-citations.ts' -g 'closure-map.ts'` found no matches). Citation verification
was performed with numbered local reads of the cited source/test ranges. The live repro compile command
was run locally against the `/tmp/.../scratchpad/e2e_out` triple and reproduced the monotonicity
diagnostic shown above.
