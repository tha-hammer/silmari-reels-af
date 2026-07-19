---
date: 2026-07-19T16:05:54-04:00
researcher: TealFalcon
git_commit: 5f521539b7ea218087a6b9234f2e8bc7ae6c7901
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "reel-af canonical output path code seams"
tags: [research, codebase, reel-af, planner, eval, output-paths, railway]
status: complete
last_updated: 2026-07-19
last_updated_by: TealFalcon
beads: [AF-cii]
coordination:
  orchestrator: BrownFox
  sota_researcher: FrostyBear
  sota_doc_path: thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md
---

# Research: reel-af canonical output path code seams

**Date**: 2026-07-19 16:05:54 -04:00  
**Researcher**: TealFalcon  
**Git Commit**: 5f521539b7ea218087a6b9234f2e8bc7ae6c7901  
**Branch**: reel-af-a1-producer-impl  
**Repository**: silmari-reels-af  
**Beads**: AF-cii

## Research Question

Find the proper code seams and config-file locations to instantiate a canonical output root for resource files (A1 triples plus rendered mp4) and eval outputs. The root should be producer/app-relative by default, remote-render-ready for Railway, and overridable by config/env. This is research only; no source or config implementation changes.

## Summary

The reel-af package is a `src` layout Python package named `reel-af`, with console entrypoints `reel-af = reel_af.cli:main` and `reel-af-server = reel_af.app:main` (`pyproject.toml:44-46`). The running app already defines an app/root anchor as `_PROJECT_ROOT = Path(__file__).resolve().parents[2]`, which resolves to the repo root locally and `/app` in the Docker image (`src/reel_af/app.py:59`, `Dockerfile:37-52`). Config loaders use package-relative JSON files under `src/reel_af/render/config/`, with `planner/config.py` anchoring to `src/reel_af` via `Path(__file__).parents[1]` before reading `render/config/planner.json` (`src/reel_af/planner/config.py:17-23`).

The current A1 producer and consumer defaults are not config-resolved: `transcript_to_plan` writes to `/tmp/reel-af/transcript-to-plan/{run_id}` when `out_dir` is absent (`src/reel_af/app.py:1778-1791`), and `dsl_hooks_to_reels` writes to `/tmp/reel-af/dsl-hooks/{run_id}` when `out_dir` is absent (`src/reel_af/app.py:1646-1648`). The planner pipeline itself takes `out_dir` as required and writes `composite.ts.md`, `transcript.words.json`, `hook-plan.json`, plus rationale/debug sidecars under that directory (`src/reel_af/planner/pipeline.py:29-38`, `src/reel_af/planner/pipeline.py:141-179`). The DSL consumer writes resolved fetched artifacts, downloaded segments, base stitch intermediates, and final finish output under its work directory (`src/reel_af/app.py:1657-1659`, `src/reel_af/app.py:1708-1724`).

Eval output is explicit today: eval library functions only persist if an `out_dir` is supplied (`src/reel_af/planner/eval/runner.py:26-54`, `src/reel_af/planner/eval/runner.py:57-81`), `write_eval_result` writes a timestamped JSON file under that directory (`src/reel_af/planner/eval/runner.py:115-122`), and `write_eval_diff` writes to an explicit path (`src/reel_af/planner/eval/runner.py:125-131`). The eval CLI currently requires `--out-dir` or `--out` (`src/reel_af/planner/eval/runner.py:236-257`).

There is no existing producer-wide output-root env var. The closest existing knobs are narrower: carousel config has `output_root: "output"` (`src/reel_af/render/config/carousel.json:7-8`) and web carousel recreate uses `REEL_CAROUSEL_RECREATE_DIR` falling back to `tempfile.gettempdir()` (`web/server.py:641-643`). SOTA coordination with FrostyBear recommends `REEL_AF_OUTPUT_ROOT` plus config key `output_root`, with resolution order explicit call arg -> env -> config -> package/app-relative default, and Railway setting the env var to a persistent volume path.

Git state note: `src/reel_af/planner/eval/` and `tests/planner/eval/` exist on disk in this checkout, but `git status --short -- tests/planner/eval src/reel_af/planner/eval` reports both as untracked (`??`). So the eval fixture path below is the current golden-fixture location on disk, not a tracked/committed fixture in this checkout.

## Producer And App Root

`pyproject.toml` declares the package as `reel-af` and uses setuptools `src` discovery (`pyproject.toml:5-7`, `pyproject.toml:48-49`). It packages JSON config files under `reel_af.agents` and `reel_af.render` (`pyproject.toml:51-53`). The console entrypoints are:

| File:line | Current fact |
|---|---|
| `pyproject.toml:44-46` | `reel-af = reel_af.cli:main`; `reel-af-server = reel_af.app:main`. |
| `src/reel_af/app.py:90-115` | Constructs the Agent and `AgentRouter(prefix="reel")`. |
| `src/reel_af/app.py:1604-1618` | `dsl_hooks_to_reels` is registered with `@reel.reasoner()`. |
| `src/reel_af/app.py:1754-1764` | `transcript_to_plan` is registered with `@reel.reasoner()`. |
| `src/reel_af/app.py:1839-1841` | Router is included after all reasoners so they register into the served app. |
| `src/reel_af/app.py:1849-1854` | `main()` runs the app on `0.0.0.0:${PORT:-8002}`. |
| `main.py:1-13` | Root shim inserts `src` on `sys.path` and imports `reel_af.app.main`. |

