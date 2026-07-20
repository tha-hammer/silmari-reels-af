# AF-ezg Research: Reel Planner Content-Driven Length

Date: 2026-07-19
Worker: CyanCompass
Bead: AF-ezg
Repo: `/home/maceo/ntm_Dev/reel-af-a1-producer-impl/silmari-reels-af`
HEAD: `db55855a6ad5eb06a3c0b169406f32cae030ad3e`

## Question

Enumerate every current length lever in the A1 transcript-to-DSL planner and classify whether it is a hard ceiling, soft/default policy, prompt bias, deterministic validator, or unused/non-load-bearing field. BrownFox's task context says the current behavior is short-form by spec and by prompt/model behavior; this document verifies the source surfaces that make that true.

## Executive Findings

The current A1 producer is not a fit-to-target system in deterministic code, but it is short-form by spec and BAML prompt design:

- Spec Section 6 R7 currently describes a short length band: `15-30s completion / 30-60s watch-time` (`specs/reels-planner.a1-producer.spec.md:194-213`). The same spec example uses `target_duration_s: 28` and a five-beat shape (`specs/reels-planner.a1-producer.spec.md:131-160`).
- `strategize.baml` requires `target_duration_s` to sit inside the requested bounds, but then instructs the model to pick the shortest tight band, maxing its intrinsic prompt bands at `42-55s` (`baml_src/strategize.baml:18-27`).
- `arrange.baml` receives the strategy but has no explicit target-fill or dynamic beat-budget rule. It describes a five-role story shape and examples that are short, around five or six beats (`baml_src/arrange.baml:24-32`, `baml_src/arrange.baml:101-119`, `baml_src/arrange.baml:155-173`).
- `mine.baml` asks for spans that can carry "a short reel" and provides only high-value filtering guidance; it does not require whole-source coverage, source-time diversity, arc groups, or a minimum candidate count (`baml_src/mine.baml:8-23`).
- The Python planner does not make the final reel fit `target_duration_s`. It mines, verifies candidates, asks strategize, asks arrange, resolves whatever beats arrange returned, lints, then serializes those beats (`src/reel_af/planner/pipeline.py:46-119`).
- `max_beats` exists in config and schema but is not used anywhere outside config definition/tests. `max_candidates` is only an upper bound on the BAML return (`src/reel_af/render/config/planner.json:15-20`, `src/reel_af/planner/config.py:62-66`, `src/reel_af/planner/llm.py:127-130`).
- Local retention rules R1/R2/R3/R8 are warnings, not hard length ceilings. Only R11 engagement bait is an error; the pipeline fails retention lint only when an error diagnostic exists (`src/reel_af/planner/lint.py:44-50`, `src/reel_af/planner/lint.py:59-72`, `src/reel_af/planner/lint.py:75-189`, `src/reel_af/planner/pipeline.py:86-97`).
- The DSL aggregate has a hard renderability ceiling at 900s, not 180s (`src/reel_af/dsl/models.py:24-27`, `src/reel_af/dsl/models.py:312-320`, `src/reel_af/dsl/models.py:574-581`). The current 180s cap is planner policy/default bounds, not the deepest render hard stop.

## Length Lever Inventory

