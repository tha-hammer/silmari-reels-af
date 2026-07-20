---
date: 2026-07-19T17:08:00-04:00
planner: Codex/FrostyBear
git_commit: db55855a6ad5eb06a3c0b169406f32cae030ad3e
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
beads: [AF-cm9]
status: complete
---

# Plan: AF-cm9 Remaining Output Path Migration

## Goal

Route the remaining non-A1 output path defaults through the existing shared resolver in `src/reel_af/planner/paths.py`, preserving explicit caller-provided paths and leaving the web submit boundary unchanged.

Phase 1 is already complete and must stay intact:

- `src/reel_af/planner/paths.py` defines `resolve_output_root()`, `runs_dir()`, `evals_dir()`, `REEL_AF_OUTPUT_ROOT`, `_PROJECT_ROOT`, and family-root semantics.
- `PlannerConfig` and `planner.json` already define `output_root`, `artifacts_dir`, and `evals_dir`.
- `transcript_to_plan`, `dsl_hooks_to_reels`, and eval CLI defaults are already migrated.

## Inputs

- `src/reel_af/planner/paths.py`
- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-code-seams.md`
- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md`
- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-migrate-research.md`

## Contracts

| Contract | Required behavior |
|---|---|
| Explicit path precedence | Any explicit `out_dir` or CLI `--out` path is used exactly as supplied via `Path(value)`. |
| Shared default root | Missing output paths resolve through `runs_dir(workflow, run_id)` so env/config/project-relative behavior stays centralized. |
| Public signatures | Existing function and CLI public signatures stay unchanged. |
| Web submit boundary | Browser/API submit filtering remains unchanged; `out_dir` and filesystem artifact refs stay rejected at the web boundary. |
| Family-root semantics | Defaults remain under `<output_root>/<artifacts_dir>/<workflow>-<run_id>`; with current config this is `resources/runs/<workflow>-<run_id>`. |
| Railway build context | Generated roots stay ignored from deployment context. |

## Implementation Plan

### 1. App Reasoner Defaults

Update only fallback path selection in `src/reel_af/app.py`:

| Function | Current fallback | New fallback |
|---|---|---|
| `article_to_reel` | `Path.cwd() / "output" / f"article-{run_id}"` | `runs_dir("article", run_id)` |
| `topic_to_reel` | `Path.cwd() / "output" / f"topic-{run_id}"` | `runs_dir("topic", run_id)` |
| `composite_to_reel` | `Path.cwd() / "output" / f"composite-{run_id}"` | `runs_dir("composite", run_id)` |
| `research_to_reel` | `Path.cwd() / "output" / f"research-{run_id}"` | `runs_dir("research", run_id)` |

Keep the existing `Path(out_dir) if out_dir else ...` precedence shape. Do not change `_render_downstream`, `_run_composite_reels`, storage upload behavior, or returned metadata.

### 2. Carousel Default

Update `_default_carousel_output_dir(run_id)` to use the shared resolver while preserving carousel's configured prefix:

```python
return runs_dir(_CAROUSEL_OUTPUT_DIR_PREFIX, run_id)
```

`research_to_carousel(out_dir=...)` continues to use `Path(out_dir)` directly. Carousel prompt, crop, slide count, and prefix config remain carousel-owned.

Remove the old carousel runtime root:

- delete `_CAROUSEL_OUTPUT_ROOT` from `app.py`,
- delete `output_root` from `src/reel_af/render/config/carousel.json`,
- add/adjust tests proving the default carousel directory comes from `runs_dir(_CAROUSEL_OUTPUT_DIR_PREFIX, run_id)`.

The runtime contract must not preserve carousel `output_root` as a second root.

### 3. CLI Defaults

In `src/reel_af/cli.py`, import `runs_dir` and replace local rendering defaults:

| Command | Current fallback | New fallback |
|---|---|---|
| `reel-af composite` | `_PROJECT_ROOT / "out" / "composite"` | `runs_dir("cli-composite", "default")` |
| `reel-af reels --preset <preset>` | `_PROJECT_ROOT / "out" / "reels" / preset` | `runs_dir("cli-reels", preset)` |

`--out` continues to win. The article/topic `_sidecar_dir()` fallback is not part of the explicit AF-cm9 `cli.py:340`/`cli.py:447` scope and remains unchanged unless tests reveal that the command result sidecar now conflicts with resolver behavior.

### 4. Driver Scripts

Driver scripts currently pass explicit `output/...` paths into `article_to_reel`, so app defaults cannot affect them. Add a tiny script helper that delegates to the same Python resolver:

```text
scripts/resolve_output_dir.py <workflow> <run_id>
```

The helper will:

- insert repo `src/` into `sys.path` so it works from a source checkout,
- call `reel_af.planner.paths.runs_dir(workflow, run_id)`,
- expose `resolve_output_dir(workflow: str, run_id: str) -> str` for Python scripts,
- expose `main(argv)` as a thin CLI wrapper that prints the resolved path for shell scripts.

Then update scripts:

| Script | New workflow/run id |
|---|---|
| `scripts/run_batch.sh` | `batch`, genre |
| `scripts/run_arxiv_one.py` | `scientific`, label |
| `scripts/run_arxiv_batch.py` | `scientific`, label |
| `scripts/run_random3.py` | `random3`, genre |
| `scripts/run_batch_parallel.py` | `batch`, genre |

The Python scripts import `resolve_output_dir()` directly. `run_batch.sh` calls `python3 scripts/resolve_output_dir.py ...` while retaining `set -euo pipefail`, so resolver import/runtime failures stop the script before dispatch. The scripts continue to send `out_dir` explicitly, but now their explicit value comes from the shared resolver.

### 5. Ignore Rules

`.gitignore` already covers `output/`, `/resources/runs/*`, and `/resources/evals/*` with `.gitkeep` exceptions. `.railwayignore` already excludes `output/`, `out/`, and `/resources/`.

Add `/out/` to `.gitignore` because the CLI has historically written there and local leftovers should not become tracked. No `.railwayignore` delta is required unless implementation introduces a new generated root beyond `resources`, `output`, or `out`.

### 6. Tests

Extend path tests in `tests/planner/test_paths.py`:

- resolver precedence remains covered by existing tests,
- `article_to_reel`, `topic_to_reel`, `composite_to_reel`, `research_to_reel`, and `research_to_carousel` default to `resources/runs/<workflow>-<run_id>` when `out_dir` is absent,
- each migrated app reasoner still honors explicit `out_dir`,
- CLI `composite` and `reels` defaults route through `resources/runs/...` when `--out` is absent,
- CLI `composite` and `reels` still honor explicit `--out`,
- `scripts/resolve_output_dir.py` delegates to the same resolver and honors `REEL_AF_OUTPUT_ROOT`.
- `carousel.json` no longer advertises an independent runtime `output_root`.

Do not weaken or remove web tests that reject filesystem refs or unsupported input fields.

## Validation

Required final gate:

```bash
set -a; . ./.env; set +a; uv run --extra dev python -m pytest tests/planner tests/web -q
```

Useful focused checks during implementation:

```bash
uv run --extra dev python -m pytest tests/planner/test_paths.py tests/planner/eval/test_runner_paths.py -q
uv run --extra dev python -m pytest tests/test_composite_cli.py tests/test_reels_cli.py tests/test_carousel.py tests/test_research_to_reel.py -q
```

## Non-Goals

- No commit, push, or Dolt push.
- No changes to A1 `transcript_to_plan`, `dsl_hooks_to_reels`, planner pipeline writers, or eval CLI defaults beyond preserving existing tests.
- No change to browser submit field allowlists or filesystem-ref rejection.
- No object storage or artifact-ref redesign.

## System Map

### Path Selection Flow

```mermaid
flowchart LR
  Explicit[out_dir / --out supplied] --> Chosen[Use Path(value)]
  Missing[output path absent] --> Resolver[runs_dir(workflow, run_id)]
  Resolver --> Root[resolve_output_root]
  Root --> Env[REEL_AF_OUTPUT_ROOT]
  Root --> Config[PlannerConfig.output_root]
  Root --> Default[_PROJECT_ROOT / resources]
  Env --> Sink[output_root / artifacts_dir / workflow-run_id]
  Config --> Sink
  Default --> Sink
  Chosen --> Writers[existing writers/renderers]
  Sink --> Writers
```

### Seam To Sink Map

| Seam | Entrypoint | Resolver input | Sink contract | Downstream writer |
|---|---|---|---|---|
| Article app default | `article_to_reel(url, out_dir=None)` | `runs_dir("article", run_id)` | `resources/runs/article-<run_id>/` by default; `media/` under it | `_render_downstream(..., out_path, media_dir)` |
| Topic app default | `topic_to_reel(topic, out_dir=None)` | `runs_dir("topic", run_id)` | `resources/runs/topic-<run_id>/` by default; `media/` under it | `_render_downstream(..., out_path, media_dir)` |
| Composite app default | `composite_to_reel(url, ..., out_dir=None)` | `runs_dir("composite", run_id)` | `resources/runs/composite-<run_id>/` by default | `_run_composite_reels(..., out_path)` |
| Research app default | `research_to_reel(..., out_dir=None)` | `runs_dir("research", run_id)` | `resources/runs/research-<run_id>/` by default; `media/` under it | injected/default renderer |
| Carousel app default | `research_to_carousel(..., out_dir=None)` | `runs_dir(_CAROUSEL_OUTPUT_DIR_PREFIX, run_id)` | `resources/runs/carousel-<run_id>/` by default | `_render_one_slide(..., out_dir=run_dir)` |
| CLI composite | `reel-af composite URL [--out PATH]` | `runs_dir("cli-composite", "default")` | `resources/runs/cli-composite-default/` by default | `render.composite_pipeline.composite_to_reel(url, work, ...)` |
| CLI reels | `reel-af reels SOURCE --preset P [--out PATH]` | `runs_dir("cli-reels", preset)` | `resources/runs/cli-reels-<preset>/` by default | lower/middle third render and composite helpers |
| Driver scripts | `scripts/run_*.{sh,py}` | `scripts/resolve_output_dir.py workflow run_id` | helper prints the same `runs_dir(workflow, run_id)` path | AgentField `article_to_reel` receives explicit resolved `out_dir` |

### Interface Grammar

```text
WorkflowComponent ::= non-empty string with "/" and "\" normalized to "-"
RunComponent      ::= non-empty string with "/" and "\" normalized to "-"
RunDir            ::= OutputRoot "/" ArtifactsDir "/" WorkflowComponent "-" RunComponent
OutputRoot        ::= ExplicitRoot | EnvRoot | ConfigRoot | ProjectRoot "/resources"
ArtifactsDir      ::= PlannerConfig.artifacts_dir  # current: "runs"
EvalDir           ::= OutputRoot "/" PlannerConfig.evals_dir

AppReasonerInput  ::= { ..., out_dir?: string }
AppReasonerRule   ::= if out_dir is present: Path(out_dir)
                    | else: RunDir

CLIInput          ::= command args + optional "--out PATH"
CLIRule           ::= if --out is present: Path(PATH)
                    | else: RunDir

DriverHelper      ::= "scripts/resolve_output_dir.py" WorkflowComponent RunComponent
DriverOutput      ::= stdout line containing RunDir
```

### Boundary Contracts

| Boundary | Data crossing | Owner | Error behavior |
|---|---|---|---|
| App reasoner -> resolver | workflow string, run id string | app reasoner owns workflow/run naming; resolver owns root precedence and component sanitization | Existing reasoners continue their current error handling; resolver does not create dirs. |
| Resolver -> writer | concrete `Path` | writer owns file creation below the chosen directory | Existing `mkdir(parents=True, exist_ok=True)` calls stay at the reasoner/writer layer. |
| Script -> helper | workflow/run id CLI args | script owns scenario labels; helper owns resolver import and printing | helper exits non-zero on wrong arg count or import/runtime error. |
| Web submit -> control plane | target-specific safe input payload | web layer owns browser-facing allowlists | Unsupported fields and filesystem refs remain rejected before row creation and dispatch. |
| Carousel config -> carousel default | `output_dir_prefix` only | carousel owns carousel naming/prompt behavior | carousel `output_root` no longer controls runtime generated roots. |

## Review

### Review Summary

| Category | Status | Issues Found |
|---|---|---|
| Contracts | Warning | 2 plan amendments needed |
| Interfaces | Warning | 1 plan amendment needed |
| Promises | Pass | No async/concurrency change |
| Data Models | Warning | 1 config cleanup decision needed |
| APIs | Pass | Web/API boundary remains unchanged |
| CodeCleanup Gates | Pass | No planned side-effecting or mutating control expressions |

### Findings

1. **Carousel `output_root` must not be left ambiguous.**
   - Risk: the draft says the key may be left inert or removed. Leaving an unused runtime root in `carousel.json` creates configuration drift and makes later operators think carousel still has a separate output root.
   - Amendment: remove `_CAROUSEL_OUTPUT_ROOT` from `app.py`, remove `output_root` from `carousel.json`, and add/adjust tests so carousel default output proves it comes from `runs_dir(_CAROUSEL_OUTPUT_DIR_PREFIX, run_id)`.

2. **Driver helper needs a callable interface, not only stdout.**
   - Risk: Python driver scripts should not have to shell out to resolve paths, and shell scripts still need a direct command. A stdout-only helper makes the Python scripts more awkward and harder to test.
   - Amendment: implement `resolve_output_dir(workflow: str, run_id: str) -> str` in `scripts/resolve_output_dir.py`, with `main(argv)` only as a thin CLI wrapper. Python scripts import the function; shell script invokes the CLI.

3. **Shell helper dependency context should be explicit.**
   - Risk: `scripts/run_batch.sh` currently uses only `curl` and `python3`. Importing `reel_af.planner.paths` requires the project Python environment. If the helper fails in a plain shell, the script should fail before dispatching jobs with stale paths.
   - Amendment: `run_batch.sh` should call `python3 scripts/resolve_output_dir.py ...` before dispatch and keep `set -euo pipefail`, so a missing dependency fails closed. The helper itself should insert repo `src/` into `sys.path`; the validation test should execute it as a subprocess with `REEL_AF_OUTPUT_ROOT` set.

4. **CLI fixed default directories retain existing overwrite behavior.**
   - Risk: `runs_dir("cli-composite", "default")` is a stable path and repeated CLI composite runs can reuse the same directory. This matches the old `_PROJECT_ROOT/out/composite` behavior, so it is not a blocker, but the test should pin the intentional default.
   - Amendment: add CLI tests for default work paths and explicit `--out` precedence so this behavior is visible.

5. **Web submit boundary is correctly out of scope.**
   - Risk reviewed: app reasoner signatures still accept `out_dir`; web allowlists reject browser-supplied `out_dir` and filesystem refs. The plan preserves that separation.
   - Amendment: keep the final validation gate on `tests/web`; do not edit web submit code.

### CodeCleanup Plan-Hygiene Review

- No planned side effects in conditionals: path selection remains simple `Path(value) if value else runs_dir(...)`; helper `main()` can use guard clauses for arg validation.
- No mutation in control expressions: no planned mutation in `if`/`while`/`for` conditions.
- NeverNesting: helper should use an early exit for usage errors and keep resolver import/setup serial.
- Named constants over literals: workflow names are external behavior labels; keep them literal at call sites where they are the contract. Do not introduce an abstraction just to hide one-use workflow names.
- Control-expression discipline: preserve existing explicit path precedence exactly; do not reorder web validation or reasoner error guards.
- Maintainability recovery: removing carousel `output_root` is preferred over leaving a stale config root.

### Approval Status

Review findings have been folded into the plan. The enhanced plan is ready for implementation.

## Enhancement

Applied review amendments:

- carousel `output_root` removal is explicit,
- the script helper has both callable and CLI contracts,
- shell driver failure behavior is fail-closed through `set -euo pipefail`,
- CLI default and explicit `--out` tests are required,
- web submit boundary remains a non-goal and final validation target.

## Implementation Results

Completed in this session:

- app reasoner defaults now route through `runs_dir(...)` for article, topic, composite, research, and carousel,
- carousel `output_root` was removed from runtime config,
- CLI composite/reels defaults now route through `runs_dir(...)`,
- driver scripts now compute explicit `out_dir` values through `scripts/resolve_output_dir.py`,
- `.gitignore` now ignores `/out/`,
- tests cover resolver precedence, each migrated default, explicit override precedence, CLI defaults, and the script helper.

Validation:

```text
uv run --extra dev python -m compileall -q scripts/resolve_output_dir.py scripts/run_arxiv_one.py scripts/run_arxiv_batch.py scripts/run_random3.py scripts/run_batch_parallel.py
uv run --extra dev python -m pytest tests/planner/test_paths.py -q
13 passed in 1.09s
uv run --extra dev python -m pytest tests/test_composite_cli.py tests/test_reels_cli.py tests/test_carousel.py tests/test_research_to_reel.py tests/test_composite_overrides.py -q
57 passed in 1.87s
set -a; . ./.env; set +a; uv run --extra dev python -m pytest tests/planner tests/web -q
566 passed, 17 skipped in 26.13s
```