Two useful anchors exist today:

| Anchor | File:line | Meaning for output-root design |
|---|---|---|
| App/repo root | `src/reel_af/app.py:59`, `src/reel_af/cli.py:31` | `_PROJECT_ROOT = Path(__file__).resolve().parents[2]`; locally this is the repo root, in Docker it is `/app`. This is the natural writable default anchor for app-relative artifacts such as `resources/...`. |
| Package module root | `src/reel_af/planner/config.py:17`, `src/reel_af/render/finish_defaults.py:19`, `src/reel_af/render/presets.py:19` | Existing config loaders anchor relative to the installed package files. This is the natural anchor for packaged read-only config, not for runtime-generated output. |

The Docker image sets `WORKDIR /app`, copies `src/` to `/app/src/`, installs the package, then copies the full app tree into `/app` (`Dockerfile:37-52`). That means the existing `_PROJECT_ROOT` pattern resolves to `/app` in Railway/container deployment.

## Config System

### Planner Config

`src/reel_af/planner/config.py` is a strict Pydantic schema over JSON defaults. `_CONFIG_PATH` points to `Path(__file__).parents[1] / "render" / "config" / "planner.json"` (`src/reel_af/planner/config.py:17`). `load_planner_defaults()` caches and returns `json.loads(_CONFIG_PATH.read_text())` (`src/reel_af/planner/config.py:20-23`). `_D = load_planner_defaults()` is captured at import time and `_v()` uses deep-copied JSON values as Pydantic field defaults (`src/reel_af/planner/config.py:26-31`). `PlannerConfig` forbids extra keys (`src/reel_af/planner/config.py:47-50`). `load_planner_config()` returns `PlannerConfig.model_validate(load_planner_defaults())` and is cached (`src/reel_af/planner/config.py:114-117`).

Existing `planner.json` keys:

| Key area | JSON file:lines | Typed fields |
|---|---|---|
| LLM model and timeouts | `src/reel_af/render/config/planner.json:2-6` | `model`, `llm_temperature`, `llm_connect_timeout_s`, `llm_request_timeout_s`, `llm_total_timeout_s` at `src/reel_af/planner/config.py:52-56`. |
| Register and duration bounds | `src/reel_af/render/config/planner.json:7-11` | `default_register`, `bounds_default` at `src/reel_af/planner/config.py:57-58`. |
| Planner repair and size limits | `src/reel_af/render/config/planner.json:12-19` | `max_repair_passes`, `verbatim_floor`, `max_transcript_chars`, `max_candidates`, `max_beats`, `max_repair_hint_chars`, `max_audio_bytes`, `max_audio_duration_s` at `src/reel_af/planner/config.py:59-66`. |
| Remote ASR | `src/reel_af/render/config/planner.json:20-50` | ASR timeouts, `self_verify`, `remote_asr_chain`, `allow_local_only_asr` at `src/reel_af/planner/config.py:67-73`. |
| Retention lint/rule thresholds | `src/reel_af/render/config/planner.json:51-64` | `r1_hook_window_s`, `r2_cadence_s`, `r4_max_gap_s`, `r8_min_token_overlap`, `r11_bait_patterns` at `src/reel_af/planner/config.py:75-79`. |

No planner config env override exists today. The loader reads exactly `planner.json` and validates it; there is no `os.getenv()` in `planner/config.py` (`src/reel_af/planner/config.py:5-17`, `src/reel_af/planner/config.py:114-117`).

Natural place for output keys:

| Proposed key | Natural current home | Why this home fits |
|---|---|---|
| `output_root` | `src/reel_af/render/config/planner.json` plus `PlannerConfig` | The A1 producer, eval gates, judge, lint, transcribe, and planner LLM already load `PlannerConfig` (`src/reel_af/planner/pipeline.py:12`, `src/reel_af/planner/eval/gates.py:20`, `src/reel_af/planner/eval/judge.py:14`, `src/reel_af/planner/transcribe.py:24`). |
| `artifacts_dir` | Same planner config, or a small shared output config if avoiding planner coupling from render reasoners | A1 triple and DSL rendered mp4 directories are part of the A1 producer/consumer resource contract. |
| `evals_dir` | Same planner config for minimal surface, or eval-specific config if evals should stay independent | Eval runner already sits under `src/reel_af/planner/eval/` and imports planner-side gates/config indirectly. |

The existing package-data setup already includes `reel_af.render` config JSON files (`pyproject.toml:51-53`), so adding fields to `planner.json` is packaging-compatible. Because `PlannerConfig.model_config` forbids extras (`src/reel_af/planner/config.py:47-50`), any config-key addition must be paired with typed fields or validation will fail.

### Other Config Files

