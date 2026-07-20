# Plan: AF-ezg Content-Driven Reel Length

Date: 2026-07-19
Worker: CyanCompass
Status: PLAN ONLY, enhanced after review, awaiting BrownFox GO
Research: `thoughts/searchable/shared/research/2026-07-19-reel-af-content-driven-length-research.md`
Governing bead: AF-ezg

## Fixed Requirement

The planner has latitude with length. Content is the arbiter: the reel is exactly as long as the content justifies to make a coherent, complete narrative. It is not fit to a timecode.

Operational consequences:

- Do not pad to hit a requested duration.
- Do not truncate a coherent narrative to fit a short requested duration.
- Keep a default soft cap of 180s because reach is curtailed past 3 minutes.
- Do not exceed about 180s unless `target_duration_bounds_s.max_s > 180` explicitly overrides the cap.
- Preserve R1/R2/R3/R8 as local per-beat rules at any total length.

## Current State Summary

The current source has three short-form forces:

- Spec Section 6 R7 says `15-30s completion / 30-60s watch-time`.
- `strategize.baml` asks for a scalar `target_duration_s` and prompts for 18-55s tight bands.
- `arrange.baml` has no dynamic beat budget and short examples.

The deterministic Python path does not force a target fit. It serializes the arranged beats. That means the implementation should change mine/strategize/arrange contracts and add policy validation, not bolt on padding.

## Desired Semantics

### Duration Policy

Introduce a single resolved duration policy at the planner boundary:

```python
DurationPolicy(
    soft_cap_s=180.0,
    effective_cap_s=180.0 or target_duration_bounds_s.max_s when max_s > 180,
    advisory_min_s=target_duration_bounds_s.min_s | None,
    advisory_max_s=target_duration_bounds_s.max_s | None,
    cap_overridden=target_duration_bounds_s.max_s > 180,
)
```

Rules:

- `effective_cap_s` is the only total-duration limit arrange must not exceed.
- `advisory_min_s` never causes padding.
- `advisory_max_s <= 180` never causes incoherent truncation.
- `advisory_max_s > 180` is the explicit override that permits output above the default 180s cap, bounded by that override.
- If a complete arc would exceed `effective_cap_s`, arrange must produce the strongest coherent cut under cap and explain what was omitted.
- If no coherent under-cap cut exists, the planner should fail with a typed R7 diagnostic instead of emitting an incoherent or padded reel.

### Completion Contract

Strategize decides what "complete" means for the chosen arc before arrange selects beats. A complete arc has:

- a hook promise;
- the minimum context required to understand that promise;
- enough proof/mechanism/example material to make the payoff credible;
- a payoff that directly resolves the hook;
- an R8 loop echo that does not replay the identical hook source interval;
- one earned R9 share/send cue when the transcript supports it.

Arrange then includes every beat required by that completion contract, plus optional supporting beats only when they improve coherence or proof.

### Review Enhancements Incorporated

The plan review artifact is:

`thoughts/searchable/shared/plans/2026-07-19-reel-af-content-driven-length-REVIEW.md`

The implementation plan below incorporates these review decisions:

- Strategize defines completion, but the pipeline must verify it with `validate_arc_completion()` after arrange and before writing sidecars.
- Every beat must be justified by a completion criterion, a named bridge between criteria, or non-duplicative proof for the payoff. Longer output is acceptable only when the extra beats earn their place.
- R7 duration checks must use the same duration surface as serialization/render: a pre-write resolved estimate from deterministic beat/transition/interrupt config, plus post-compile/eval evidence from compiled duration.
- `target_duration_s` is removed from LLM decision contracts. Any transition scalar is derived after arrange as eval or legacy metadata only and must never be passed back into Strategize or Arrange.
- R3 is measured by local or sectional pacing groups for long reels, not by strict global monotonic duration.
- Whole-source mining must have explicit window, token, candidate-packing, and timeout budgets.

## Proposed Spec Revision Text