| Lever | Evidence | Current behavior | Classification |
|---|---|---|---|
| Governing spec pipeline S2/S3 | `specs/reels-planner.a1-producer.spec.md:103-125` | S2 explicitly outputs target length band R7; S3 arranges beats and enforces R2/R3/R4/R8. | Spec-level design, currently short-form. |
| Spec example target duration | `specs/reels-planner.a1-producer.spec.md:131-160` | Example blueprint uses `target_duration_s: 28` and five beats. | Few-shot/spec bias toward short reels. |
| Spec Section 6 R7 | `specs/reels-planner.a1-producer.spec.md:194-213` | R7 is "length band by intent" with default `15-30s completion / 30-60s watch-time`; spec says non-R11 numbers are ordinal defaults, not hard thresholds. | Soft/default policy, but currently short-form. |
| RetentionRules global frame | `baml_src/retention.baml:3-6` | The shared system prompt says "one short vertical reel." | Prompt bias, not deterministic hard cap. |
| R1 hook window | `baml_src/retention.baml:21-23`, `src/reel_af/planner/lint.py:75-93` | Hook should resolve within 3.5s; deterministic lint emits a warning if hook duration exceeds `cfg.r1_hook_window_s`. | Local per-hook rule, warning only. |
| R2 cadence | `baml_src/retention.baml:24-25`, `src/reel_af/planner/lint.py:96-115` | Beat cadence ceilings are 3s entertainment, 5s educational, 9s B2B unless a cut-in/interrupt exists. | Local per-beat rule, warning only; not a reel-length cap. |
| R3 escalation | `baml_src/retention.baml:26-27`, `src/reel_af/planner/lint.py:171-189` | Back half should tighten; lint warns when durations are non-decreasing. | Local pacing rule, warning only. |
| R8 loop | `baml_src/retention.baml:37-39`, `src/reel_af/planner/lint.py:151-168` | Final span must echo hook idea; lint warns below token overlap threshold. | Local ending rule, warning only. |
| R11 bait | `baml_src/retention.baml:45-46`, `src/reel_af/planner/lint.py:59-72` | Bait patterns produce an `error` diagnostic. | Hard lint gate, unrelated to length. |
| Mine prompt | `baml_src/mine.baml:8-23` | Asks for CandidateSpan objects for "a short reel"; rejects filler and low-value spans; never emits low-value spans just to fill the list. | Candidate-quality prompt; no source-coverage or minimum count. |
| Mine examples | `baml_src/mine.baml:39-68` | Few-shots keep one or two spans from tiny transcripts. | Strong short-list bias; no long-arc example. |
| BAML candidate type | `baml_src/types.baml:72-96` | CandidateSpan/PlannerCandidate include quote, timing hints/aligned timing, scores, and rationale, but no arc id, coverage bucket, or narrative role. | Data model lacks whole-source coverage contract. |
| Candidate upper bound | `src/reel_af/render/config/planner.json:17-19`, `src/reel_af/planner/config.py:64-66`, `src/reel_af/planner/llm.py:127-130` | Config has `max_candidates: 80`; adapter only rejects if BAML returns more than the limit. | Upper hard adapter guard; not a minimum material guarantee. |
| Candidate verifier | `src/reel_af/planner/verbatim.py:45-84` | Aligns every mined candidate and keeps only candidates above the verbatim floor. | Quality/validity gate; can reduce supply; no coverage repair. |
| Candidate join policy | `src/reel_af/planner/verbatim.py:87-132`, `src/reel_af/planner/verbatim.py:224-340` | Beat quotes can trim or join through adjacent candidate windows when the quote is continuous in source tokens. | Enables longer spans only if mine provided adjacent candidates. |
| Strategize bounds requirement | `baml_src/strategize.baml:12-20`, `src/reel_af/planner/llm.py:241-258` | BAML must output `target_duration_s` within bounds; adapter rejects out-of-bounds targets. | Hard target field bounds, not final compiled duration bounds. |
| Strategize tight bands | `baml_src/strategize.baml:22-27` | Prompt instructs shortest band: 18-24s, 24-32s, 32-42s, 42-55s; if requested bounds exclude ideal, choose nearest 6-10s band inside bounds. | Prompt-level short-form policy. |
| Strategize rationale | `baml_src/strategize.baml:89-93` | Rationale must name why target length is tight enough. | Prompt bias toward shorter target. |
| Strategy schema | `baml_src/types.baml:155-162` | `ReelStrategy` has one scalar `target_duration_s`, not a range/latitude or completeness criterion. | Data model favors point target. |
| Arrange story shape | `baml_src/arrange.baml:16-32` | Ordered beats must follow one narrative thread with Hook, Context, Value, Payoff, Cta/loop. | Narrative constraint; currently implies ~5 roles but does not prohibit more beats. |
| Arrange R3/R8 local rules | `baml_src/arrange.baml:34-55` | Hook max <=3.5s, beats >5s need change, back half tightens, final beat echoes hook with distinct source. | Local craft rules, not total duration cap. |
| Arrange interrupts | `baml_src/arrange.baml:73-85` | Use interrupt_out on 2 or 3 beats total, not every beat. | Prompt bias toward short beat count; may need revision for long arcs. |
| Arrange examples | `baml_src/arrange.baml:101-119`, `baml_src/arrange.baml:155-173` | Few-shot examples are a compact 5-6 beat arrangement and BAML test target is 24s. | Strong short-form exemplar bias. |
| Blueprint schema | `baml_src/types.baml:164-172` | `ReelBlueprint` has `target_duration_s` and `beats Beat[]`; no total duration range, completeness flag, or cap rationale field. | Data model carries target but not content-completion contract. |
| Beat max length | `baml_src/types.baml:127-135`, `src/reel_af/planner/serialize.py:80-86`, `src/reel_af/planner/serialize.py:246-252` | Each beat has `max_len_s`; resolver clamps aligned span end to `start_s + max_len_s`. | Per-beat upper bound; can shorten compiled reel; not a total target fitter. |
| Planner orchestration | `src/reel_af/planner/pipeline.py:46-119` | `plan()` uses arranged beats as returned. It does not compare compiled duration to `strategy.target_duration_s` or add/remove beats to fit. | Content-as-arranged, not target-fit. |
| Repair loop | `src/reel_af/planner/pipeline.py:65-84`, `src/reel_af/render/config/planner.json:15` | Repair retries unresolved quotes only. | Validity repair, not length repair. |
| Retention lint fail condition | `src/reel_af/planner/pipeline.py:86-97` | Only `severity == "error"` fails the plan. R1/R2/R3/R8 are warnings. | Local warnings; no hard length ceiling. |
| Triple sidecars | `src/reel_af/planner/pipeline.py:141-179` | Writes `mined-candidates.json`, `accepted-candidates.json`, `strategy.json`, and `blueprint.json` alongside triple. | Existing evidence surface for future length evals. |
| Hook-plan bounds | `src/reel_af/planner/serialize.py:170-214`, `src/reel_af/planner/serialize.py:290-295` | Hook plan records duration bounds; default is 10-180 if omitted, but `plan()` passes effective bounds. | Metadata/default, not compiled duration enforcement. |
| Default planner bounds | `src/reel_af/render/config/planner.json:11-14`, `src/reel_af/planner/pipeline.py:227-238` | Defaults are min 10s, max 180s unless caller passes `target_duration_bounds_s`. | Soft cap/default policy at planner boundary. |
| `target_duration_bounds_s` entrypoint | `src/reel_af/app.py:1748-1792` | Public reasoner accepts optional bounds and passes them into `plan()`. | Override path exists for planner target bounds. |
| `max_beats` | `src/reel_af/render/config/planner.json:19`, `src/reel_af/planner/config.py:66` | Config/schema field exists. Focused search found no runtime read outside config/tests. | Unused lever today. |
| DSL total hard cap | `src/reel_af/dsl/models.py:24-27`, `src/reel_af/dsl/models.py:312-320`, `src/reel_af/dsl/models.py:574-581` | `FootageReel.duration_s` must be positive and <= 900s; `validate_renderable()` rejects over 900s. | True hard render cap, much higher than 180s. |
| A1 hook clip constants | `src/reel_af/dsl/models.py:37-40`, `src/reel_af/dsl/models.py:247-264`, `tests/dsl/test_compile_context.py:42-57` | CompileContext defaults carry min hook clip 10s and max hook clip 180s, but focused search found no current enforcement against total reel duration. | Context/default; not current total length gate. |
| Generative path beat model | `src/reel_af/models.py:226-263` | Older generative path says a reel has about five beats and uses `target_duration_s` to pick Veo buckets. | Non-load-bearing for A1 transcript-to-DSL planner; do not patch for AF-ezg. |

