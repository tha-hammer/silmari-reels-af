---
date: 2026-07-19T17:04:08-04:00
researcher: Codex/FrostyBear
git_commit: db55855a6ad5eb06a3c0b169406f32cae030ad3e
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "AF-cm9 remaining reel-af output path seams after A1 resolver migration"
tags: [research, codebase, output-paths, resolver, reel-af, carousel, cli, scripts]
status: complete
last_updated: 2026-07-19
last_updated_by: Codex/FrostyBear
beads: [AF-cm9]
---

# Research: AF-cm9 Remaining Output Path Seams

**Date**: 2026-07-19 17:04:08 -04:00  
**Researcher**: Codex/FrostyBear  
**Git Commit**: db55855a6ad5eb06a3c0b169406f32cae030ad3e  
**Branch**: reel-af-a1-producer-impl  
**Repository**: silmari-reels-af  
**Beads**: AF-cm9

## Research Question

Verify and extend the non-A1 output path seam inventory after Phase 1. Confirm current behavior for legacy app reasoners, carousel, CLI defaults, and driver scripts, and identify how carousel's own `output_root` relates to the shared resolver.

## Summary

Phase 1 is present in this checkout. The shared resolver lives in `src/reel_af/planner/paths.py`: `resolve_output_root()` honors explicit root, `REEL_AF_OUTPUT_ROOT`, planner config `output_root`, then `_PROJECT_ROOT / "resources"`; `runs_dir()` derives `output_root / artifacts_dir / "<workflow>-<run_id>"`; `evals_dir()` derives `output_root / evals_dir` (`src/reel_af/planner/paths.py:10`, `src/reel_af/planner/paths.py:15`, `src/reel_af/planner/paths.py:33`, `src/reel_af/planner/paths.py:53`). Planner config already contains `output_root: "resources"`, `artifacts_dir: "runs"`, and `evals_dir: "evals"` (`src/reel_af/render/config/planner.json:7`), and the strict schema includes matching fields (`src/reel_af/planner/config.py:52`).

The A1 defaults are already migrated and should not be re-touched: `dsl_hooks_to_reels` defaults to `runs_dir("dsl-hooks", run_id)` and `transcript_to_plan` defaults to `runs_dir("transcript-to-plan", run_id)` (`src/reel_af/app.py:1648`, `src/reel_af/app.py:1780`). Eval CLI defaults are also already routed through `evals_dir()` for score and diff outputs (`src/reel_af/planner/eval/runner.py:210`, `src/reel_af/planner/eval/runner.py:223`, `src/reel_af/planner/eval/runner.py:231`).

The remaining runtime defaults still outside the resolver are:

| Seam | Current behavior | Explicit override contract |
|---|---|---|
| `article_to_reel` | `Path(out_dir)` or `Path.cwd() / "output" / f"article-{run_id}"`; then creates `media/` and passes both paths to `_render_downstream` (`src/reel_af/app.py:441`, `src/reel_af/app.py:459`, `src/reel_af/app.py:485`). | `out_dir` wins. |
| `topic_to_reel` | `Path(out_dir)` or `Path.cwd() / "output" / f"topic-{run_id}"`; then shared downstream render (`src/reel_af/app.py:530`, `src/reel_af/app.py:550`, `src/reel_af/app.py:641`). | `out_dir` wins. |
| `composite_to_reel` | `Path(out_dir)` or `Path.cwd() / "output" / f"composite-{run_id}"`; passes `out_path` to `_run_composite_reels` (`src/reel_af/app.py:817`, `src/reel_af/app.py:837`, `src/reel_af/app.py:844`). | `out_dir` wins. |
| `research_to_reel` | `Path(out_dir)` or `Path.cwd() / "output" / f"research-{run_id}"`; then creates `media/` and calls the renderer (`src/reel_af/app.py:1221`, `src/reel_af/app.py:1289`, `src/reel_af/app.py:1316`). | `out_dir` wins. |
| `research_to_carousel` | `Path(out_dir)` or `_default_carousel_output_dir(run_id)`; slide rendering receives `run_dir` and the result returns `"out_dir"` (`src/reel_af/app.py:986`, `src/reel_af/app.py:1013`, `src/reel_af/app.py:1041`, `src/reel_af/app.py:1061`). | `out_dir` wins. |
| `_default_carousel_output_dir` | `Path.cwd() / _CAROUSEL_OUTPUT_ROOT / f"{_CAROUSEL_OUTPUT_DIR_PREFIX}-{run_id}"` (`src/reel_af/app.py:951`). | Fallback-only helper. |
| CLI `composite` | `--out` or `_PROJECT_ROOT / "out" / "composite"` (`src/reel_af/cli.py:320`, `src/reel_af/cli.py:340`). | `--out` wins. |
| CLI `reels` | `--out` or `_PROJECT_ROOT / "out" / "reels" / preset`; output reels are created below that work dir (`src/reel_af/cli.py:400`, `src/reel_af/cli.py:447`, `src/reel_af/cli.py:476`). | `--out` wins. |
| Driver scripts | Shell/Python scripts construct `output/batch`, `output/scientific`, or `output/random3` and pass them as `out_dir`, which bypasses reasoner defaults (`scripts/run_batch.sh:22`, `scripts/run_batch.sh:33`, `scripts/run_arxiv_one.py:27`, `scripts/run_arxiv_batch.py:27`, `scripts/run_random3.py:26`, `scripts/run_batch_parallel.py:32`). | Script-supplied `out_dir` wins at the reasoner boundary. |