Replace spec Section 6 R7 with this text:

```markdown
| R7 | content-driven length with 3-minute soft cap | complete coherent arc; default cap 180s unless explicitly overridden | total duration | high |

R7 Content-driven length:
The A1 producer does not fit a reel to a target timecode. S2 declares the intended narrative arc and a justified duration range/latitude. S3 includes every narratively required beat until the hook promise is resolved, the payoff lands, and the R8 loop can close cleanly. The reel may be shorter than a requested lower bound when the content is already complete; never pad with filler. The reel may exceed a short requested upper preference when truncation would break coherence, but it must stay under the default 180s soft cap unless the caller explicitly overrides the cap with target_duration_bounds_s.max_s > 180.

If the complete arc would exceed the active cap, S3 must choose the strongest coherent cut under that cap by dropping optional branches, repeated examples, and lower-value support. It must not arbitrarily cut at a timestamp or drop the payoff/loop. If no coherent under-cap cut exists, fail planning with an R7 diagnostic instead of emitting an incoherent reel.

R1/R2/R3/R8 remain local craft rules at any total length: hook resolves locally, beats longer than register cadence need visible/listenable change, the back half tightens toward payoff, and the final span echoes the hook idea from a distinct source interval.
```

Also update the blueprint example in Section 4 away from `target_duration_s: 28` and a five-beat-only shape. The new example should show `duration_policy`, `duration_range_s`, `arc`, `completion_criteria`, and 10-20 beats when the source material justifies it.

## Proposed BAML/Data Contract Changes

### New/Changed Types

Add types in `baml_src/types.baml`:

```baml
class DurationPolicy {
  soft_cap_s float
  effective_cap_s float
  advisory_min_s float?
  advisory_max_s float?
  cap_overridden bool
}

class DurationRange {
  min_s float
  max_s float
  rationale string
}

class ArcPlan {
  promise string
  thread string
  completion_criteria string[]
  required_candidate_ids string[]
  optional_candidate_ids string[]?
  excluded_candidate_ids string[]?
}
```

Change `ReelStrategy`:

```baml
class ReelStrategy {
  template_ Template
  duration_range_s DurationRange
  duration_policy DurationPolicy
  arc ArcPlan
  hook Hook
  engagement_primary EngagementKind
  cta CtaPlan
  rationale string?
}
```

Change `ReelBlueprint`:

```baml
class ReelBlueprint {
  template_ Template
  duration_range_s DurationRange
  duration_policy DurationPolicy
  arc ArcPlan
  hook Hook
  beats Beat[]
  loop LoopPlan
  engagement_primary EngagementKind
  cta CtaPlan
  completion_rationale string
  cap_rationale string?
  omitted_candidate_ids string[]?
  rationale string?
}
```

Migration rule:

- Remove scalar `target_duration_s` from BAML prompts and LLM decision types.
- If a downstream reader needs a transition scalar, expose only a derived read-only `legacy_target_duration_s` or eval metric after arrange/resolve. It is not an input to Strategize or Arrange and is not used as a fit target.
- Update readers/tests that currently expect `target_duration_s`: BAML tests, prompt tests, eval extraction, `_write_triple` sidecar assertions, and any CLI/display snapshots.
- Add a regression prompt test that rejects `target_duration_s` in `strategize.baml` and `arrange.baml`.

## Implementation Behaviors After GO

### B1: Resolve DurationPolicy at the Planner Boundary

Files:

- `src/reel_af/planner/config.py`
- `src/reel_af/render/config/planner.json`
- `src/reel_af/planner/pipeline.py`
- `tests/planner/test_config.py`
- `tests/planner/test_pipeline.py`

Changes:

- Add config fields:
  - `r7_soft_cap_s: 180.0`
  - `r7_cap_tolerance_s: 3.0`
  - `max_beats: 48` or higher, now actively used as an upper guard
  - `max_candidates: 160` minimum proposed; prefer 240 if prompt token budget holds
  - `mine_window_duration_s: 180.0`
  - `mine_window_overlap_s: 15.0`
  - `mine_candidates_per_window: 6`