| Config | Loader | Existing relevant keys |
|---|---|---|
| Carousel | `_CAROUSEL_CONFIG_PATH = Path(__file__).parent / "render" / "config" / "carousel.json"` and `_carousel_config()` reads JSON (`src/reel_af/app.py:117-130`) | `output_dir_prefix` and `output_root` are already in JSON (`src/reel_af/render/config/carousel.json:7-8`), but only feed `_default_carousel_output_dir()` (`src/reel_af/app.py:950-951`). |
| Finish | `_CONFIG_PATH = Path(__file__).parent / "config" / "finish.json"` and `load_finish_defaults()` caches JSON (`src/reel_af/render/finish_defaults.py:19-25`) | Geometry, captions, banner, lower-third, divider, image cut-ins, Whisper, encode, ASS styles (`src/reel_af/render/config/finish.json:2-97`); no output-root key. |
| Finish typed schema | `ReelFinishConfig` uses `load_finish_defaults()` and forbids extras (`src/reel_af/render/finish_config.py:20-27`, `src/reel_af/render/finish_config.py:77-80`) | Typed fields mirror `finish.json` (`src/reel_af/render/finish_config.py:82-166`); no output-root field. |
| Presets | `_PRESETS_PATH = Path(__file__).parent / "config" / "presets.json"` and `_all()` caches JSON (`src/reel_af/render/presets.py:19-24`) | Format presets for `middle-third-dynamic`, `horizontal-youtube-lowerthird`, and `carousel-default` (`src/reel_af/render/config/presets.json:2-39`); no output-root key. |
| Images | `_CONFIG_PATH = Path(__file__).parent / "config" / "images.json"` and `_image_config()` caches JSON (`src/reel_af/render/images.py:25-30`) | Image model default is env-overridable through `REEL_AF_IMAGE_MODEL` (`src/reel_af/render/images.py:49-50`); no output-root key. |
| Tunables | `_TUNABLES_PATH = Path(__file__).parent / "config" / "tunables.json"` and `load_tunables()` caches JSON (`src/reel_af/render/tunables.py:28-35`) | Per-job UI/render override spec (`src/reel_af/render/config/tunables.json:2-18`); not an operator filesystem config. |
| Recreate policy | Reads `recreate_config.json` with `Path(__file__).with_name(...)` (`src/reel_af/recreate.py:33-37`) | Has env knobs `REEL_AF_IMAGE_MODEL_HQ` and `REEL_AF_HQ_RECREATE_CAP` (`src/reel_af/recreate.py:28-44`); no canonical output-root key. |
| Web recreate output | `_recreate_out_dir()` | Uses `REEL_CAROUSEL_RECREATE_DIR` or `tempfile.gettempdir()` plus `RECREATE_OUTPUT_DIR` (`web/server.py:641-643`). This is a separate web-only output seam. |

### Existing Env Override Paths

There is no producer-wide `REEL_AF_OUTPUT_ROOT` or equivalent. Existing env paths are domain-specific:

| Env var | File:line | Current use |
|---|---|---|
| `AGENT_NODE_ID`, `AGENTFIELD_SERVER`, `AGENTFIELD_API_KEY`, `REEL_AF_MODEL`, `REEL_AF_API_KEY`, `REEL_AF_API_BASE`, `OPENROUTER_API_KEY` | `src/reel_af/app.py:90-110` | Agent/control-plane and LLM configuration. |
| `PORT` | `src/reel_af/app.py:1849-1854` | HTTP bind port. |
| `A1_ARTIFACTS_BASE` | `src/reel_af/app.py:1551-1591` | Resolves `a1://` artifact refs for co-located dev only, not output roots. |
| `CHROMIUM_PATH` | `src/reel_af/app.py:835-839` | Render browser path for composite reasoner. |
| `REEL_AF_IMAGE_MODEL` | `src/reel_af/render/images.py:49-50` | Image model override. |
| `REEL_AF_HQ_RECREATE_CAP`, `REEL_AF_IMAGE_MODEL_HQ` | `src/reel_af/recreate.py:28-44`, `src/reel_af/recreate.py:109-117` | Recreate policy/model overrides. |
| `REEL_CAROUSEL_RECREATE_DIR` | `web/server.py:641-643` | Web carousel recreate output root only. |
| `YTDLP_COOKIES_FILE`, `YTDLP_COOKIES_B64`, `YTDLP_PROXY_URL` | `src/reel_af/render/hooks.py:37-46` | Download helper secrets/proxy, with temp cookie materialization at `/tmp/reel-af-ytdlp-cookies.txt`. |

## Current Output Path Seams

### A1 Producer And Consumer

