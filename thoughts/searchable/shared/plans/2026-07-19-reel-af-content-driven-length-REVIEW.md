# Review: AF-ezg Content-Driven Reel Length Plan

Date: 2026-07-19
Reviewer: CyanCompass
Plan reviewed: `thoughts/searchable/shared/plans/2026-07-19-reel-af-content-driven-length.md`
Research reviewed: `thoughts/searchable/shared/research/2026-07-19-reel-af-content-driven-length-research.md`
System map reviewed: `thoughts/searchable/shared/plans/2026-07-19-reel-af-content-driven-length-system-map.md`
Status: needs enhancement before BrownFox approval

## Summary

The plan correctly changes the length model from fit-to-target to content-driven and keeps R1/R2/R3/R8 local. The remaining risk is not the high-level principle; it is making "complete content" concrete enough that the implementation cannot silently drift into short default behavior, padding, rambling, or arbitrary over-cap truncation.

Approval should require the plan enhancements below before implementation starts.

## Findings

### 1. Completion ownership is assigned, but completion verification is underspecified.

The plan says Strategize owns `ArcPlan.completion_criteria` and Arrange writes `completion_rationale`. That is the right boundary, but the implementation plan does not yet require a deterministic post-arrange check that the blueprint actually satisfies the arc.

Risk:

- Arrange can claim completion while omitting required candidate ids.
- Optional support can displace required hook/proof/payoff material.
- The pipeline could write artifacts before detecting that the declared arc is incomplete.

Required enhancement:

- Add a `validate_arc_completion()` gate after arrange and before writing sidecars.
- Check that every `strategy.arc.required_candidate_ids` entry is used by at least one beat or is explicitly moved to `omitted_candidate_ids` only under cap pressure.
- Check that `completion_rationale` references the completion criteria, not only generic "this is coherent" language.
- Error when a required hook, proof/mechanism, payoff, or R8 loop role is missing.

### 2. The plan needs an explicit anti-rambling rule, not only "do not pad".

The fixed requirement forbids padding, but a longer content-driven model also needs a local definition of justified beats. Otherwise the model can add repeated examples and call them completeness.

Risk:

- Windowed mine plus higher candidate caps can flood Arrange with plausible material.
- Arrange examples with 12-30 beats can teach length without teaching exclusion.

Required enhancement:

- Add a per-beat justification rule: every beat must satisfy one completion criterion, bridge two named criteria, or provide non-duplicative proof for the payoff.
- Treat duplicate support, repeated examples, and optional-only branches as first to omit under cap pressure.
- Add lint warnings for beats that lack a completion role or duplicate an already-covered criterion.

### 3. R7 duration measurement must use the same duration surface as compile/render.

B5 says R7 should compute total duration from resolved beats plus black interrupts/transitions "where available." That is not precise enough for the enforcement gate.

Risk:

- Planner accepts a blueprint under 180s by estimate, but compile output exceeds the cap.
- Repair hints chase a duration model that differs from render behavior.

Required enhancement:

- Define the pre-write estimate source exactly: resolved beat durations plus deterministic transition/interrupt durations from the same config used by serialization/render.
- Add a post-compile/eval assertion that uses `FootageReel.duration_s` or the compiled plan duration as the final evidence.
- Include cap tolerance only for known serialization/render rounding, not as free latitude.

### 4. Schema migration is ambiguous around `target_duration_s`.

The plan says to remove scalar `target_duration_s`, then says temporary compatibility may keep a derived/deprecated field if BrownFox wants it. That leaves the implementer with a product decision at coding time.

Risk:

- Existing generated BAML clients, eval readers, sidecars, and tests may still require the field.
- A compatibility field can accidentally remain in prompts and reintroduce fit-to-target behavior.

Required enhancement:

- Pick a migration rule in the plan before GO.
- Recommended rule: remove `target_duration_s` from BAML prompts and LLM decision types; if downstream compatibility is needed, expose a derived read-only `legacy_target_duration_s` or eval-only metric after arrange, never an input to arrange.
- Add an inventory of readers/tests that must be updated or adapted.

### 5. R3 over many beats needs a measurable local interpretation.

The plan correctly rejects strict monotonic tightening across 30 beats. It still needs a concrete replacement so lint and prompt tests do not diverge.

Risk:

- R3 can become toothless for long reels.
- Or future tests can accidentally reintroduce a global monotonic duration requirement.

Required enhancement:

- Define R3 as rolling or sectional pacing: compare intro, middle, and payoff-approach groups; require increasing change density or shorter median unresolved beat length near payoff.
- Keep exceptions for quotes that need full sentence integrity, but require visible/listenable change inside long beats under R2.

### 6. Whole-source mining needs token and timeout budgets.

B2 raises candidate counts and introduces windowed BAML calls, but the plan does not specify how to stay inside `llm_total_timeout_s`, prompt limits, or context budget.

Risk:

- A 28-minute transcript can multiply calls enough to make planning flaky.
- Passing 160-240 candidates to Strategize/Arrange can crowd out the actual prompt instructions.

Required enhancement:

- Add bounded window construction with a max window count or adaptive window size.
- Add candidate packing rules for Strategize/Arrange, including compact candidate fields and source-window diversity.
- Add tests for timeout/cap behavior independent of live LLM calls.

### 7. API semantics for `target_duration_bounds_s` need explicit caller-facing documentation.

The plan intentionally reinterprets existing bounds as duration policy. This matches the new requirement, but the name still implies a target fit.

Risk:

- A caller passing `{min:120,max:180}` may expect output near 120s.
- A caller passing `{max:90}` may expect a hard 90s cap, but the new requirement allows a coherent reel to exceed it up to 180s.

Required enhancement:

- Document that `min_s` and `max_s <= 180` are advisory preferences, not fit constraints.
- Document that only `max_s > 180` explicitly overrides the default 180s cap.
- Add tests for below-min complete output and above-advisory-max coherent output.

### 8. Code hygiene constraints should be included before implementation.

The plan touches planner control flow, config, lint, and generated-client boundaries. It should carry implementation constraints so the final change stays reviewable.

Required enhancement:

- Use named config/constants for cap, tolerance, windowing, and candidate packing thresholds.
- Keep policy resolution and completion validation as pure helpers before artifact writes.
- Avoid side effects in conditionals and keep repair/write sequencing explicit.
- Prefer guard clauses over nested control flow in cap/repair paths.

## Review Verdict

The plan is directionally sound but should be enhanced before asking BrownFox for GO. The required enhancements are specific and do not change the principal design principle:

- Strategize defines completion.
- Arrange proves completion with beats.
- Pipeline validates completion and cap policy before writing.
- Compile/eval verifies final duration.
- Local craft rules stay local.