- Add `_duration_policy(bounds, cfg)` that treats `bounds.max_s > r7_soft_cap_s` as the explicit over-180 override.
- Stop treating lower duration bounds as output minimums.

Tests:

- No bounds -> `effective_cap_s == 180`, `cap_overridden is False`.
- Bounds `{min_s:120,max_s:180}` -> cap stays 180 and min is advisory.
- Bounds `{min_s:180,max_s:240}` -> cap becomes 240 and override is true.
- Invalid ordered bounds still fail early.

### B2: Mine Across the Whole Source

Files:

- `src/reel_af/planner/pipeline.py`
- `src/reel_af/planner/llm.py`
- `baml_src/mine.baml`
- `baml_src/types.baml`
- generated `src/baml_client/**` after coordinated BAML regen
- `tests/planner/test_llm.py`
- `tests/planner/test_pipeline.py`
- `tests/planner/test_prompts.py`

Changes:

- Build deterministic transcript windows from `WordsSidecar` using word/segment timestamps.
- Bound the number of windows with adaptive sizing for long sources so live planning stays within `llm_total_timeout_s`.
- Keep `mine_window_duration_s` and `mine_window_overlap_s` as named config, but allow a deterministic window builder to widen windows when a source would otherwise produce too many calls.
- Mine per window or pass explicit window blocks into BAML so the model cannot cluster all candidates around one moment.
- Preserve verbatim alignment as the source of truth.
- Add candidate metadata if needed:
  - `source_window_id`
  - `source_window_index`
  - `source_window_start_s`
  - `source_window_end_s`
- After `enforce_verbatim`, cap candidates by value while preserving source-window diversity.
- Reject all-low-value output, but do not reject merely because a window has no good spans.
- Pack candidates passed to Strategize/Arrange with compact fields only: id, quote, value score/rationale, source window, start/end, role hints. Do not pass bulky transcript context once candidates are accepted.

Selection algorithm:

1. Split transcript into windows.
2. Ask mine for high-value candidates in each window, with no filler.
3. Align and score accepted candidates.
4. Keep top candidates per window, then global top candidates, up to `max_candidates`.
5. Preserve enough early/middle/late source coverage for strategize to form a complete arc.

Tests:

- A synthetic 30-minute `WordsSidecar` with valuable spans in early/middle/late windows produces accepted candidates from each region.
- A low-value window may contribute zero candidates without failing the whole run.
- The global candidate cap is honored after diversity preservation.
- Window cap and adaptive sizing keep BAML call count bounded for a synthetic long source.
- Candidate packing keeps Strategize/Arrange payload size bounded while retaining early/middle/late coverage.
- Prompt tests assert "whole source", "source windows", "do not cluster", and "do not emit filler just to fill a quota".

### B3: Strategize an Arc and Range, Not a Scalar Target

Files:

- `baml_src/strategize.baml`
- `baml_src/types.baml`
- `src/reel_af/planner/llm.py`
- `tests/planner/test_llm.py`
- `tests/planner/test_prompts.py`

Changes:

- Replace "TIGHT TARGET LENGTH BAND" with "CONTENT-DRIVEN LENGTH LATITUDE".
- Strategize must output:
  - intended arc;
  - duration range/latitude;
  - completion criteria;
  - required candidate ids;
  - optional candidate ids;
  - cap handling rationale.
- Remove local rejection that assumes scalar `target_duration_s` must be inside bounds.
- Validate instead:
  - range is finite and ordered;
  - range max is not above `duration_policy.effective_cap_s` unless it is explicitly marked as a pre-cap complete-arc estimate;
  - required candidates exist in accepted candidates;
  - rationale is present.

Tests:

- BAML adapter passes candidate objects and `DurationPolicy`.
- Missing arc/completion criteria fails before pipeline writes artifacts.
- Static prompt tests reject "target length is tight enough" and require "content complete", "duration range", "do not pad", "do not truncate".