## Diagnosis Check

BrownFox's latest `AF-ezg` bead context reports a 2026-07-19 test with bounds `{min:120,max:180}` where strategize chose `124-126s`, arrange emitted only five beats, compiled reels were `17-32s`, and mine returned 13 clustered candidates. That exact 120-180 run output is not checked into this repo. The current source does verify the mechanisms that make that outcome plausible:

- strategize is the only phase locally checked against duration bounds (`src/reel_af/planner/llm.py:254-256`);
- arrange is not given an explicit dynamic beat budget or target-fill rule (`baml_src/arrange.baml:16-32`);
- pipeline does not check compiled duration against the strategy target (`src/reel_af/planner/pipeline.py:63-119`);
- mine has no whole-source coverage requirement and no minimum candidate count (`baml_src/mine.baml:8-23`);
- `max_beats` is not runtime-consumed (`src/reel_af/render/config/planner.json:19`, `src/reel_af/planner/config.py:66`).

Checked-in supporting evidence from an earlier `wPcKNuUG3NM` baseline also shows short-form behavior: a handoff says mine returned 14 candidates and arrange made a 6-beat 41.58s reel (`thoughts/searchable/shared/handoffs/general/2026-07-19_11-44-37_reel-af-baml-planner-script-quality.md:68-70`), and the eval artifact records `target_duration_s` about 42.93s with six beats and compile status ok (`/home/maceo/ntm_Dev/reel-af-a1-producer-impl/thoughts/searchable/shared/eval/2026-07-19-AF-0lx/20260719T183239Z-BASELINE-0.json:104-118`, `/home/maceo/ntm_Dev/reel-af-a1-producer-impl/thoughts/searchable/shared/eval/2026-07-19-AF-0lx/20260719T183239Z-BASELINE-0.json:150-179`, `/home/maceo/ntm_Dev/reel-af-a1-producer-impl/thoughts/searchable/shared/eval/2026-07-19-AF-0lx/20260719T183239Z-BASELINE-0.json:217-240`).

