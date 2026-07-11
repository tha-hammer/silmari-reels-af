---
date: 2026-07-11T12:59:01-04:00
researcher: Codex
git_commit: 18ea51fbb6923061910706c3936dc020d83b2444
branch: main
repository: silmari-reels-af
topic: "reels-af-ui configuration options and proposed enhanced UI"
tags: [research, codebase, reels-af-ui, configuration, render, web]
status: complete
last_updated: 2026-07-11
last_updated_by: Codex
last_updated_note: "Added follow-up research for polished-output positioning surface, grammar, interfaces, and contracts"
last_updated_git_commit: dfdd98931a4aab3b065baf337f84aecc1f7e5bec
---

```
┌─────────────────────────────────────────────────────────────────────┐
│  RESEARCH: reels-af-ui configuration surface                         │
│  Status: complete                                                     │
│  Date: 2026-07-11 12:59:01 -04:00                                     │
└─────────────────────────────────────────────────────────────────────┘
```

# Research: reels-af-ui Configuration Options

**Date**: 2026-07-11 12:59:01 -04:00  
**Researcher**: Codex  
**Git Commit**: `18ea51fbb6923061910706c3936dc020d83b2444`  
**Branch**: `main`  
**Repository**: `silmari-reels-af`

## Research Question

`$research-codebase the reels-af-ui needs to be enhanced with the proper configuration options. The current UI is "dumb". Research the configuration options, then think about how to display in the UI. add a mock up of the proposed UI in your research document`

## Summary

The live UI is a single static page served from `web/index.html`. It exposes source mode, three preset buttons, URL/topic/file input, and a roll button. Its embedded config block carries API paths, visible presets, copy, stage labels, and polling/status behavior (`web/index.html:12-64`). The browser currently sends only `{topic}`, `{source, preset}`, or `{url, source, preset}` from `buildInput()` (`web/index.html:515-520`).

The backend accepts only two targets from the UI: `reel-af.reel_topic_to_reel` and `reel-af.reel_composite_to_reel`; `reel-af.reel_article_to_reel` exists as a constant but is not allowlisted (`web/reel_jobs.py:21-27`). Composite submissions require a non-empty `preset`, accept either a URL or upload handle, preserve sanitized input as `params`, and dispatch an identity-free `cp_input` to the control plane (`web/reel_jobs.py:97-103`, `web/reel_jobs.py:139-174`, `web/server.py:156-193`).

The most important missing per-job UI option is `count`: the AgentField composite reasoner already accepts `count: int = 1` and clamps it before rendering (`src/reel_af/app.py:688-717`), while the existing UI never sends it. The named preset choice is also under-described in the UI: `presets.json` contains duration, dimensions, overlay kind, Remotion composition, accent, phrase grouping, and lower-third settings that are not displayed (`src/reel_af/render/config/presets.json:1-32`).

There is a second, richer config plane in `finish.json` plus `ReelFinishConfig`. It covers finish-stage geometry, captions, banner layout, image cut-ins, Whisper, encode settings, and ASS styles (`src/reel_af/render/config/finish.json:1-97`, `src/reel_af/render/finish_config.py:77-168`). That config is typed and tested, but it is not currently threaded through the web composite reasoner. It should be displayed as advanced/default configuration context, or kept disabled/read-only until a backend contract accepts per-job overrides.

## Detailed Findings

### 1. Current Web UI Surface

| Area | Current implementation | Evidence |
|---|---|---|
| Static page | `create_app()` serves `web/index.html` from `/` after session resolution. | `web/server.py:349-356` |
| API config | Embedded JSON defines execute, poll, upload, health, polling interval, and timeout. | `web/index.html:12-22` |
| Visible presets | Two composite presets plus topic mode are hardcoded in UI config. | `web/index.html:23-27` |
| Source controls | File/drop mode and URL mode are visible; topic reuses the URL slate. | `web/index.html:325-349`, `web/index.html:420-434` |
| Submit body | `buildInput()` emits only topic, file source+preset, or URL+source+preset. | `web/index.html:515-520` |
| Execute flow | `roll()` uploads file sources first, then calls `execute()` and `poll()`. | `web/index.html:527-556` |
| Idempotency | Browser sends `Idempotency-Key`; 409 pending retries reuse the same key. | `web/index.html:576-592` |
| Result display | UI treats `result.error` as failure and uses `download_url` for downloads. | `web/index.html:611-619`, `web/index.html:627-639` |

The visible page is therefore "dumb" in the sense that it only selects a target/preset and source; it does not expose the backend's accepted `count`, show preset-derived details, or distinguish job-safe options from operator-level defaults.

### 2. Backend Submit Contract

`web/reel_jobs.py` is the validation/canonicalization boundary. It rejects unsupported targets, missing input, empty topics, invalid URLs, missing file handles, and forbidden identity fields (`web/reel_jobs.py:105-122`). It strips identity fields and `client_request_id` from the control-plane body (`web/reel_jobs.py:87-94`). It also stores sanitized `params` with `target` and `preset` where present (`web/reel_jobs.py:97-103`).

Composite URL submissions produce:

```json
{
  "params": { "...clean input": "...", "target": "reel-af.reel_composite_to_reel", "preset": "<preset>" },
  "cp_input": { "...clean input": "...", "url": "<normalized http(s) URL>" }
}
```