### B4: Arrange Until Arc Completion, Then Apply the Cap

Files:

- `baml_src/arrange.baml`
- `baml_src/types.baml`
- `src/reel_af/planner/pipeline.py`
- `src/reel_af/planner/lint.py`
- `tests/planner/test_pipeline.py`
- `tests/planner/test_lint.py`
- `tests/planner/test_prompts.py`

Changes:

- Arrange prompt uses a two-pass instruction:
  - first select every beat required by `strategy.arc.completion_criteria`;
  - then, if estimated duration exceeds `duration_policy.effective_cap_s`, reduce to the strongest coherent under-cap cut.
- Remove "interrupt_out on 2 or 3 beats total" as a global count rule; replace it with density guidance scaled to beat count.
- Keep R1/R2/R3/R8 local:
  - hook still short;
  - long beats need changes;
  - back-half pacing tightens by local groups, not every single beat in a 30-beat reel;
  - final loop still distinct.
- Add `completion_rationale`, `cap_rationale`, and `omitted_candidate_ids`.
- Enforce `len(beats) <= cfg.max_beats` as a guard, not as the desired count.
- Add `validate_arc_completion(strategy, blueprint, accepted_candidates)` after arrange and before `_write_triple`.
- Require every beat to declare or infer one completion role:
  - satisfies a specific completion criterion;
  - bridges two named criteria;
  - provides non-duplicative proof or mechanism for the payoff.
- Under cap pressure, omit optional-only branches, duplicate support, and repeated examples before removing required hook/proof/payoff/loop material.
- Treat required candidate ids as required unless `cap_rationale` explains why a coherent under-cap cut had to omit them.
- Define R3 for long reels as sectional pacing:
  - intro establishes promise and context;
  - middle advances proof without repeated low-change beats;
  - payoff-approach has higher change density or shorter median unresolved beat length than the middle;
  - full-sentence quote integrity can override shortening, but R2 still requires visible/listenable change inside long beats.

Tests:

- A fake arrange output with 18 coherent beats compiles and is not rejected for being longer than old 30-60s bands.
- A fake output above 180s without override returns an R7 diagnostic or triggers one repair pass.
- A fake output above 180s with override `{max_s:240}` is accepted if under 240.
- A fake output under requested `min_s` is accepted when `completion_rationale` says the arc is complete.
- R1/R2/R3/R8 warning behavior remains local and does not become a total duration cap.
- Missing required candidate coverage fails before sidecars are written.
- A beat with no criterion/bridge/proof role produces a lint warning or repair hint.
- Sectional R3 tests prove long reels do not require strict monotonic beat duration.

### B5: Deterministic R7 Policy Check and Repair Hint

Files:

- `src/reel_af/planner/lint.py`
- `src/reel_af/planner/pipeline.py`
- `tests/planner/test_lint.py`
- `tests/planner/test_pipeline_repair.py`

Changes:

- Add R7 lint after `resolve_timecodes()` so it uses resolved durations, not estimates.
- Compute pre-write total arranged duration from resolved beats plus deterministic transition and interrupt durations from the same config path used by serialization/render.
- Treat `r7_cap_tolerance_s` as rounding/serialization slack only, not extra creative latitude.
- Error when:
  - total duration exceeds `duration_policy.effective_cap_s + tolerance`;
  - `len(beats) > cfg.max_beats`;
  - required arc fields are missing;
  - `completion_rationale` is absent;
  - `validate_arc_completion()` fails required hook/proof/payoff/loop coverage.
- Warning when:
  - output is much shorter than advisory lower preference but complete;
  - output exceeds advisory upper preference but stays under cap and explains why.
- Send a targeted repair hint that asks arrange for a coherent under-cap cut, not arbitrary deletion.
- Add post-compile/eval evidence that checks `FootageReel.duration_s` or the compiled plan duration against the same R7 policy.

