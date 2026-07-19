# BAML single-source-of-truth refactor + baml_src restructure

**Decision (principal, 2026-07-19): Option 1.** BAML-generated types become the SINGLE source of
truth for the planner domain types. DROP the hand-written pydantic duplicates in `planner/models.py`.
This deletes the BAML↔pydantic bridge and fixes the real `ArrangeReel` bug found by the live e2e.

## Why (the bug this fixes)
Live e2e on `https://youtu.be/wPcKNuUG3NM`: transcription ✓, `MineCandidates` ✓ (verbatim, aligned
quality 1.0), `StrategizeReel` ✓ — then `ArrangeReel` **failed**: `arrange` fed
`strategy.model_dump()` (pydantic lowercase alias values like `result_first`, field `template`) back
into BAML, but BAML **function inputs require the enum member** (`ResultFirst`) and the real field
name (`template_`). Docs confirm: BAML enum values MUST be PascalCase; `@alias` only affects LLM
prompt/parse, NOT your code or function args. So the fix is to stop maintaining a second shape at all.

## Reference pattern to follow
`https://github.com/ai-that-works/ai-that-works/tree/main/2025-03-31-large-scale-classification/baml_src`
- `clients.baml` — ALL `client<llm>` + `retry_policy` blocks; named + composable (`round-robin`,
  `fallback` strategies over base clients).
- `generators.baml` — only the generator block.
- ONE `.baml` file per function/concern; that function's bespoke types live beside it; shared types in
  a shared file.
- `retry_policy` with explicit strategy (`exponential_backoff` + `delay_ms`, `multiplier`, `max_delay_ms`).
- Prompts: `{{ ctx.output_format }}` for schema, `{{ _.role('user') }}` for the user turn, kept tight.
- A `test` block per function.

## Task A — baml_src restructure  [PANE 1 / bravo — sole owner of baml_src + baml_client]
Split `baml_src/reel_planner.baml` into, per the reference:
- `generators.baml` (exists).
- `clients.baml` — `PlannerLLM` (openrouter, `anthropic/claude-sonnet-5`) + `PlannerRetry`
  (exponential_backoff). ADD a `fallback` client for model resilience per the reference.
- `types.baml` — all enums (PascalCase identifiers + `@alias("<lowercase wire>")`) + all classes
  (`CandidateSpan, Hook, Interrupt, CutIn, Engagement, Beat, LoopPlan, CtaPlan, DurationBounds,
  ReelStrategy, ReelBlueprint`). **Rename `template_` → `template`** (NOT reserved — verified by
  compile probe; only `template_string` is a keyword). Keep enum `@alias` for the lowercase wire values.
- `retention.baml` — the `RetentionRules()` `template_string`.
- `mine.baml` / `strategize.baml` / `arrange.baml` — one function each + its `test` block.
Regenerate: `.venv/bin/baml-cli generate`; commit-stage `baml_client/`.

## Task B — single-source exposure  [PANE 1 decides + announces "TYPES-READY"]
Turn `planner/models.py` into a THIN FACADE that re-exports the BAML types so the ~40 downstream
import sites barely change:
```python
from baml_client.types import (
    CandidateSpan, ReelStrategy, ReelBlueprint, Beat, Hook, Interrupt, CutIn, Engagement,
    LoopPlan, CtaPlan, DurationBounds, Template, HookType, BeatRole, InterruptKind,
    EngagementKind, CutInKind, CtaHardness,
)
```
Plus module-level HELPER FUNCTIONS replacing the pydantic methods/validators BAML types don't carry:
- `interrupt_marker(interrupt) -> str`  (was `Interrupt.kind_as_marker`: `black`→`"insert"` else kind)
- validator equivalents as functions where still needed (candidate end>start; cutin until>at + visual
  requires image_prompt; interrupt trans default effect). BAML SAP already validates shape; keep only
  the semantic guards that matter.
Note: BAML pydantic classes have no `extra="forbid"` — acceptable (SAP validates LLM output).
**Announce in Agent Mail: "TYPES-READY" + the exact exported names + helper signatures.** Panes 2 & 3 gate on it.

## Enum-value semantics (EVERYONE — verify in the generated types)
The generated enums are Python enums; the observed `model_dump` uses the IDENTIFIER (`"ResultFirst"`,
`"Save"`), not the alias. So:
- In code, compare with the MEMBER: `x == HookType.ResultFirst` (not `== "result_first"`).
- `serialize.py` must MAP BAML enum members → the DSL's lowercase wire tokens for `composite.ts.md`
  (e.g. interrupt kind → marker). This is legitimate domain mapping, NOT the redundant type bridge.

## Task C — llm.py, kills the arrange bug  [PANE 2 / charlie — gate on TYPES-READY]
`BamlPlannerLLM.mine/strategize/arrange` call `b.*` and **RETURN THE BAML OBJECTS DIRECTLY** — no
`model_validate`, no `model_dump` round-trip. Pass BAML objects between calls (the `ReelStrategy` from
`strategize` goes straight into `arrange`). Type the `PlannerLLM` Protocol to the BAML types.
`FakePlannerLLM`/`NeverPlannerLLM` construct BAML type instances. Update `test_llm.py`; the
`test_type_bridge.py` parity test is now trivial or deleted (one shape).

## Task D — deterministic layers  [PANE 3 / delta — gate on TYPES-READY]
`pipeline.py`, `serialize.py`, `lint.py`, `verbatim.py`, `config.py`: consume BAML types; switch
enum comparisons to BAML members; use pane 1's helper fns for `kind_as_marker`/validators;
`serialize.py` maps enum members → DSL wire tokens (above). `verbatim.py` aligns `beat.span_quote`
(now a BAML `Beat`) — same logic, new type. `WordsSidecar` stays `dsl/models.py` (unaffected).

## Task E — prove it  [PANE 4 / echo — after A–D land]
`transcribe.py`/`ingest.py` unaffected (WordsSidecar is dsl). Own: (1) run the full suite green;
(2) **RE-RUN the live e2e on `https://youtu.be/wPcKNuUG3NM`** and confirm `ArrangeReel` now succeeds,
the A1 triple is written, and `compile_composite` returns `status="ok"`; (3) add the gated e2e test
(B12) that was skipped, capturing the transcript as a committed fixture.

## Guardrails
Single-owner `baml_src` (pane 1). Reserve shared files; announce behaviors in Agent Mail. Branch
`reel-af-a1-producer-impl`. Test: `uv run --extra dev python -m pytest tests/planner -q`.
**CONSERVATIVE: no git commit/push, no bd dolt push.**
**Done = suite green AND the live e2e on wPcKNuUG3NM writes the triple + compiles (arrange no longer errors).**