| File:line | Current | Proposed seam |
|---|---|---|
| `src/reel_af/app.py:1754-1764` | `transcript_to_plan(..., out_dir: str | None = None, *, llm=None, transcribe=None, artifact_writer=None)` exposes an optional `out_dir`. | Keep explicit `out_dir` as highest precedence; when absent, call shared resolver for resource artifact dir. |
| `src/reel_af/app.py:1778-1791` | Generates `run_id`, defaults `work` to `/tmp/reel-af/transcript-to-plan/{run_id}`, then passes `out_dir=work` to planner `plan()`. | Default should resolve to `<output_root>/<artifacts_dir>/transcript-to-plan/{run_id}` or equivalent under app-relative `resources/runs`. |
| `src/reel_af/planner/pipeline.py:29-38` | `plan(..., out_dir: str | Path, cfg: PlannerConfig | None = None)` requires `out_dir`. | No public default required here; caller owns the run dir. Optional helper can be used by callers, not by this lower-level writer. |
| `src/reel_af/planner/pipeline.py:99-111` | Hook plan embeds `composite_ref = str(Path(out_dir) / "composite.ts.md")` and then calls `_write_triple(out_dir, ...)`. | Keep path construction relative to resolved run dir; if future refs need remote URLs, `artifact_writer` remains the post-write seam in `transcript_to_plan`. |
| `src/reel_af/planner/pipeline.py:141-179` | `_write_triple()` creates `out_dir` and writes `composite.ts.md`, `transcript.words.json`, `hook-plan.json`, `mined-candidates.json`, `accepted-candidates.json`, `strategy.json`, `blueprint.json`. | Same filenames, only parent root changes. |
| `src/reel_af/app.py:1604-1618` | `dsl_hooks_to_reels(..., out_dir: str | None = None, *, fetch_segment=None, uploader=None, ...)` exposes optional `out_dir`. | Keep explicit `out_dir` as highest precedence; when absent, call same output resolver for rendered resource dir. |
| `src/reel_af/app.py:1646-1648` | Generates `run_id`, defaults `work` to `/tmp/reel-af/dsl-hooks/{run_id}`, then creates the directory. | Default should resolve to `<output_root>/<artifacts_dir>/dsl-hooks/{run_id}` or equivalent under app-relative `resources/runs`. |
| `src/reel_af/app.py:1657-1659` | Resolves/fetches `composite.ts.md`, words, and hook-plan into `work` when refs are http(s). | Use the resolved `work` root; fetched artifact filenames remain unchanged. |
| `src/reel_af/app.py:1708-1724` | Downloads segments into `work / "segments"`, stitches base under `work / "base"`, then `finish_reel(..., out_dir=work / "final")`. | Preserve subdirectory structure under resolved run dir. |
| `src/reel_af/render/footage_stitch.py:126-153` | `download_segments()` writes each segment as `out_dir / f"{segment_id}.mp4"`. | No new root needed; caller supplies resolved subdir. |
| `src/reel_af/render/footage_stitch.py:361-402` | `stitch_footage_reel()` creates `out_dir`, uses `out_dir / f"{safe_run_id}-stitch"`, writes `out_dir / f"{safe_run_id}.mp4"`, and may write stderr text. | No new root needed; caller supplies resolved subdir. |
| `src/reel_af/render/finish.py:204-224` | `finish_reel(..., out_dir: Optional[Path] = None)` defaults to `base.parent` if absent. | No root resolver needed inside finish; callers that own resource dirs should pass `out_dir`. |
| `src/reel_af/render/finish.py:242-267` | Writes `{run_id}.ass`, optional `{run_id}-images/`, and `final.mp4` under `out_dir`. | Preserve filenames under caller-supplied resolved finish dir. |

### Eval Runner

| File:line | Current | Proposed seam |
|---|---|---|
| `src/reel_af/planner/eval/runner.py:26-54` | `score_blueprint(..., out_dir=None)` writes only if `out_dir` is not `None`. | Keep library no-write behavior or make default explicit in CLI only; use resolver when an eval output dir is needed. |
| `src/reel_af/planner/eval/runner.py:57-81` | `score_artifact_dir(..., out_dir=None)` reads a fixture/artifact dir and writes only if `out_dir` is not `None`. | Same. |
| `src/reel_af/planner/eval/runner.py:115-122` | `write_eval_result(result, out_dir)` creates `out_dir` and writes `{slug(run_id)}.json`. | Parent should be resolved eval output dir, e.g. `<output_root>/<evals_dir>`. |
| `src/reel_af/planner/eval/runner.py:125-131` | `write_eval_diff(diff, out_path)` writes to an explicit path. | CLI can default `--out` to resolved eval dir plus a slug; lower writer can remain explicit. |
| `src/reel_af/planner/eval/runner.py:202-229` | CLI command handlers call scoring without `out_dir`, then call writers with CLI args. | Natural seam for defaulting absent CLI output args to config/env-resolved eval dir. |
| `src/reel_af/planner/eval/runner.py:236-257` | CLI requires `--out-dir` for score commands and `--out` for diff. | Minimal default change: make these optional and resolve to `<output_root>/<evals_dir>` when absent. |

### Existing Non-A1 Runtime Defaults

These are not the A1 triple/render mp4 path, but they are the other scattered runtime output defaults in the producer app:

| File:line | Current | Notes |
|---|---|---|
| `src/reel_af/app.py:456-460` | `article_to_reel` defaults to `Path.cwd() / "output" / f"article-{run_id}"`. | Same app-level pattern as topic/research. |
| `src/reel_af/app.py:547-551` | `topic_to_reel` defaults to `Path.cwd() / "output" / f"topic-{run_id}"`. | Same. |
| `src/reel_af/app.py:835-837` | `composite_to_reel` defaults to `Path.cwd() / "output" / f"composite-{run_id}"`. | Same. |
| `src/reel_af/app.py:950-951` | `_default_carousel_output_dir()` uses `Path.cwd() / _CAROUSEL_OUTPUT_ROOT / f"{_CAROUSEL_OUTPUT_DIR_PREFIX}-{run_id}"`. | The only existing JSON-driven `output_root`, but carousel-only. |
| `src/reel_af/app.py:1011-1013` | `research_to_carousel` uses explicit `out_dir` or `_default_carousel_output_dir(run_id)`. | Already config-mediated, but not env-mediated. |
| `src/reel_af/app.py:1286-1290` | `research_to_reel` defaults to `Path.cwd() / "output" / f"research-{run_id}"`. | Same app-level pattern. |
| `src/reel_af/cli.py:148-163` | CLI sidecar dir returns explicit out dir, derives from absolute `video_path` containing `output`, or falls back to `Path.cwd() / "output" / run_id`. | Local result sidecar path, not worker artifact root. |
| `src/reel_af/cli.py:340` | `reel-af composite` defaults to `_PROJECT_ROOT / "out" / "composite"`. | Local CLI-only output root. |
| `src/reel_af/cli.py:447` | `reel-af reels` defaults to `_PROJECT_ROOT / "out" / "reels" / preset`. | Local CLI-only output root. |
| `web/server.py:641-643` | Web recreate defaults to `Path(os.getenv("REEL_CAROUSEL_RECREATE_DIR", tempfile.gettempdir())) / RECREATE_OUTPUT_DIR`. | Existing env override is web/carousel-specific. |

### Hardcoded `/tmp`, `~`, And Local-Path Tests

| File:line | Current literal | Relevance |
|---|---|---|
| `src/reel_af/app.py:1647` | `/tmp/reel-af/dsl-hooks/{run_id}` | Current A1 rendered-resource default. |
| `src/reel_af/app.py:1779` | `/tmp/reel-af/transcript-to-plan/{run_id}` | Current A1 triple default. |
| `src/reel_af/render/hooks.py:37-46` | `/tmp/reel-af-ytdlp-cookies.txt` | Temp cookie materialization, not resource output. Keep separate from canonical artifact root unless operator wants all temp files moved. |
| `src/reel_af/planner/ingest.py:230` | `tempfile.TemporaryDirectory(prefix="reel_af_planner_whisper_")` | True temporary ASR workspace, not committed/runtime artifact root. |
| `src/reel_af/planner/transcribe.py:358-379` | `materialize_audio(..., tmp_root=None)` uses `tempfile.gettempdir()` and `reel_af_asr_{uuid}` when no temp root is supplied. | True ASR materialization temp dir, separate from canonical artifact output. |
| `tests/planner/eval/fixtures/BASELINE-0/hook-plan.json:4` | Absolute `/tmp/claude-1000/.../scratchpad/e2e_out/composite.ts.md` inside the golden fixture file on disk. | Golden fixture content contains a historical absolute path string. Eval scoring reads local fixture files directly, but the embedded ref is still observable fixture content. |
| `tests/planner/test_serialize.py:157-181` | Builds hook plan with `composite_ref="/tmp/out/composite.ts.md"` and asserts that exact value. | Unit test pins serializer preservation of passed refs, not default root. |
| `tests/planner/test_serialize.py:191-197` | Reuses `/tmp/out/composite.ts.md`. | Same. |
| `tests/planner/test_ingest.py:234-243` | `"/tmp/local.mp4"` expected to produce no YouTube id. | Parser behavior test, not output-root default. |
| `tests/test_ingest.py:74` | Rejects `"/tmp/source.mp4"` as bad input. | Intake URL/local-path guard test. |
| `tests/web/test_dsl_hooks_submit.py:175-186` | Rejects artifact refs such as `"/tmp/x.ts.md"` and `"~/x.ts.md"`. | Web boundary intentionally forbids filesystem artifact refs before CP dispatch. |
| `tests/web/test_dsl_hooks_poll.py:41-44`, `tests/web/test_dsl_hooks_poll.py:103-128` | Uses `"/tmp/node/out.mp4"` and `"/tmp/node/a.mp4"` to assert node-local paths are not exposed for delivery-required DSL hooks. | Delivery policy test. |
| `deploy/RAILWAY-RUNBOOK.md:51`, `deploy/RAILWAY-RUNBOOK.md:97`, `docs/railway-deployment.md:48`, `docs/railway-deployment.md:166` | `cd ~/ntm_Dev/silmari-agentfield-system` | Human runbook checkout paths, not runtime output roots. |
| `scripts/run_batch.sh:17-33`, `scripts/run_batch.sh:78-79` | Creates `output/batch`, sends article jobs with `out_dir="output/batch/${genre}"`, and reports `output/batch/`. | Driver-script relative runtime output. |
| `scripts/run_arxiv_one.py:24-32` | Uses `out_dir = f"output/scientific/{label}"` and sends it to `article_to_reel`. | Driver-script relative runtime output. |
| `scripts/run_arxiv_batch.py:26-34` | Uses `out = f"output/scientific/{label}"` and sends it as `out_dir`. | Driver-script relative runtime output. |
| `scripts/run_random3.py:25-28` | Uses `out_dir = f"output/random3/{genre}"` and sends it as `out_dir`. | Driver-script relative runtime output. |
| `scripts/run_batch_parallel.py:31-34` | Uses `out_dir = f"output/batch/{genre}"` and sends it as `out_dir`. | Driver-script relative runtime output. |