Tests:

- R7 over-cap diagnostic includes total duration, cap, override status, and candidate/beat hints.
- Repair hint uses "drop optional support, keep hook/proof/payoff/loop" language.
- R7 warning does not fail the pipeline when content is complete and under cap.
- Pre-write estimate and compiled-duration eval agree within configured tolerance for a deterministic fixture.

### B6: Re-Exemplar Mine and Arrange for Long-Form

Files:

- `baml_src/mine.baml`
- `baml_src/strategize.baml`
- `baml_src/arrange.baml`
- `tests/planner/test_prompts.py`

Changes:

- Replace short-only few-shots with mixed examples:
  - 25s complete one-idea arc;
  - 75-100s medium proof arc;
  - 140-175s long-form coherent source-video arc;
  - over-cap source where the model chooses a coherent under-180s cut.
- Mine examples must include candidate spread across windows.
- Arrange examples must include 12-30 beats without padding.
- Include a negative example where a 5-beat reel is bad because it leaves the hook unresolved.
- Include a negative example where a long reel is bad because repeated examples do not satisfy new completion criteria.

Tests:

- Prompt tests assert the presence of long-form examples and absence of old hard short-band language.
- BAML basic tests updated to the new schema.

### B7: Evaluation Evidence for AF-ezg

Files:

- `tests/planner/eval/**`
- `src/reel_af/planner/eval/**`
- optional checked-in sanitized eval artifacts under `thoughts/searchable/shared/eval/`

Changes:

- Eval should report:
  - candidate source coverage by quartile/window;
  - strategy duration range and completion criteria;
  - arranged beat count;
  - compiled duration;
  - pre-write estimated duration and post-compile duration delta;
  - cap/override rationale;
  - omitted candidates when capped.
- The live/key-gated `wPcKNuUG3NM` repro should assert no candidate clustering and no fixed 5-beat arrangement.
- Avoid a universal "must be >= 120s" test. Instead, for this known source, use BrownFox-approved acceptance after reviewing the produced arc. Content remains the arbiter.

Tests:

- Unit eval extraction from `blueprint.json`, `strategy.json`, `mined-candidates.json`, and `accepted-candidates.json`.
- Key-gated live eval records the long-source output and validates coverage plus arc-completion rationale.

## Config Changes

Initial proposed defaults:

```json
{
  "bounds_default": { "min_s": 10.0, "max_s": 180.0 },
  "r7_soft_cap_s": 180.0,
  "r7_cap_tolerance_s": 3.0,
  "max_candidates": 160,
  "max_beats": 48,
  "mine_window_duration_s": 180.0,
  "mine_window_overlap_s": 15.0,
  "mine_candidates_per_window": 6
}
```

Rationale:

- `max_candidates: 160` gives a 28-minute source room for whole-source coverage without unlimited prompt growth.
- `max_beats: 48` supports about 3 minutes at 3.5-5s local beats while still bounding pathological output.
- Windowed mine avoids "all candidates came from minute 6" without requiring low-value filler.

## Validation Plan

Fast deterministic gates:

```bash
uv run --extra dev python -m pytest tests/planner/test_config.py tests/planner/test_llm.py tests/planner/test_lint.py tests/planner/test_pipeline.py tests/planner/test_pipeline_repair.py tests/planner/test_prompts.py -q
uv run --extra dev ruff check src/reel_af/planner tests/planner baml_src
```

BAML gates:

```bash
.venv/bin/baml-cli generate
.venv/bin/baml-cli test
```

Integration gates:

```bash
uv run --extra dev python -m pytest tests/planner/eval -q
uv run --extra dev python -m pytest tests/dsl tests/planner/test_pipeline.py -q
```

Key-gated live gate after BrownFox GO and source edits:

```bash
uv run --extra dev python -m pytest tests/planner/test_baml_client.py tests/planner/eval/test_judge_real.py --require-openrouter -q
```