The current validator does not whitelist every non-identity input key. That means the web boundary may forward extra keys, but the target reasoner still determines whether they are accepted. For `reel-af.reel_composite_to_reel`, the live function signature accepts `url`, `preset`, `count`, and `out_dir` (`src/reel_af/app.py:688-694`). Unknown keys are not a documented target contract.

File submissions are special: the browser uploads a file to `/api/v1/uploads`, receives an org-scoped handle, and submits `{source: handle, preset}`. The server validates ownership and converts that handle to a presigned URL before inserting a job row or dispatching (`web/server.py:140-153`).

### 3. AgentField Reasoner Options

| Target | Current web visibility | Accepted reasoner args | Notes |
|---|---:|---|---|
| `reel-af.reel_composite_to_reel` | Visible via two presets | `url`, `preset`, `count`, `out_dir` | `count` is accepted but missing from the UI; `out_dir` is internal/operator-facing. |
| `reel-af.reel_topic_to_reel` | Visible as `TOPIC` | `topic`, `out_dir` | UI sends only `topic`; render voice and downstream settings are internal. |
| `reel-af.reel_article_to_reel` | Not visible | `url`, `out_dir` | Constant exists as future/not allowlisted in web backend. |

The composite reasoner runs `_run_composite_reels()` off-thread. It loads the named preset, validates overlay kind, downloads the source, checks audio, transcribes, computes how many preset-sized windows fit, renders up to `count`, uploads the first reel result, and returns `video_path`, `reels`, `reel_count`, `source_seconds`, metadata, and possibly `download_url` (`src/reel_af/app.py:619-735`).

Topic/article reasoners converge on `_render_downstream()`. That downstream path hardcodes `voice_tone="wonder"` for TTS and runs audio, cards/beats, visuals/accents, video generation, and stitch (`src/reel_af/app.py:743-831`). Those choices are not per-job web options today.

### 4. Preset Configuration Plane

`src/reel_af/render/config/presets.json` is the current job-relevant format plane for source-video composites. It is loaded by `load_preset()` and validated only by name plus overlay-kind checks in consumers (`src/reel_af/render/presets.py:19-36`, `src/reel_af/app.py:633-640`).

| Preset | Key settings | Current UI treatment |
|---|---|---|
| `middle-third-dynamic` | `1080x1920`, `120s`, `overlay="middle_third"`, `MiddleThird`, accent `#7E22CE`, vertical anchor `0.32`, phrase max words/duration/gap/hold, uppercase flag. | Shown only as `MIDDLE-THIRD`, `vertical · script cards`, `9:16`. |
| `horizontal-youtube-lowerthird` | `1920x1080`, `180s`, `overlay="lower_third"`, `LowerThird`, accent, lower-third duration, image count/every, zoom. | Shown only as `LOWER-THIRD`, `16:9 · yt title + zoom`, `16:9`. |

The UI can safely display these preset details without changing the backend contract. Editing them per job is not currently supported unless the backend accepts either custom preset objects or named override fields.

### 5. Finish Configuration Plane

`ReelFinishConfig` is a strict Pydantic schema over `finish.json`; unknown fields are forbidden (`src/reel_af/render/finish_config.py:77-80`). Its groups are:

| Group | Fields | Current wiring |
|---|---|---|
| Geometry | `canvas_w`, `canvas_h`, `center_x`, `caption_safe_y`, `divider_y` | Used by caption/banner position helpers. |
| Caption grouping | `caption_max_words`, `caption_max_dur_s`, `caption_gap_s`, uppercase flags | Used by finish-stage caption building. |
| Banner layout | font refs, min/max, readable size, line limits, box dimensions/padding, fixed text, duration/fades, lower-third fields | Used by banner layout/ASS and optional lower-third finish path. |
| Divider detection | probe time, band bounds, sample step, dark rows, min contrast | Used by divider computation. |
| Image cut-ins | `image_count`, `image_placement`, min/max duration, edge guard, region | `image_count` gates generation; region drives overlay placement. |
| Whisper/encode | `whisper_model`, `whisper_device`, `whisper_compute_type`, `encode_crf`, `encode_preset` | `whisper_model`, CRF, and preset are wired; device/compute values exist in JSON but the subprocess currently hardcodes `cpu`/`int8`. |
| ASS styles | `caption_style`, `banner_style`, ASS defaults | Typed via `AssStyle`; tests pin values and override behavior. |

This is the right source for an "Advanced defaults" panel, but not a currently supported per-job override surface for the live web composite target.

### 6. Tests And Contracts

| Contract | Tests |
|---|---|
| Submit auth, authorization, target validation, identity stripping, topic trimming, composite URL/file mapping, presign ownership | `tests/web/test_submit.py:29-187` |
| Idempotency and dispatch failure behavior | `tests/web/test_dispatch.py:33-118` |
| Poll ownership, transient CP pass-through, result-ref reconciliation | `tests/web/test_poll.py:34-137` |
| Upload path handles, storage failure modes, bucket presign TTL and org scoping | `tests/web/test_upload.py:23-74`, `tests/web/test_bucket_upload.py:49-120` |
| Browser error/result handling | `tests/web/test_index_contract.py:10-17` |
| Finish config defaults, overrides, unknown-field rejection | `tests/test_finish_config.py:24-123` |
| Provider defaults and env overrides | `tests/test_provider_config.py:30-77`, `tests/test_provider_wiring.py:24-183` |
| Preset CLI behavior: unknown preset, overlay support, reel count, `--only`, lower third | `tests/test_reels_cli.py:25-181` |