Many tests pass pytest `tmp_path` as explicit `out_dir`; those are healthy injection seams rather than hardcoded runtime defaults. Examples include `tests/planner/test_reasoner.py:99-106`, `tests/planner/test_pipeline.py:171-180`, and `tests/planner/eval/test_gates_runner.py:337-360`.

## Golden Versus Runtime Outputs

Golden eval fixture files live on disk under `tests/planner/eval/fixtures/BASELINE-0`, but the eval tree is currently untracked in this checkout:

| Path | Evidence |
|---|---|
| `tests/planner/eval/fixtures/BASELINE-0/composite.ts.md` | Present on disk as a golden fixture file. |
| `tests/planner/eval/fixtures/BASELINE-0/hook-plan.json` | Present on disk; line 4 contains an absolute historical `/tmp/.../composite.ts.md` ref. |
| `tests/planner/eval/fixtures/BASELINE-0/transcript.words.json` | Present on disk as a golden fixture file. |
| `tests/planner/eval/test_gates_runner.py:35-37` | Test defines `BASELINE = Path(__file__).resolve().parent / "fixtures" / "BASELINE-0"`. |
| `tests/planner/eval/test_gates_runner.py:337-360` | Baseline fixture is scored and result JSON is written to `tmp_path`, not beside the fixture. |
| Git state | `git status --short -- tests/planner/eval src/reel_af/planner/eval` reports `?? tests/planner/eval/` and `?? src/reel_af/planner/eval/`; `git ls-files tests/planner/eval src/reel_af/planner/eval` returns no tracked paths. |

Runtime-generated outputs currently land in caller/default output dirs:

| Runtime output | Current location behavior |
|---|---|
| A1 triple resources | Explicit `transcript_to_plan(out_dir=...)` or `/tmp/reel-af/transcript-to-plan/{run_id}` (`src/reel_af/app.py:1778-1791`). |
| A1 rendered mp4 resources | Explicit `dsl_hooks_to_reels(out_dir=...)` or `/tmp/reel-af/dsl-hooks/{run_id}` with final mp4 under `work/final/final.mp4` (`src/reel_af/app.py:1646-1648`, `src/reel_af/app.py:1716-1724`, `src/reel_af/render/finish.py:263-278`). |
| Eval result JSON | Explicit `out_dir`; writer creates `{slug(run_id)}.json` (`src/reel_af/planner/eval/runner.py:115-122`). |
| Eval diff JSON | Explicit `out_path` (`src/reel_af/planner/eval/runner.py:125-131`). |
| Legacy app reels | `Path.cwd() / "output" / ...` unless explicit `out_dir` (`src/reel_af/app.py:456-460`, `src/reel_af/app.py:547-551`, `src/reel_af/app.py:835-837`, `src/reel_af/app.py:1286-1290`). |
| Local CLI composite/reels | `_PROJECT_ROOT / "out" / ...` unless explicit `--out` (`src/reel_af/cli.py:340`, `src/reel_af/cli.py:447`). |
| Driver scripts | Relative `output/...` directories are passed to article jobs by batch/arxiv/random scripts (`scripts/run_batch.sh:17-33`, `scripts/run_arxiv_one.py:24-32`, `scripts/run_arxiv_batch.py:26-34`, `scripts/run_random3.py:25-28`, `scripts/run_batch_parallel.py:31-34`). |

Ignore coverage today:

| File:line | Current coverage | Gap |
|---|---|---|
| `.gitignore:18-19` | Ignores `output/`. | Does not ignore `out/`, `resources/`, `resources/runs/`, `resources/evals/`, or any proposed canonical output root. |
| `.railwayignore:1-11` | Excludes `.git/`, `.venv/`, `node_modules`, `output/`, `out/`, pyc/cache dirs from Railway build context. | Does not exclude `resources/` or future canonical generated roots. |
| `docs/railway-deployment.md:216` | Runbook warns that uploading `node_modules`/`.venv`/`output` makes the Railway context large. | If `resources/` is introduced locally, runbooks/ignore should account for it. |

If the default becomes app-relative `resources/...`, ignore additions should cover runtime-generated resource and eval outputs without touching committed fixtures under `tests/**/fixtures`. Minimal ignore candidates are `/resources/runs/` and `/resources/evals/`; broader `/resources/` is simpler but leaves no room for committed non-runtime resources under that tree.