## Risks and Mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Few-shot short bias remains | Mine/arrange examples currently encode tiny transcripts and compact 5-beat reels. | B6 explicitly re-exemplars short, medium, long, and over-cap cases. |
| "Content complete" is vague | Without a decision surface, arrange may ramble or stop too early. | Strategize owns `ArcPlan.completion_criteria`; arrange must satisfy it and write `completion_rationale`. |
| Whole-source mine becomes noisy | More candidates can dilute quality. | Windowed top-k plus global value cap; low-value windows can emit zero. |
| Over-180 cap causes arbitrary deletion | A naive cap could just drop the tail. | R7 repair hint and arrange prompt require coherent cut under cap while preserving hook/proof/payoff/loop. |
| R3 over many beats becomes impossible | Strictly decreasing every beat across 30 beats is brittle. | Preserve R3 as local tightening by back-half groups and payoff approach, not a monotone mathematical stair-step across every beat. |
| API name still says `target_duration_bounds_s` | Existing callers may assume hard fit-to-bounds. | Re-document as duration policy input; only `max_s > 180` is a cap override. |

Review-strengthened mitigations:

- `validate_arc_completion()` makes completion a pipeline gate instead of a prompt-only promise.
- Beat justification and duplicate-support warnings prevent the new model from becoming "longer is better".
- Pre-write and post-compile duration checks prevent an estimate/render mismatch around the 180s cap.
- The schema migration rule keeps `target_duration_s` out of the decision loop.

## Implementation Hygiene

After BrownFox GO, implementation should keep the policy path reviewable:

- Use named config/constants for soft cap, tolerance, window sizing, candidate packing, and beat guards.
- Keep `_duration_policy()`, transcript windowing, candidate packing, `validate_arc_completion()`, and R7 duration calculation as pure helpers.
- Run validation and repair gates before artifact writes.
- Keep cap and repair paths as guard clauses; avoid nested control flow around write/repair side effects.
- Do not hide generated-client compatibility behind prompt text. Adapters should make legacy fields explicit and one-way.

## System Map Appendix

Full system map artifact:

`thoughts/searchable/shared/plans/2026-07-19-reel-af-content-driven-length-system-map.md`

Core length-lever-to-behavior map:

| Lever | Current behavior | Target behavior |
|---|---|---|
| Spec R7 | Short-form default band, `15-30s / 30-60s`. | Content-driven complete arc, default 180s cap, overridable by `target_duration_bounds_s.max_s > 180`. |
| `target_duration_bounds_s` | Produces hard local bounds for strategize scalar target. | Duration policy input; max above 180 overrides cap, lower/upper preferences do not force padding/truncation. |
| `target_duration_s` | Scalar strategy/blueprint field; not used to fit output. | Removed from LLM decision contract; replaced by `DurationRange` and `DurationPolicy`. |
| `max_candidates` | Upper adapter guard only. | Global safety cap after windowed source coverage preservation. |
| `max_beats` | Configured but unused. | Active upper guard for pathological output, not a desired count. |
| Mine prompt | High-value candidates for "a short reel"; no coverage contract. | Windowed/whole-source candidate mining with high-value filter and source diversity. |
| Strategize prompt | Pick shortest tight band, 18-55s. | Pick intended arc, completion criteria, duration latitude, and cap rationale. |
| Arrange prompt | Five-role shape plus short examples; no target-fill rule. | Include all required beats to complete arc, then cap by coherent cut. |
| R1/R2/R3/R8 | Local warning rules. | Preserved as local warning rules at any total length. |
| R11 | Hard engagement-bait error. | Preserved as hard engagement-bait error. |
| DSL `MAX_REEL_DURATION_S` | Hard render cap 900s. | Unchanged; planner policy stays stricter by default. |

## Non-Goals Before GO

- No code edits.
- No spec edits.
- No BAML regeneration.
- No commits, pushes, or `bd dolt push`.

## Approval Gate

Implementation must wait until BrownFox replies `GO`.