The browser itself has string-contract tests, not full DOM/Playwright tests. Backend route contracts are the stronger safety net.

## Proposed UI Mockup

The UI should separate **job-safe controls** from **format metadata** and **operator defaults**. This avoids presenting env/deployment settings as if a browser user can safely mutate them per job.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ REEL-AF                                            SIGNAL LIVE  12:59:01:04 │
├──────────────────────────────────────────────────────────────────────────────┤
│ Source                                                                       │
│ [ Drop file ] [ From URL ] [ Topic ]                                         │
│                                                                              │
│ ┌──────────────────────────── preview / drop target ───────────────────────┐ │
│ │                                                                          │ │
│ │                         MARK IN / selected source                        │ │
│ │                                                                          │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ URL or topic  [ https://youtube.com/watch?v=...                         ]   │
│                                                                              │
│ ┌────────────────────────────── Format ───────────────────────────────────┐ │
│ │ (●) Middle-third dynamic       9:16  120s  Remotion: MiddleThird        │ │
│ │     Script-synced phrases      accent #7E22CE  6 words / 3.2s / 0.4s   │ │
│ │                                                                          │ │
│ │ ( ) Horizontal lower-third     16:9  180s  Remotion: LowerThird         │ │
│ │     Lower-third 6s             6 cut-ins / about every 30s / zoom       │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│ ┌──────────────────────────── Job Settings ────────────────────────────────┐ │
│ │ Reels to cut       [-]  1  [+]       applies to video source presets     │ │
│ │ Start window       first available     current backend cuts first N       │ │
│ │ Output             download link        stored from result.download_url   │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│ ┌──────────────────────────── Advanced Defaults ───────────────────────────┐ │
│ │ Captions: 4 words / 1.8s / 0.35s gap     read-only until override API     │ │
│ │ Images:   3 cut-ins, full, 2.0-3.0s       finish config default           │ │
│ │ Encode:   CRF 19, preset fast             finish config default           │ │
│ │ Provider: env-controlled                  REEL_AF_* variables             │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│                                               [ ROLL ]                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Display Rules

| UI area | Control type | Backing contract | Notes |
|---|---|---|---|
| Source mode | Segmented control | Existing `state.mode` and target kind | Topic hides file upload; video presets allow file or URL. |
| Format | Radio/card list | `CFG.presets[]` plus `presets.json` metadata | Show ratio, duration, overlay, composition, accent, and key timing values. |
| Reels to cut | Stepper/input | `composite_to_reel(count=1)` | Clamp client-side to integer >= 1; server already clamps lower bound. |
| Topic phrase | Text input | `topic_to_reel(topic)` | No preset-specific source controls. |
| Preset details | Read-only summary | `presets.json` | Safe to display now; does not imply per-job override support. |
| Finish defaults | Collapsible read-only panel | `finish.json` / `ReelFinishConfig` | Useful for transparency; editable only after backend accepts override schema. |
| Provider/model settings | Disabled operator panel | Env vars in README/tests | Do not expose secrets or runtime deployment switches as browser fields. |

## Code References

- `web/index.html:12-64` - Browser-owned UI config: API paths, presets, copy, stages, status mapping.
- `web/index.html:515-520` - Current submit input builder.
- `web/index.html:527-556` - Roll flow: validate source, upload if needed, execute, poll.
- `web/index.html:576-597` - Execute request and idempotent-pending retry.
- `web/server.py:140-193` - Server submit path: identity, authorization, validation, presign, row insert, CP dispatch, attach execution id.
- `web/server.py:203-220` - Poll path: ownership, CP poll, status normalization, reconcile.
- `web/reel_jobs.py:21-27` - Web target allowlist.
- `web/reel_jobs.py:87-103` - Identity stripping and sanitized `params`.
- `web/reel_jobs.py:105-175` - Topic/composite validation and canonical `ReelSubmission`.
- `src/reel_af/app.py:688-735` - Composite reasoner accepted args and result handling.
- `src/reel_af/render/config/presets.json:1-32` - Named preset definitions.
- `src/reel_af/render/config/finish.json:1-97` - Finish-stage defaults.
- `src/reel_af/render/finish_config.py:77-168` - Strict typed finish config schema.

## Architecture Documentation

The current architecture has three distinct configuration levels:

1. **Browser UI config** lives inline in `web/index.html`. It controls local UI text, available preset cards, and API paths. It is not fetched dynamically.
2. **Job input config** is the JSON under `input` submitted to `/api/v1/execute/async/{target}`. The web backend validates target/source shape, strips identity, stores sanitized params, and forwards the identity-free control-plane body.
3. **Render defaults/config** live in Python/JSON under `src/reel_af/render/`. `presets.json` is used by the source-video composite path. `finish.json` and `ReelFinishConfig` are used by richer finish-stage modules, but not exposed through the current web reasoner contract.

This split is why the proposed UI should not be one flat "advanced JSON" editor. Preset metadata can be displayed immediately; `count` can be a real job control; finish/provider options need a backend override contract before they become editable per job.

## Workflow Closure Map

### Behavior

A browser user selects reel configuration, submits a reel job, and observes the configured execution status/result in the UI.

### Production Chain

```text
browser config/form state -> browser roll/buildInput/execute ->
Flask /api/v1/execute/async/{target} -> build_submission ->
deepresearch.reel_job insert -> HttpControlPlane.dispatch_async ->
AgentField composite/topic reasoner -> browser/server poll ->
job reconcile + result display
```

### Nodes, Edges, And Evidence

| Depth | Node | Label | Adds/changes for UI enhancement | Evidence |
|---:|---|---|---:|---|
| 0 | Browser config/form state | production-called | yes | `web/index.html:12-64`, `web/index.html:389` |
| 1 | `roll()` / `buildInput()` / `execute()` | production-called | yes | `web/index.html:515-597` |
| 2 | Flask API route and `_handle_submit()` | production-called | maybe if new backend keys are validated | `web/server.py:156-193`, `web/server.py:363-369` |
| 3 | `build_submission()` | production-called | maybe if the backend contracts new fields | `web/reel_jobs.py:105-175` |
| 4 | `PgReelJobRepo.insert_or_get_queued()` | production-called | no | `web/pg.py:191-231` |
| 5 | `HttpControlPlane.dispatch_async()` | production-called | no | `web/control_plane.py:33-37` |
| 6 | `composite_to_reel()` / `_run_composite_reels()` | production-called | maybe if per-job render overrides are added | `src/reel_af/app.py:619-735` |
| 7 | `_handle_poll()` and browser `poll()` / `finish()` | production-called | no | `web/server.py:203-220`, `web/index.html:600-639` |

**Highest new connector**: `web/index.html` configuration controls plus `buildInput()` if the enhancement is limited to already supported fields such as `count`. If editable finish defaults become part of the enhancement, the highest backend connector becomes `build_submission()` and the target reasoner signatures.

### Edge Notes

| Edge | Producer -> consumer | Boundary | Runtime context | Error behavior | Tests |
|---|---|---|---|---|---|
| E0 | Browser form state -> `buildInput()` | same page | DOM state, selected preset, source mode | Client flash errors for missing topic/URL/file | Browser contract only; no DOM test harness |
| E1 | `execute()` -> Flask `/api/...` | HTTP | SuperTokens session cookie, `Idempotency-Key`, JSON body | 401 redirects to login; non-OK throws | `tests/web/test_submit.py`, `tests/web/test_dispatch.py` |
| E2 | `_handle_submit()` -> `build_submission()` -> repo | Python + DB | AuthContext, org/user/role, client request id | 400/401/403/404/503 before CP where applicable | `tests/web/test_submit.py`, integration DB test |
| E3 | `_handle_submit()` -> control plane -> reasoner | server-to-server HTTP, async execution | Identity-free body, server API key | Dispatch failures mark row failed; missing `execution_id` is 502 | `tests/web/test_dispatch.py` |
| E4 | Browser/server poll -> result display | async polling | Execution id scoped through owned job row | CP 429/5xx pass through; result error normalizes to failure | `tests/web/test_poll.py`, `tests/web/test_index_contract.py` |

### ClosureMap (structured - derive() input)

```json
{
  "behavior": "A browser user selects per-job reel configuration, submits it, and observes the configured execution result in the UI.",
  "git_commit": "18ea51fbb6923061910706c3936dc020d83b2444",
  "repo": "/home/maceo/ntm_Dev/silmari-agentfield-system/silmari-reels-af",
  "nodes": [
    {
      "id": "browser_form_state",
      "module": "web/index.html",
      "is_entrypoint": false,
      "adds_or_changes": true,
      "read_path": null,
      "seedable_store": "web/index.html#config+state"
    },
    {
      "id": "browser_roll_execute",
      "module": "web/index.html",
      "is_entrypoint": true,
      "adds_or_changes": true,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "server_submit",
      "module": "web/server.py",
      "is_entrypoint": true,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "reel_job_row_and_dispatch",
      "module": "web/pg.py + web/control_plane.py",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "agentfield_execution",
      "module": "src/reel_af/app.py",
      "is_entrypoint": true,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "browser_poll_result",
      "module": "web/index.html",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": "web/index.html#poll+finish",
      "seedable_store": null
    }
  ],
  "edges": [
    {
      "is_async": false,
      "cross_boundary": false,
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
    },
    {
      "is_async": true,
      "cross_boundary": true,
      "driver": "web/index.html#poll"
    },
    {
      "is_async": false,
      "cross_boundary": true,
      "driver": null
    }
  ]
}
```

### Closure adapter (staged proposal - `2026-07-11-12-59-reels-af-ui-configuration-options.closure-adapter.py`)

```python
"""Closure adapter (STAGED PROPOSAL - not wired into the repo).
Derived from the ClosureMap for: A browser user selects per-job reel configuration,
submits it, and observes the configured execution result in the UI.
Pin: 18ea51fbb6923061910706c3936dc020d83b2444.
Promote into /home/maceo/ntm_Dev/silmari-agentfield-system/silmari-reels-af and
complete each TODO(promote) before use.
Speaks the 7-op contract apps/closure-oracle already talks to (mock_adapter.py).
"""
import http.server, json, sys
ASYNC_EDGES = ["reel_job_row_and_dispatch->agentfield_execution"]
CONNECTOR = {e: True for e in ASYNC_EDGES}
SINK = []

def handle(op, p):
    if op == "/reset":        SINK.clear(); CONNECTOR.update({e: True for e in ASYNC_EDGES}); return {"ok": True}
    if op == "/set_connector": CONNECTOR[p["edge"]] = p["enabled"]; return {"ok": True}
    if op == "/seed_sink":     SINK.append(p["value"]); return {"ok": True}
    if op == "/seed":
        # TODO(promote): seed web/index.html#config+state with p["data"]        (web/index.html:12, web/index.html:389)
        return {"ok": True}
    if op == "/trigger":
        # TODO(promote): call roll()/buildInput()/execute(p["args"])            (web/index.html:515, web/index.html:527, web/index.html:576)
        return {"ok": True}
    if op == "/drive":
        if not CONNECTOR.get(p["edge"], True): return {"ok": True}
        # TODO(promote): drain CP execution and browser poll - poll()           (web/index.html:600)
        return {"ok": True}
    if op == "/observe":
        # TODO(promote): return json.dumps(finish/poll observed result)         (web/index.html:627)
        return {"ok": True, "value": json.dumps(SINK)}
    return {"ok": False, "error": "unknown op"}

class Hn(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        out = json.dumps(handle(self.path, json.loads(self.rfile.read(n) or "{}"))).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Hn).serve_forever()
```

## Historical Context (from thoughts/)

- `thoughts/searchable/shared/plans/2026-07-11-prd-carousel-image-pipeline-and-research-handoff.md` describes future carousel/image and deep-research-to-reels workflows. It includes current web UI context and planned UI/config route expansions.
- `thoughts/searchable/shared/plans/2026-07-11-tdd-05-create-from-research.md` is the main prior UI workflow plan. It discusses adding a "Create from research" mode, Automatic vs Full-control, video/carousel multi-select, fan-out submission, allowlist additions, and `research_run_id`.
- `thoughts/searchable/shared/plans/2026-07-11-tdd-06-carousel-review-and-routes.md` documents future carousel review UI and route contracts, and cites the existing `web/index.html` config/film-token surface as a target area.
- `thoughts/searchable/shared/plans/2026-07-10-tdd-video-ingest-youtube-vimeo.md` documents video-ingest hardening. It treats yt-dlp format ladders, cookies, and JS runtime as protocol/operator configuration rather than user-facing preference controls.

## Related Research

No existing `thoughts/searchable/shared/research/` documents were present before this one.

## Verification Notes

- Initial `bd list --status=open` failed before the branch/worktree correction. Follow-up `bd list --status=open` on `main` succeeded and showed open AF issues around persisted assets/projects/multiple reels, not a direct positioning issue.
- `silmari-oracle metadata` was run after switching back to `main`; this document is pinned to `18ea51fbb6923061910706c3936dc020d83b2444`.
- Follow-up metadata was gathered on `2026-07-11 13:49:34 -04:00` at commit `dfdd98931a4aab3b065baf337f84aecc1f7e5bec` on branch `main`.
- The Semgrep verification scripts referenced by the research workflow were not present under `SAI/skills/ResearchSemgrep/`; citations were verified with targeted `nl -ba` reads and `rg` caller searches.
- `gh repo view` resolved a different upstream repository than the configured `origin`, and `git ls-remote origin <commit>` did not confirm the commit directly. This document therefore uses repo-relative citations rather than GitHub permalinks.

## Follow-up Research 2026-07-11 13:49 - Polished Output Positioning

Research query:

`$research-codebase the positioning code that renders the polished output now lives in silmari-reels-af, enhance the research with the surface, grammar, interfaces and contracts`

This follow-up is pinned to commit `dfdd98931a4aab3b065baf337f84aecc1f7e5bec` on `main`. The positioning/rendering code for polished output is now in this repo under `src/reel_af/render/` plus the repo-root `remotion/` project. There are two related surfaces:

| Surface | Runtime role | Primary files |
|---|---|---|
| Finish-stage polish | Burns banner, captions, and image cut-ins onto a base reel and writes `final.mp4`. | `src/reel_af/render/finish.py`, `src/reel_af/render/captions.py`, `src/reel_af/render/image_cutins.py`, `src/reel_af/render/finish_config.py`, `src/reel_af/render/config/finish.json` |
| Preset Remotion overlays | Renders transparent PNG sequences for middle-third/lower-third overlays and composites them over source windows. | `src/reel_af/render/middle_third.py`, `src/reel_af/render/lower_third.py`, `src/reel_af/render/config/presets.json`, `remotion/src/*.tsx` |

### Surface

`ReelFinishConfig` is the typed public surface for finish-stage geometry and style. The module states that the schema lives in code while defaults come from `config/finish.json`; every field default is loaded through `load_finish_defaults()` (`src/reel_af/render/finish_config.py:1-27`, `src/reel_af/render/finish_defaults.py:19-25`). The core geometry defaults are `canvas_w=1080`, `canvas_h=1920`, `center_x=540`, `caption_safe_y=1344`, and `divider_y=772` (`src/reel_af/render/config/finish.json:2-6`). The default image cut-in pane is `image_region: {x: 0, y: 800, w: 1080, h: 1120}` (`src/reel_af/render/config/finish.json:52-64`).

The finish orchestrator is `finish_reel(base, ctx, cfg, *, deps, raw, out_dir)`. It probes duration, optionally computes a per-reel divider Y, concurrently gathers hook text and caption words, writes combined ASS, optionally generates image cut-ins, builds an overlay graph, appends the ASS burn, runs ffmpeg, and returns `out_dir / "final.mp4"` (`src/reel_af/render/finish.py:204-278`). Its dependency surface is explicit in `FinishDeps`, including `caption_words`, `build_finish_ass`, `generate_hook`, `pick_image_moments`, `generate_image_cutins`, `build_overlay_graph`, `run_ffmpeg`, `probe_duration`, and optional `resolve_divider_y` (`src/reel_af/render/finish.py:73-90`).

The Remotion overlay surface is preset-driven. `presets.json` defines `middle-third-dynamic` as a 1080x1920 `MiddleThird` overlay with `overlay_vertical_anchor: 0.32`, phrase grouping fields, and accent color; it defines `horizontal-youtube-lowerthird` as a 1920x1080 `LowerThird` overlay with lower-third duration, image count, and zoom fields (`src/reel_af/render/config/presets.json:2-31`). `middle_third.render_overlay()` writes Remotion props containing `accent`, `segments`, `totalFrames`, and `verticalAnchor`, then calls `npx remotion render ... --sequence --image-format=png` (`src/reel_af/render/middle_third.py:101-134`). `lower_third.render_lower_third()` writes title/accent props and also renders a transparent PNG sequence (`src/reel_af/render/lower_third.py:49-76`).

The web/API surface currently reaches the Remotion composite reasoner but not per-job finish overrides. The browser's `buildInput()` sends topic, file source+preset, or URL+source+preset (`web/index.html:515-520`). The backend allowlists `reel-af.reel_topic_to_reel` and `reel-af.reel_composite_to_reel`, validates composite `preset`, and forwards cleaned input to the control plane (`web/reel_jobs.py:20-27`, `web/reel_jobs.py:105-174`). The AgentField composite reasoner accepts `url`, `preset`, `count`, and `out_dir` (`src/reel_af/app.py:688-717`), loads the preset, and dispatches to `middle_third` or `lower_third` based on `overlay` (`src/reel_af/app.py:619-685`).

### Grammar

The polished-output positioning grammar is not a text DSL. It is a config/schema grammar:

| Grammar area | Current representation | Evidence |
|---|---|---|
| Finish schema | Pydantic `AssStyle`, `ImageRegion`, `ReelFinishConfig`, all with `extra="forbid"`. | `src/reel_af/render/finish_config.py:45-80` |
| Finish defaults | JSON values for canvas, caption grouping, banner fit, divider detection, image cut-ins, Whisper, encode, and ASS styles. | `src/reel_af/render/config/finish.json:2-97` |
| Caption position | ASS `\pos(center_x, caption_safe_y)`. | `src/reel_af/render/finish_config.py:169-171`, `src/reel_af/render/captions.py:617-624` |
| Banner position | `divider_y` anchor; banner box/text geometry centers ink around that Y. | `src/reel_af/render/finish_config.py:174-176`, `src/reel_af/render/captions.py:627-693` |
| Image cut-in position | Region model accepts `x`, `y`, `width`, `height` with aliases `w`/`h`; graph scales/crops and overlays at `x:y`. | `src/reel_af/render/image_cutins.py:31-40`, `src/reel_af/render/image_cutins.py:135-176` |
| Remotion middle third | Prop grammar is `{segments, accent, totalFrames, verticalAnchor}`; each segment is `{text, from, durationInFrames}`. | `src/reel_af/render/middle_third.py:75-98`, `src/reel_af/render/middle_third.py:120-127`, `remotion/src/MiddleThird.tsx:11-20` |
| Remotion lower third | Prop grammar is `{title, accent}`. CSS positions the card at `left: 96`, `bottom: 132`. | `src/reel_af/render/lower_third.py:49-76`, `remotion/src/LowerThird.tsx:10-17`, `remotion/src/LowerThird.tsx:32-79` |

The repo also contains Composite Transcript DSL v2 under `src/reel_af/dsl/`. That grammar controls source segment selection, insertions, extensions, joins, and transitions rather than final caption/banner placement. Its marker parser recognizes bracketed verbs `insert`, `find`, `extend`, `join`, and `trans`, supports hole resolution with `?` and `=>`, and validates transition primitives from the `XfadeEffect` set (`src/reel_af/dsl/parser.py:36-74`, `src/reel_af/dsl/parser.py:77-244`, `src/reel_af/dsl/models.py:38-54`). The compiler turns a `CompositeDoc` and `WordsSidecar` into a validated `FootageReel` by aligning text, applying extends/inserts/joins/transitions, deriving duration, and calling `validate_renderable()` (`src/reel_af/dsl/compile.py:52-122`).

### Interfaces

| Interface | Input | Output/side effect | Evidence |
|---|---|---|---|
| `finish_reel` | Base `Path`, `FinishContext`, optional `ReelFinishConfig`, injected deps, `raw`, `out_dir`. | Returns base path when `raw=True`; otherwise writes ASS/images/final ffmpeg output and returns `final.mp4`. | `src/reel_af/render/finish.py:204-278` |
| `build_finish_ass` | Word timings, hook, duration, config. | ASS document containing banner box/text and caption events. | `src/reel_af/render/captions.py:713-719` |
| `write_ass` | ASS string and output path. | Writes the sidecar `.ass` file. | `src/reel_af/render/captions.py:722-727` |
| `build_image_overlay_filtergraph` | Image cut-ins plus finish config. | `ImageOverlayFilterGraph(filter_complex, video_label, image_input_count)`. | `src/reel_af/render/image_cutins.py:135-176` |
| `build_finish_ffmpeg_cmd` | Base mp4, image paths, overlay graph, ASS path, duration, config. | ffmpeg command mapping composed video label and optional base audio into output mp4. | `src/reel_af/render/finish.py:150-190` |
| `middle_third.window_segments` | Word timings, source window, preset, fps. | Remotion `Segment` props relative to the reel window. | `src/reel_af/render/middle_third.py:75-98` |
| `middle_third.render_overlay` | Segment props, total frames, output sequence dir, preset. | Transparent PNG sequence rendered by Remotion. | `src/reel_af/render/middle_third.py:101-135` |
| `middle_third.composite_window` | Source, time window, PNG sequence, output path. | ffmpeg overlay at `0:0`, output mp4. | `src/reel_af/render/middle_third.py:138-162` |
| `lower_third.render_lower_third` / `composite_window` | Title/accent/config and source window. | Transparent lower-third sequence, then scaled/cropped 16:9 output mp4. | `src/reel_af/render/lower_third.py:49-76`, `src/reel_af/render/lower_third.py:118-150` |
| `reel-af reels` CLI | Source, `--preset`, optional `--only`, `--whisper`, `--chrome`. | Writes `reelXX/reelXX.mp4` outputs and removes sequence dirs. | `src/reel_af/cli.py:399-508` |
| `reel-af composite` CLI | URL, optional `--out`, `--fast/--rich`. | Calls `render.composite_pipeline.composite_to_reel`; rich is default. | `src/reel_af/cli.py:317-359` |
| `reel-af.reel_composite_to_reel` reasoner | `url`, `preset`, `count`, optional `out_dir`. | Produces one or more preset reels and uploads a downloadable result when storage is configured. | `src/reel_af/app.py:688-735` |

### Contracts

The current tests lock the positioning and polish contracts at several layers:

| Contract | Test evidence |
|---|---|
| Finish config defaults and strictness: geometry defaults, caption/banner style values, image-region defaults, override movement, unknown-field rejection. | `tests/test_finish_config.py:24-123` |
| Caption grouping and placement: grouped phrases obey word/duration/gap thresholds; ASS events are ordered, escaped, and positioned at `caption_safe_y`. | `tests/test_captions.py:73-207` |
| Banner shape: one `BannerBox` drawing plus one `Banner` text event; divider override moves Y; white box and purple text are asserted. | `tests/test_banner.py:29-125` |
| Computed banner behavior: divider detection fallback, measured font fit, computed `\fs`, purple-on-white banner style, caption box at `int(0.70 * H)`. | `tests/test_banner_computed.py:41-195` |
| Rendered pixel contract: libass-rendered banner text fills the box, is centered, and the box spans the frame width. | `tests/test_banner_fill.py:1-145` |
| Image cut-in contract: graph scales/crops to the configured region and overlays at exact `x:y` windows; integration paints the configured region. | `tests/test_image_cutins.py:47-90`, `tests/test_image_cutins.py:92-183` |
| Finish orchestration: ASS burn is appended last; ffmpeg command maps final video label and optional audio; `raw=True` returns base untouched; `image_count=0` skips images but still burns captions/banner. | `tests/test_finish.py:29-90`, `tests/test_finish.py:164-204` |
| Full closure render: real ffmpeg burns banner, captions, and image cut-ins onto a synthetic reel; only Whisper/LLM/image generation are faked. | `tests/test_finish_closure.py:1-192` |
| Composite/CLI contract: rich finish is default for `reel-af composite`; `--fast` skips finish/providers; default stages wire real `finish_reel`. | `tests/test_composite_pipeline.py:36-90`, `tests/test_composite_cli.py:45-90` |
| App Remotion contract: lower-third preset is wired; no-audio sources fail before transcription. | `tests/test_ingest.py:258-333` |
| Source-footage overlay DSL contract: overlay cut-ins accept `zoom` and `visual`; zoom crop focuses map to known crop origins and unknown focus falls back to center. | `tests/dsl/test_overlays.py:14-56` |
| Composite Transcript DSL contract: model constants, transition adjacency/duration, duration math, and renderability validation are pinned. | `tests/dsl/test_models.py:21-52`, `tests/dsl/test_models.py:299-430` |

### Workflow Closure Map: Polished Finish Render

Behavior: `finish_reel` accepts a base stitched reel plus finish context/config and returns a polished `final.mp4` with banner, captions, and image cut-ins burned through the production ffmpeg path.

Production chain:

```text
base reel + FinishContext + ReelFinishConfig
-> finish_reel()
-> captions.build_finish_ass() + image_cutins.build_image_overlay_filtergraph()
-> finish.build_finish_ffmpeg_cmd() + FinishDeps.run_ffmpeg()
-> final.mp4
-> probe_duration()/pixel observation
```

| Depth | Node | Label | Evidence |
|---:|---|---|---|
| 0 | Base reel/config inputs | source | `FinishContext` and `FinishDeps` shape the input and collaborators (`src/reel_af/render/finish.py:54-90`); `ReelFinishConfig` provides strict defaults (`src/reel_af/render/finish_config.py:77-168`). |
| 1 | `finish_reel` | production-called entrypoint | CLI composite default stages call `finish_reel` (`src/reel_af/render/composite_pipeline.py:83-89`); tests drive it directly (`tests/test_finish_closure.py:153-192`). |
| 2 | ASS + image graph builders | production-called collaborators | `finish_reel` calls `build_finish_ass`, `write_ass`, and `build_overlay_graph` (`src/reel_af/render/finish.py:242-265`). |
| 3 | ffmpeg command/run | production-called external renderer | `finish_reel` builds the command and awaits `deps.run_ffmpeg` (`src/reel_af/render/finish.py:267-278`); default deps wire `_run_ffmpeg` (`src/reel_af/render/finish.py:113-125`, `src/reel_af/render/finish.py:304-323`). |
| 4 | `final.mp4` | observable | `finish_reel` returns `out_dir / "final.mp4"` (`src/reel_af/render/finish.py:263-278`); closure tests observe duration and pixel bands (`tests/test_finish_closure.py:164-184`). |

`highest_new_connector`: none. This follow-up documents existing behavior in `main`; it does not add or change a production node.

### ClosureMap (structured - derive() input)

```json
{
  "behavior": "finish_reel returns a polished final.mp4 with banner, captions, and image cut-ins burned through the production ffmpeg path.",
  "git_commit": "dfdd98931a4aab3b065baf337f84aecc1f7e5bec",
  "repo": "/home/maceo/ntm_Dev/silmari-agentfield-system/silmari-reels-af",
  "nodes": [
    {
      "id": "base_reel_finish_inputs",
      "module": "src/reel_af/render/finish.py",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": "finish_reel(base: Path, ctx: FinishContext, cfg: ReelFinishConfig)"
    },
    {
      "id": "finish_reel_entrypoint",
      "module": "src/reel_af/render/finish.finish_reel",
      "is_entrypoint": true,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "ass_and_overlay_graph",
      "module": "src/reel_af/render/captions.py + src/reel_af/render/image_cutins.py",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "ffmpeg_finish_burn",
      "module": "src/reel_af/render/finish.build_finish_ffmpeg_cmd",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "final_mp4_observable",
      "module": "src/reel_af/render/finish.probe_duration",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": "src/reel_af/render/finish.probe_duration",
      "seedable_store": null
    }
  ],
  "edges": [
    {
      "is_async": false,
      "cross_boundary": false,
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
    },
    {
      "is_async": false,
      "cross_boundary": true,
      "driver": null
    }
  ]
}
```

### Closure adapter (staged proposal - `2026-07-11-12-59-reels-af-ui-configuration-options.polished-finish.closure-adapter.py`)

```python
"""Closure adapter (STAGED PROPOSAL - not wired into the repo).
Derived from the ClosureMap for: finish_reel returns a polished final.mp4 with
banner, captions, and image cut-ins burned through the production ffmpeg path.
Pin: dfdd98931a4aab3b065baf337f84aecc1f7e5bec.
Promote into /home/maceo/ntm_Dev/silmari-agentfield-system/silmari-reels-af and
complete each TODO(promote) before use.
Speaks the 7-op contract apps/closure-oracle already talks to (mock_adapter.py).
"""
import http.server, json, sys
ASYNC_EDGES = []
CONNECTOR = {e: True for e in ASYNC_EDGES}
SINK = []
STATE = {}

def handle(op, p):
    if op == "/reset":        SINK.clear(); STATE.clear(); CONNECTOR.update({e: True for e in ASYNC_EDGES}); return {"ok": True}
    if op == "/set_connector": CONNECTOR[p["edge"]] = p["enabled"]; return {"ok": True}
    if op == "/seed_sink":     SINK.append(p["value"]); return {"ok": True}
    if op == "/seed":
        # TODO(promote): seed base Path + FinishContext + ReelFinishConfig        (src/reel_af/render/finish.py:54, src/reel_af/render/finish_config.py:77, src/reel_af/render/finish.py:204)
        STATE["seed"] = p["data"]
        return {"ok": True}
    if op == "/trigger":
        # TODO(promote): call finish_reel(base, ctx, cfg, deps=..., out_dir=...)  (src/reel_af/render/finish.py:204)
        return {"ok": True}
    if op == "/drive":
        return {"ok": True}
    if op == "/observe":
        # TODO(promote): return json.dumps(probe_duration(final_mp4))             (src/reel_af/render/finish.py:284)
        return {"ok": True, "value": json.dumps(SINK)}
    return {"ok": False, "error": "unknown op"}

class Hn(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        out = json.dumps(handle(self.path, json.loads(self.rfile.read(n) or "{}"))).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Hn).serve_forever()
```

## Open Questions

- Should `count` be the only editable non-source option in the first UI enhancement, since the backend already accepts it?
- Should `finish.json` options become per-job overrides, or remain operator defaults displayed read-only in the browser?
- Should article mode be allowlisted and surfaced, or remain outside the reels-af-ui until a product decision is made?
- Should preset metadata be duplicated into `web/index.html` config, fetched from an API, or generated from `src/reel_af/render/config/presets.json` during build/deploy?
- Should UI-visible positioning metadata distinguish the Remotion preset surface from the finish-stage `ReelFinishConfig` surface, since they are separate current contracts?