## Carousel Root Boundary

Carousel has the only pre-existing non-planner output-root key. `app.py` loads `src/reel_af/render/config/carousel.json`, reads `_CAROUSEL_OUTPUT_ROOT` from JSON key `output_root`, and `_default_carousel_output_dir()` uses that value as a cwd-relative root (`src/reel_af/app.py:118`, `src/reel_af/app.py:131`, `src/reel_af/app.py:951`; `src/reel_af/render/config/carousel.json:8`).

For AF-cm9, carousel's generated image output should fold into the shared resolver for default directory selection: `research_to_carousel(out_dir=None)` should resolve to `runs_dir("carousel", run_id)` or the equivalent shared run directory while preserving `output_dir_prefix` as the workflow/prefix signal. The existing carousel `output_root` should not remain an independent root for runtime generated outputs, because that would preserve a second cwd-relative output policy and bypass `REEL_AF_OUTPUT_ROOT`. Other carousel config keys, especially `output_dir_prefix`, `run_id_hex_chars`, prompt text, and slide bounds, remain carousel-specific.

## CLI And Script Notes

The CLI has two distinct path classes. The `article` and `topic` commands submit to AgentField and only include `out_dir` when `--out` is provided; their local `result.json` sidecar placement is computed by `_sidecar_dir()` from explicit `--out`, returned `video_path`, or fallback `Path.cwd() / "output" / run_id` (`src/reel_af/cli.py:148`, `src/reel_af/cli.py:244`, `src/reel_af/cli.py:298`). The local rendering commands `composite` and `reels` are the AF-cm9 CLI defaults in scope because they construct local work dirs themselves (`src/reel_af/cli.py:340`, `src/reel_af/cli.py:447`).

Driver scripts currently pass explicit relative `out_dir` values into article jobs. Because the reasoners correctly honor explicit `out_dir`, migrating driver defaults requires changing the script-computed defaults themselves, not only changing app defaults.

## Web Submit Boundary

The direct app reasoner signatures still expose `out_dir`, but the browser submit boundary does not. Web submit canonicalization uses target-specific allowlists and rejects unsupported input fields before creating rows or dispatching to the control plane (`web/reel_jobs.py:99`, `web/reel_jobs.py:129`, `web/reel_jobs.py:270`). Tests pin that composite rejects `out_dir` and other local/operator fields (`tests/web/test_submit.py:330`, `tests/web/test_submit.py:351`) and that DSL hooks rejects filesystem artifact refs such as `/tmp/x.ts.md` and `~/x.ts.md` (`tests/web/test_dsl_hooks_submit.py:175`). AF-cm9 should not alter this boundary.

## Ignore Coverage

`.gitignore` currently ignores `output/`, `/resources/runs/*`, and `/resources/evals/*`, with `.gitkeep` exceptions (`.gitignore:19`, `.gitignore:20`, `.gitignore:22`). It does not ignore `/out/`, even though CLI defaults currently write there. `.railwayignore` already excludes `output/`, `out/`, and `/resources/` from Railway build context (`.railwayignore:5`, `.railwayignore:6`, `.railwayignore:7`).

## Verification Notes

The ResearchSemgrep verifier scripts referenced by the imported research workflow were not present in this checkout (`SAI/skills/ResearchSemgrep/verify-citations.ts` and `closure-map.ts` were missing), so citation verification used targeted reads, `rg`, and two read-only explorer passes. No source implementation files were changed during this research phase.

## Workflow Closure Map

No structured ClosureMap is emitted for this artifact. This research maps a set of path-selection seams rather than one end-to-end asynchronous production workflow. The concrete seam-to-sink map and contracts are appended to the AF-cm9 implementation plan.

## Historical Context

This artifact builds on:

- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-code-seams.md` - original code seam inventory before Phase 1 implementation.
- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md` - SOTA convention recommending `REEL_AF_OUTPUT_ROOT`, app-relative `resources`, `resources/runs`, and `resources/evals`.

## Open Questions

- Whether the obsolete carousel `output_root` JSON key should be removed immediately or kept as an inert compatibility key. The runtime default should still use the shared resolver either way.
- Whether CLI article/topic `_sidecar_dir()` fallback should move in this bead. It is a local metadata sidecar after remote execution, not one of the explicit `cli.py:340`/`cli.py:447` defaults in AF-cm9 scope.