## Seam Table

| File:line | Current | Proposed |
|---|---|---|
| `src/reel_af/app.py:59` | `_PROJECT_ROOT = Path(__file__).resolve().parents[2]`. | Reuse as app-relative writable default anchor. |
| `src/reel_af/cli.py:31` | CLI has same `_PROJECT_ROOT` pattern. | Keep CLI defaults aligned with app output resolver if CLI outputs move. |
| `src/reel_af/planner/config.py:17-23` | Static package-relative `planner.json` loader. | Add typed output config fields and a resolver that applies env override. |
| `src/reel_af/render/config/planner.json:1-65` | Planner JSON has model, ASR, lint, limits; no output root. | Add `output_root`, `artifacts_dir`, `evals_dir` or equivalent. |
| `src/reel_af/planner/config.py:47-50` | `PlannerConfig` forbids extra keys. | Any added JSON keys must be added to `PlannerConfig`. |
| `src/reel_af/app.py:1778-1791` | `transcript_to_plan` defaults to `/tmp/reel-af/transcript-to-plan/{run_id}`. | Resolve `work` from explicit `out_dir` or config/env root, e.g. `resources/runs/transcript-to-plan/{run_id}` locally and Railway volume path remotely. |
| `src/reel_af/planner/pipeline.py:99-111` | Builds `composite_ref` from `Path(out_dir)`. | Keep using resolved run dir. |
| `src/reel_af/planner/pipeline.py:141-179` | Writes triple and sidecars under `out_dir`. | Keep filenames; only default parent changes. |
| `src/reel_af/app.py:1646-1648` | `dsl_hooks_to_reels` defaults to `/tmp/reel-af/dsl-hooks/{run_id}`. | Resolve `work` from explicit `out_dir` or config/env root, e.g. `resources/runs/dsl-hooks/{run_id}` locally and Railway volume path remotely. |
| `src/reel_af/app.py:1708-1724` | Uses `work / "segments"`, `work / "base"`, `work / "final"`. | Preserve subdirs under resolved `work`. |
| `src/reel_af/render/finish.py:223-267` | `finish_reel` defaults to `base.parent`, writes `final.mp4` under `out_dir`. | Leave lower-level default; caller should pass resolved `work / "final"`. |
| `src/reel_af/planner/eval/runner.py:26-54` | `score_blueprint` writes only with explicit `out_dir`. | Keep library optional, or default only through CLI wrapper to resolved eval dir. |
| `src/reel_af/planner/eval/runner.py:57-81` | `score_artifact_dir` writes only with explicit `out_dir`. | Same; CLI can resolve eval dir. |
| `src/reel_af/planner/eval/runner.py:115-122` | `write_eval_result` writes one JSON to supplied dir. | Call with resolved eval dir by default at the CLI/driver seam. |
| `src/reel_af/planner/eval/runner.py:125-131` | `write_eval_diff` writes to supplied path. | CLI can default absent `--out` to resolved eval dir plus generated diff filename. |
| `src/reel_af/planner/eval/runner.py:236-257` | Eval CLI requires `--out-dir` / `--out`. | Make defaults optional if canonical eval output is desired by command-line users. |
| `scripts/run_batch.sh:17-33` | Driver passes relative `output/batch/${genre}` as `out_dir`. | Either keep explicit override behavior or route driver default through the same resolver. |
| `scripts/run_arxiv_one.py:24-32` | Driver passes relative `output/scientific/{label}` as `out_dir`. | Same. |
| `scripts/run_arxiv_batch.py:26-34` | Driver passes relative `output/scientific/{label}` as `out_dir`. | Same. |
| `scripts/run_random3.py:25-28` | Driver passes relative `output/random3/{genre}` as `out_dir`. | Same. |
| `scripts/run_batch_parallel.py:31-34` | Driver passes relative `output/batch/{genre}` as `out_dir`. | Same. |
| `src/reel_af/render/config/carousel.json:7-8` | Carousel-only `output_dir_prefix` and `output_root`. | Either leave as carousel-specific or migrate to shared resolver after A1 resource root lands. |
| `src/reel_af/app.py:950-951` | Carousel output uses `Path.cwd() / _CAROUSEL_OUTPUT_ROOT / prefix-run_id`. | Potential follow-up: use shared output resolver for carousel too. |
| `web/server.py:641-643` | Web recreate uses `REEL_CAROUSEL_RECREATE_DIR` or temp dir. | Potential follow-up: align with shared output root or keep as web-specific cache/temp path. |
| `.gitignore:18-19` | Ignores `output/` only. | Add proposed generated roots, especially `/resources/runs/` and `/resources/evals/`; consider `/out/` because CLI already writes there. |
| `.railwayignore:5-6` | Excludes `output/` and `out/`. | Add `/resources/` or specific generated subdirs so local artifacts are not uploaded. |

## Minimal Seam Set

The minimal implementation seam set is:

