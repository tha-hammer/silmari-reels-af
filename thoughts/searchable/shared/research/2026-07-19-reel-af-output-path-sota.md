---
date: 2026-07-19T16:08:17-04:00
researcher: Codex/FrostyBear
git_commit: 5f521539b7ea218087a6b9234f2e8bc7ae6c7901
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "state of the art for canonical output/resource/eval paths in a remote-rendered reel-af pipeline"
tags: [research, sota, output-paths, resources, evals, railway, containers, configuration]
status: complete
related_beads: [AF-d77]
---

# Research: Canonical Output, Resource, and Eval Paths

## Research Question

What is the right output-root convention for the reel-af A1 producer and remote-rendered
pipeline, given that the producer writes `composite.ts.md`, `hook-plan.json`,
`transcript.words.json`, and sidecars such as `blueprint.json`, `strategy.json`,
`mined-candidates.json`, and `accepted-candidates.json`, while the eventual renderer runs
remotely on Railway?

The constraint is explicit: the output path must be relative to the reel-af producer by
default, remote-first, and overridable. It must not be a developer home path and must not
default to `/tmp`.

## Summary

Use a single canonical generated-artifact root for the producer, then derive run and eval
subtrees from it:

```text
<producer_root>/resources/
  runs/<run_id>/
    composite.ts.md
    transcript.words.json
    hook-plan.json
    blueprint.json
    strategy.json
    mined-candidates.json
    accepted-candidates.json
    media/
    render/
  evals/
    <eval_run_id>.json
    diffs/<left>__<right>.json
```

Recommended config contract:

```text
paths.output_root       # config-file key; default: <producer_root>/resources
REEL_AF_OUTPUT_ROOT     # deployment/env override for paths.output_root
```

Resolution order for service defaults:

```text
REEL_AF_OUTPUT_ROOT
  > config-file paths.output_root
  > <producer_root>/resources
```

Trusted internal callers and tests may still pass an explicit `out_dir`, but browser/API
submissions must not. The web boundary already rejects `out_dir` and local filesystem refs
for production submit paths.

On Railway, set `REEL_AF_OUTPUT_ROOT` to a path backed by a Railway Volume, for example
`/app/resources` if the volume is mounted at `/app/resources`, or `/data/reel-af/resources`
if the volume is mounted at `/data`. The application should then derive
`resources/runs` and `resources/evals` from that root. The producer should return logical
artifact refs such as `a1://runs/<run_id>/composite.ts.md` or HTTP(S) artifact URLs, not
absolute container paths.

The important distinction is:

- Runtime generated artifacts live under `resources/runs` and `resources/evals`.
- Large binary media is ignored by git and persisted through a Railway Volume or object
  storage.
- Small structured JSON/Markdown outputs are generated artifacts unless deliberately
  promoted into committed regression fixtures under `tests/**/fixtures` or
  `tests/**/golden`.
- Browser-deliverable mp4s should use object storage or a serving route. A producer-local
  volume is durable storage for the service, not a portable cross-service API.

## Current Local State

The codebase has multiple output seams today:

| Surface | Current behavior | Evidence |
|---|---|---|
| Article reasoner | Defaults to `Path.cwd() / "output" / f"article-{run_id}"`. | `src/reel_af/app.py:456-462` |
| Topic reasoner | Defaults to `Path.cwd() / "output" / f"topic-{run_id}"`. | `src/reel_af/app.py:547-553` |
| Composite URL reasoner | Defaults to `Path.cwd() / "output" / f"composite-{run_id}"`; final mp4 is uploaded to bucket when configured. | `src/reel_af/app.py:835-869` |
| Transcript-to-plan producer | Defaults to `/tmp/reel-af/transcript-to-plan/<run_id>` when `out_dir` is absent, then passes that work dir to the A1 planner. | `src/reel_af/app.py:1754-1798` |
| DSL-hooks renderer | Documents that Railway cannot see A1 local filesystem artifacts, but still defaults its work dir to `/tmp/reel-af/dsl-hooks/<run_id>` when `out_dir` is absent. | `src/reel_af/app.py:1545-1550`, `src/reel_af/app.py:1646-1648` |
| A1 producer | Requires caller-supplied `out_dir`; writes the triple plus sidecars into that directory and returns string refs. | `src/reel_af/planner/pipeline.py:29-43`, `src/reel_af/planner/pipeline.py:141-179` |
| Planner eval runner | Reads a persisted artifact triple directory and writes one eval JSON only when `out_dir` is supplied. | `src/reel_af/planner/eval/runner.py:57-80`, `src/reel_af/planner/eval/runner.py:115-130` |
| Eval triple reader | Looks for optional sidecars next to the triple: `blueprint.json`, `strategy.json`, `mined-candidates.json`, `accepted-candidates.json`. | `src/reel_af/planner/eval/gates.py:103-202` |
| Browser submit boundary | Allows opaque `a1://` and HTTP(S) artifact refs, and rejects filesystem paths. | `web/reel_jobs.py:35-37`, `web/reel_jobs.py:215-227`, `web/reel_jobs.py:452-487` |
| Upload and delivery | Local upload volume cannot be presigned for a separate render node; shared bucket upload is the browser-delivery path. | `web/uploads.py:55-89`, `src/reel_af/storage.py:1-10`, `src/reel_af/storage.py:43-73` |
| Deployment docs | Current Railway notes say `output` is excluded from deploy context; file uploads need a volume; rendered-file retrieval needs object storage or a serving route. | `docs/railway-deployment.md:86`, `docs/railway-deployment.md:216`, `docs/railway-deployment.md:227-228` |
| Git ignore | Only `output/` is ignored today, not `resources/runs` or `resources/evals`. | `.gitignore:18-19` |

There are also tests pinning the desired security shape. Browser composite input rejects
`out_dir`, `finish_config`, renderer project dirs, and encode/Whisper fields
(`tests/web/test_submit.py:330-348`). DSL-hooks submit rejects local filesystem refs such as
`/tmp/x.ts.md`, `~/x.ts.md`, and `../../secret` before any row or dispatch
(`tests/web/test_dsl_hooks_submit.py:175-187`). That is the right production boundary.

One committed eval fixture currently contains a legacy `/tmp/claude-1000/...` path inside
`tests/planner/eval/fixtures/BASELINE-0/hook-plan.json:4`. Treat that as a regression
fixture artifact, not as the runtime convention to propagate.

## SOTA Findings

### 1. Output-root strategies for deployable Python services

| Strategy | Best use | Trade-offs |
|---|---|---|
| Package/repo-relative default | Local development, deterministic tests, and source-checkout services that need a sane default with no user home path. | Good ergonomics and stable relative paths. In an installed wheel or locked container image, package directories may be read-only or ephemeral unless a volume is mounted there. Do not treat package resources as mutable application state. |
| Configured absolute path | Production deploys and CI runners where the platform controls writable storage. | Matches 12-factor deployment config and avoids cwd surprises. It must be an explicit deploy variable, not a developer-specific absolute path. Validate that it is absolute in production. |
| Mounted volume path | Large generated artifacts, re-renders, debugging, eval inputs, and any output that must survive restart/redeploy. | Correct remote persistence primitive, but it is platform-specific and requires capacity/permission management. On Railway, volumes are mounted at runtime, not build time, and the configured mount path is the path the service reads/writes. |
| Object storage / artifact server | Cross-service handoff and browser delivery. | Better than sharing local filesystem paths between producer and renderer. Requires URL/ref generation, authorization, retention policy, and cleanup. |
| `/tmp` / tempfile | Short-lived scratch files that can be recreated inside one operation. | Not acceptable for canonical producer outputs or eval records. Python's `tempfile` APIs are explicitly for temporary files/directories with cleanup semantics, so they are wrong for durable artifacts. |

Sources:

- The Python standard library treats temporary files and directories as temporary storage
  with cleanup behavior, which supports using `/tmp` only for scratch space, not canonical
  output paths ([Python `tempfile`](https://docs.python.org/3/library/tempfile.html)).
- Docker recommends volumes for persistent container data; data in the container writable
  layer is coupled to the container lifecycle, while volumes exist outside it and are better
  for long-term/high-I/O storage ([Docker storage overview](https://docs.docker.com/engine/storage/),
  [Docker volumes](https://docs.docker.com/engine/storage/volumes/)).
- Railway volumes provide persistent service storage at a configured mount path, expose
  `RAILWAY_VOLUME_MOUNT_PATH`, and are mounted only at runtime
  ([Railway Using Volumes](https://docs.railway.com/volumes),
  [Railway Volumes Reference](https://docs.railway.com/volumes/reference)).

Recommendation for reel-af: default to package/repo-relative `resources`, require an env override
for production durability, and use a Railway Volume for generated runs/evals that must survive
redeploy. Use object storage or a serving route for any artifact another service or browser must
fetch.

### 2. Config precedence conventions

The service should use this precedence for canonical defaults:

```text
environment variable
  > config-file value
  > package/repo-relative default
```

This matches the operational direction of the Twelve-Factor App, which puts deploy-varying
configuration in environment variables rather than code constants or scattered local config
files ([12-factor config](https://www.12factor.net/config)). It also lines up with XDG's
model of environment-named base directories and defaults, including the rule that base-dir
environment paths must be absolute and that user-specific data/state/config have separate
locations ([XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/latest/)).

For local Python CLIs, `platformdirs` is the current idiom for user data/config/cache/log
directories across macOS, Windows, Linux/Unix, and Android; it honors XDG variables and can
create directories on access ([platformdirs docs](https://platformdirs.readthedocs.io/en/latest/),
[platformdirs API](https://platformdirs.readthedocs.io/en/latest/api.html)). `appdirs` is the
older Python package for the same class of problem and has not released since 2020
([appdirs on PyPI](https://pypi.org/project/appdirs/)).

For this service, `platformdirs` is useful background but should not be the canonical default.
It would push local outputs into a user home data directory, which conflicts with the user
requirement that producer outputs be package/app-relative by default. The right split is:

- Service/runtime generated artifacts: `REEL_AF_OUTPUT_ROOT` > config > `<producer_root>/resources`.
- User CLI preferences or caches, if introduced later: use `platformdirs`, not the run/eval
  artifact root.
- Secrets and deployment switches: env vars only; never committed config.

### 3. Remote/container specifics

Containers usually have a writable filesystem, but that does not make it durable. Docker's
storage docs distinguish the container writable layer from volumes; volumes are the persistent
mechanism and avoid growing the container layer for data-heavy workloads. Docker also notes
that volumes persist beyond a container's removal and are preferred for long-term storage.

Railway-specific constraints:

- A Railway Volume is attached to a service and exposed at the configured absolute mount path.
- Railway places application files under `/app`; if an app writes to a relative path and that
  path must persist, the volume mount should include the `/app` path, such as `/app/data`.
- Railway provides `RAILWAY_VOLUME_NAME` and `RAILWAY_VOLUME_MOUNT_PATH` at runtime.
- Volumes are mounted at runtime, not build time; build/pre-deploy writes do not land on the
  volume.
- Railway volume reference docs list plan-dependent capacity, resize behavior, and constraints
  such as one volume per service and no replicas with volumes.

Implications for reel-af:

1. The production Railway service should explicitly configure `REEL_AF_OUTPUT_ROOT`.
2. If preserving the package-relative default on Railway, mount the volume at `/app/resources`
   and set `REEL_AF_OUTPUT_ROOT=/app/resources`.
3. If using a neutral data mount, mount at `/data` and set
   `REEL_AF_OUTPUT_ROOT=/data/reel-af/resources`.
4. The producer must never return `/app/...`, `/data/...`, `/tmp/...`, or `~/...` as an API
   artifact reference. Return `a1://runs/...` for trusted internal refs or HTTP(S) URLs for
   remote renderers.
5. A Railway Volume is a service-local persistence mechanism. For a separate remote renderer,
   use HTTP(S) artifact fetch or shared object storage, unless producer and renderer are
   deliberately co-located behind the same filesystem boundary.

This aligns with existing repo comments around A1 artifact resolution: Railway workers cannot
see A1 local files, so production refs must arrive as HTTP(S) or presigned bucket URLs, while
`a1://<rel>` is only for co-located development through `A1_ARTIFACTS_BASE`
(`src/reel_af/app.py:1545-1592`).

### 4. Separation of generated artifacts, evals, and golden fixtures

| Artifact class | Recommended location | Git policy | Notes |
|---|---|---|---|
| Producer triple and planner sidecars | `resources/runs/<run_id>/` | Ignored by default. Promote selected small fixtures manually. | Includes `composite.ts.md`, `transcript.words.json`, `hook-plan.json`, `blueprint.json`, `strategy.json`, `mined-candidates.json`, and `accepted-candidates.json`. |
| Large binary media and render intermediates | `resources/runs/<run_id>/media/`, `resources/runs/<run_id>/render/`, or final stage subdirs | Ignored. Use object storage or Railway Volume. Use Git LFS only for rare committed media baselines. | Includes mp4, wav, image sequences, downloaded source, final render, and ffmpeg intermediates. |
| Eval result JSON | `resources/evals/<eval_run_id>.json` | Ignored by default unless promoted. | Eval outputs are generated observations; they can reference run artifacts by logical ref. |
| Eval diffs | `resources/evals/diffs/<left>__<right>.json` | Ignored by default unless promoted. | Useful for local/CI comparison reports. |
| Committed golden fixtures | `tests/**/fixtures/<case_id>/` or `tests/**/golden/<case_id>/` | Tracked deliberately. Avoid large binaries in normal git. | Deterministic regression baselines should be small, reviewable, and intentionally named. |

Git's ignore documentation says shared ignore patterns for generated project files belong in
version-controlled `.gitignore` files ([Git `gitignore`](https://git-scm.com/docs/gitignore)).
GitHub's Git LFS docs describe LFS as replacing large file contents with pointers while storing
the actual object elsewhere, which is a fallback for rare large committed baselines, not a
default runtime artifact strategy ([GitHub Git LFS](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)).

Recommended ignore policy for a future implementation:

```gitignore
# Generated reel-af artifacts
resources/runs/
resources/evals/

# Keep directories documentable if desired
!resources/.gitkeep
!resources/runs/.gitkeep
!resources/evals/.gitkeep
```

Do not add this from the research task; it is an implementation follow-up.

## Recommended Convention for This Pipeline

### Directory layout

Use `resources` as the generated-artifact root, not `output`, because this pipeline now produces
more than final media. It produces reusable resources for a later renderer and eval system.

```text
resources/
  runs/
    <run_id>/
      composite.ts.md
      transcript.words.json
      hook-plan.json
      blueprint.json
      strategy.json
      mined-candidates.json
      accepted-candidates.json
      manifest.json
      media/
      render/
        source.mp4
        segments/
        base/
        final/
  evals/
    <eval_run_id>.json
    diffs/
      <left_run_id>__<right_run_id>.json
```

`manifest.json` is optional but recommended once implemented. It should contain the producer
version, run id, source URL hash or provenance, logical refs, created time, and schema versions.
It should not contain secrets or developer-local absolute paths.

### Logical refs

Producer filesystem path:

```text
<resolved_output_root>/runs/<run_id>/composite.ts.md
```

Producer/renderer ref:

```text
a1://runs/<run_id>/composite.ts.md
```

Remote renderer ref:

```text
https://<artifact-service-or-bucket>/runs/<run_id>/composite.ts.md
```

The renderer should resolve refs, not trust raw filesystem paths. The existing DSL-hooks
web boundary already enforces that contract for browser submissions.

### Config keys

Use one primary service-level output root:

```json
{
  "paths": {
    "output_root": "resources"
  }
}
```

Environment override:

```text
REEL_AF_OUTPUT_ROOT=/app/resources
```

Derived paths:

```text
runs_root  = output_root / "runs"
evals_root = output_root / "evals"
```

Optional later keys, only if real operational pressure appears:

```text
REEL_AF_RUNS_ROOT
REEL_AF_EVALS_ROOT
```

Do not add those split keys by default. A single root is easier to document, migrate,
ignore, mount, clean up, and inspect.

### Resolution behavior

For service/runtime defaults:

```text
1. If REEL_AF_OUTPUT_ROOT is set:
     use it.
     In production/Railway, require it to be absolute.

2. Else if config paths.output_root is set:
     resolve absolute values as-is;
     resolve relative values against <producer_root>.

3. Else:
     use <producer_root>/resources.
```

For trusted internal function calls and tests:

```text
explicit out_dir > resolved runs_root/evals_root
```

For browser/API input:

```text
out_dir is never accepted.
artifact refs are a1:// or http(s), never filesystem paths.
```

This preserves existing tests that reject `out_dir` and local artifact refs from submit bodies,
while giving internal and test seams a controlled override.

### Railway behavior

Recommended Railway setup:

```text
Volume mount path: /app/resources
REEL_AF_OUTPUT_ROOT=/app/resources
```

or:

```text
Volume mount path: /data
REEL_AF_OUTPUT_ROOT=/data/reel-af/resources
```

The first option keeps the default mental model (`<producer_root>/resources`) aligned with the
mounted path because Railway deploys app files under `/app`. The second option keeps durable
data outside the app tree, which some operators prefer. Both are acceptable if the env var is
set and no code hardcodes either path.

For cross-service remote rendering:

- Producer writes under `REEL_AF_OUTPUT_ROOT`.
- Producer returns `a1://runs/<run_id>/...` and/or HTTP(S) artifact URLs.
- Renderer fetches artifacts by ref. It does not assume the producer's local path exists.
- Final mp4 delivery uses object storage or a file-serving route. Existing `upload_reel()`
  already models the bucket-delivery path.

### Local behavior

From a source checkout with no env override:

```text
silmari-reels-af/resources/runs/<run_id>/...
silmari-reels-af/resources/evals/<eval_run_id>.json
```

This is package/app-relative, not home-relative and not `/tmp`. It also makes local cleanup
obvious:

```bash
rm -rf resources/runs/<run_id>
```

For an installed end-user CLI mode, a future CLI-specific feature may choose `platformdirs`
for user preferences/cache. That should be separate from the producer artifact root because
these A1 outputs are pipeline resources, not personal app preferences.

## Implementation-Seam Guidance for the Code-Seams Researcher

Agent Mail coordination with TealFalcon on 2026-07-19 confirmed there is no existing
producer-wide output-root environment variable. Existing knobs are narrower:
`render/config/carousel.json` has `output_root` only for carousel defaults, web recreate
uses `REEL_CAROUSEL_RECREATE_DIR`, A1 `transcript_to_plan` and `dsl_hooks_to_reels` have
hardcoded `/tmp/reel-af/...` defaults, and legacy article/topic/composite/research paths
use `Path.cwd() / "output" / ...`. Therefore `REEL_AF_OUTPUT_ROOT` / `paths.output_root`
is a clean canonical name rather than a rename of an existing producer-wide contract.

This recommendation should be implementable by introducing a small path resolver and routing
existing seams through it. The seams are:

1. A1 producer caller path: `src/reel_af/planner/pipeline.py:29-43` and
   `src/reel_af/planner/pipeline.py:141-179` already write to caller-provided `out_dir`.
   Keep that contract; centralize how callers choose `out_dir`.
2. Eval writer path: `src/reel_af/planner/eval/runner.py:115-130` writes result JSON to a
   caller-provided `out_dir`. Default CLI/eval commands should resolve this to
   `resources/evals`, not require ad hoc paths.
3. Runtime reasoners/producers: `article_to_reel`, `topic_to_reel`, `composite_to_reel`,
   `transcript_to_plan`, and `dsl_hooks_to_reels` currently each choose local defaults.
   Replace duplicated defaults with derived run dirs under `resources/runs`.
4. DSL-hooks remote fetch: keep `a1://` and HTTP(S) refs. Do not reintroduce local path refs
   through the browser boundary.
5. Git ignore/deployment docs: later implementation should ignore `resources/runs` and
   `resources/evals`, update Railway docs to add `REEL_AF_OUTPUT_ROOT`, and keep object
   storage guidance for browser delivery.

The safest migration is additive:

- Add resolver and config tests first.
- Switch one caller at a time from `Path.cwd()/output` or `/tmp` to the resolver.
- Keep explicit `out_dir` test seams working.
- Preserve submit-boundary rejection of browser-supplied filesystem paths.
- Update docs and ignore rules only after code behavior is in place.

## Sources

External source URLs verified on 2026-07-19:

- [The Twelve-Factor App: Config](https://www.12factor.net/config)
- [XDG Base Directory Specification](https://specifications.freedesktop.org/basedir-spec/latest/)
- [platformdirs documentation](https://platformdirs.readthedocs.io/en/latest/)
- [platformdirs API](https://platformdirs.readthedocs.io/en/latest/api.html)
- [appdirs on PyPI](https://pypi.org/project/appdirs/)
- [Python tempfile documentation](https://docs.python.org/3/library/tempfile.html)
- [Docker storage overview](https://docs.docker.com/engine/storage/)
- [Docker volumes documentation](https://docs.docker.com/engine/storage/volumes/)
- [Railway Using Volumes](https://docs.railway.com/volumes)
- [Railway Volumes Reference](https://docs.railway.com/volumes/reference)
- [Git gitignore documentation](https://git-scm.com/docs/gitignore)
- [GitHub Git Large File Storage documentation](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)