## Hard vs Soft Length Ceilings

Hard ceilings:

- DSL/render aggregate: 900s via `MAX_REEL_DURATION_S` and `FootageReel.duration_s` validation (`src/reel_af/dsl/models.py:24-27`, `src/reel_af/dsl/models.py:312-320`, `src/reel_af/dsl/models.py:574-581`).
- Adapter candidate upper bound: more than `max_candidates` raises (`src/reel_af/planner/llm.py:127-130`).
- Strategy scalar must be inside caller/default bounds (`src/reel_af/planner/llm.py:254-256`).
- R11 engagement bait errors fail the planner (`src/reel_af/planner/lint.py:59-72`, `src/reel_af/planner/pipeline.py:93-97`).

Soft/default or prompt-only constraints:

- Spec R7 `15-30s / 30-60s` is a default band with confidence caveat, not a render hard cap (`specs/reels-planner.a1-producer.spec.md:194-213`).
- Planner default bounds max is 180s, and caller can override through `target_duration_bounds_s` (`src/reel_af/render/config/planner.json:11-14`, `src/reel_af/app.py:1748-1792`).
- R1/R2/R3/R8 are local craft rules and warnings in deterministic lint (`src/reel_af/planner/lint.py:75-189`).
- Strategize/arrange short examples are prompt bias, not code caps (`baml_src/strategize.baml:22-27`, `baml_src/arrange.baml:101-119`).

Unused or misleading levers:

- `max_beats` is not runtime-consumed. Raising it alone cannot increase output length unless arrange is taught and/or validated against a dynamic budget (`src/reel_af/render/config/planner.json:19`, `src/reel_af/planner/config.py:66`).
- `target_duration_s` in `ReelBlueprint` is persisted but not used to fit, extend, or truncate serialized beats (`baml_src/types.baml:164-172`, `src/reel_af/planner/pipeline.py:99-119`).

## Workflow Closure Map

Current behavior chain:

```text
transcript_to_plan request
-> source_url transcription / WordsSidecar
-> MineCandidates returns CandidateSpan[]
-> enforce_verbatim creates accepted PlannerCandidate[]
-> StrategizeReel creates scalar target_duration_s inside bounds
-> ArrangeReel returns Beat[]
-> resolve_timecodes clamps each beat to max_len_s
-> lint_blueprint warnings/errors
-> serialize_composite + build_hook_plan + _write_triple
-> consumer compile/render observes duration from arranged source spans
```

Node labels:

| Depth | Node | Evidence | Label | Adds/changes in AF-ezg plan |
|---:|---|---|---|---|
| 0 | Public reasoner `transcript_to_plan` accepts bounds | `src/reel_af/app.py:1748-1792` | production-called | no |
| 1 | `plan()` orchestration | `src/reel_af/planner/pipeline.py:29-119` | production-called | yes, after GO |
| 2 | `MineCandidates` prompt and adapter | `baml_src/mine.baml:1-83`, `src/reel_af/planner/llm.py:229-239` | production-called | yes, after GO |
| 3 | `StrategizeReel` prompt and adapter | `baml_src/strategize.baml:1-127`, `src/reel_af/planner/llm.py:241-258` | production-called | yes, after GO |
| 4 | `ArrangeReel` prompt and adapter | `baml_src/arrange.baml:1-173`, `src/reel_af/planner/llm.py:260-276` | production-called | yes, after GO |
| 5 | Serialization and sidecar write | `src/reel_af/planner/serialize.py:58-167`, `src/reel_af/planner/pipeline.py:141-179` | production-called | maybe |
| 6 | Compile/render observes derived duration | `src/reel_af/dsl/compile.py:135-163`, `src/reel_af/dsl/models.py:312-377` | production-called | test only |

Highest new connector after GO should be `plan()`/planner BAML boundary, because the behavior change starts before mine/strategize/arrange and needs end-to-end evidence through compile.

### ClosureMap (structured - derive input)

```json
{
  "behavior": "transcript_to_plan produces an A1 triple whose compiled reel duration is determined by narratively justified arranged beats under the requested bounds policy.",
  "git_commit": "db55855a6ad5eb06a3c0b169406f32cae030ad3e",
  "repo": "/home/maceo/ntm_Dev/reel-af-a1-producer-impl/silmari-reels-af",
  "nodes": [
    {
      "id": "source_video_transcript",
      "module": "src/reel_af/app.py transcript_to_plan",
      "is_entrypoint": true,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": "WordsSidecar"
    },
    {
      "id": "planner_orchestration",
      "module": "src/reel_af/planner/pipeline.py plan",
      "is_entrypoint": false,
      "adds_or_changes": true,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "mine_strategy_arrange",
      "module": "baml_src/mine.baml + baml_src/strategize.baml + baml_src/arrange.baml",
      "is_entrypoint": false,
      "adds_or_changes": true,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "a1_triple",
      "module": "src/reel_af/planner/pipeline.py _write_triple",
      "is_entrypoint": false,
      "adds_or_changes": true,
      "read_path": "src/reel_af/dsl/compile.py compile_composite",
      "seedable_store": null
    }
  ],
  "edges": [
    {
      "is_async": false,
      "cross_boundary": true,
      "driver": null
    },
    {
      "is_async": false,
      "cross_boundary": true,
      "driver": null
    },
    {
      "is_async": false,
      "cross_boundary": true,
      "driver": null
    }
  ]
}
```