1. Add a single output resolver near planner config, for example in `src/reel_af/planner/config.py` or a sibling `src/reel_af/planner/paths.py`, because `planner.json` and `PlannerConfig` are already the A1 producer config surface (`src/reel_af/planner/config.py:17-23`, `src/reel_af/planner/config.py:47-50`, `src/reel_af/planner/config.py:114-117`).
2. Add strict config fields to `PlannerConfig` and `src/reel_af/render/config/planner.json`: `output_root`, `artifacts_dir`, and `evals_dir`. Since extra keys are forbidden, schema and JSON must change together (`src/reel_af/planner/config.py:47-50`).
3. Resolve paths in this order: explicit `out_dir` argument, `REEL_AF_OUTPUT_ROOT` env var, config-file value, app-relative default under `_PROJECT_ROOT / "resources"` or `_PROJECT_ROOT / "resources" / "runs"` depending on whether `output_root` is the family root or run-artifact root. FrostyBear's SOTA coordination recommends `REEL_AF_OUTPUT_ROOT` / `output_root` and a local layout of `resources/runs/<run_id>/` and `resources/evals/<eval_id>/`.
4. Update only the two A1 app defaults first: `transcript_to_plan` at `src/reel_af/app.py:1778-1791` and `dsl_hooks_to_reels` at `src/reel_af/app.py:1646-1648`. Keep their signatures and explicit `out_dir` override behavior unchanged.
5. Leave `plan(out_dir=...)`, `_write_triple()`, `download_segments()`, `stitch_footage_reel()`, and `finish_reel()` as lower-level writers that receive a parent directory from their caller (`src/reel_af/planner/pipeline.py:29-38`, `src/reel_af/planner/pipeline.py:141-179`, `src/reel_af/render/footage_stitch.py:126-153`, `src/reel_af/render/footage_stitch.py:361-402`, `src/reel_af/render/finish.py:204-224`).
6. Add eval defaulting at the CLI/driver seam, not necessarily in the pure scoring functions: the eval CLI currently requires output paths (`src/reel_af/planner/eval/runner.py:236-257`), while library functions intentionally only write when `out_dir` is supplied (`src/reel_af/planner/eval/runner.py:26-54`, `src/reel_af/planner/eval/runner.py:57-81`).
7. Add ignore coverage for the new generated roots. `.gitignore` currently ignores only `output/` (`.gitignore:18-19`); `.railwayignore` currently excludes `output/` and `out/` (`.railwayignore:5-6`).

Recommended default resolution shape:

```text
explicit out_dir
  -> REEL_AF_OUTPUT_ROOT
  -> PlannerConfig.output_root
  -> _PROJECT_ROOT / "resources"

resource run dir:
  output_root / artifacts_dir / "<workflow>-<run_id>"

eval dir:
  output_root / evals_dir
```

For Railway, set `REEL_AF_OUTPUT_ROOT` to the mounted persistent volume root, for example `/data/reel-af`, with resource runs under `/data/reel-af/runs` and evals under `/data/reel-af/evals`. If the team chooses to define `REEL_AF_OUTPUT_ROOT` as the run-artifact root itself, then pair it with a separate eval root key/env or define evals as a sibling with clear normalization rules.

## Workflow Closure Map

No structured ClosureMap is emitted for this research artifact. This pass maps existing code/config seams and future insertion points, not a single behavior change being implemented in this session. The current source-to-sink path evidence is captured in the seam tables above:

```text
transcript_to_plan -> plan(out_dir=work) -> _write_triple(work) -> composite/words/hook refs
dsl_hooks_to_reels -> resolve refs into work -> download segments -> stitch base -> finish final.mp4
eval CLI/library -> score artifacts/blueprint -> write_eval_result/write_eval_diff when output path is supplied
```

## Historical Context

Prior UI/config research documented the split between safe job controls, preset metadata, and operator/local paths, and specifically noted that `out_dir` is internal/operator-facing rather than a browser-visible option (`thoughts/searchable/shared/research/2026-07-11-12-59-reels-af-ui-configuration-options.md`). Current web tests preserve that boundary by rejecting `out_dir` as an unsupported composite input (`tests/web/test_submit.py:330-345`) and ensuring UI preset details omit local/operator paths (`tests/web/test_index_contract.py:143-149`).

FrostyBear's Agent Mail SOTA coordination on 2026-07-19 recommended `REEL_AF_OUTPUT_ROOT` / `output_root`, resolution order explicit arg -> env -> config -> app-relative default, local layout `resources/runs` and `resources/evals`, and Railway using a persistent volume path. This seam map found no existing producer-wide env/config name that should override that recommendation.

## Open Questions

- Should `output_root` mean the family root containing both `runs/` and `evals/`, or should it mean the resource-run root itself? The code seams support either, but a family root avoids a second eval-root env var.
- Should carousel and legacy article/topic/composite/research defaults migrate immediately, or stay as follow-up after the A1 triple/mp4 and eval seams land?
- Should the `BASELINE-0/hook-plan.json` absolute `/tmp/...` `composite_ref` be normalized before the eval fixture is promoted/tracked, or preserved as historical fixture content while eval gates continue to read local fixture files directly?
