---
date: 2026-07-20T10:03:56-04:00
researcher: DustyDune
git_commit: 3515f10dce5d98565c82099cc4fea111f85cd354
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "AF-egx code-side reel mp4 upload and browser delivery path"
tags: [research, codebase, reel-af, delivery, upload, object-storage, a1, web]
status: complete
last_updated: 2026-07-20
last_updated_by: DustyDune
beads: [AF-egx]
coordination:
  orchestrator: BrownFox
  infra_researcher: AmberRobin
  infra_doc_path: thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md
---

# Research: AF-egx code-side reel mp4 upload and browser delivery path

**Date**: 2026-07-20 10:03:56 -04:00  
**Researcher**: DustyDune  
**Git Commit**: 3515f10dce5d98565c82099cc4fea111f85cd354  
**Branch**: reel-af-a1-producer-impl  
**Repository**: silmari-reels-af  
**Beads**: AF-egx

## Research Question

Trace the code side of the reel mp4 delivery path: how a rendered reel mp4 becomes a browser-deliverable URL today, how `dsl_hooks_to_reels` currently handles delivery, how the other reasoners deliver outputs, how A1 artifact refs resolve, and how the web/UI side reconciles `reel_job.result_ref` and browser output URLs.

## Summary

Rendered reel mp4 delivery is currently S3-compatible object-storage upload plus a presigned HTTP(S) GET URL. The `src/reel_af/storage.py` helper has one production backend: lazy `boto3.client("s3", ...)` configured from `REEL_BUCKET_*`; tests can inject a fake client factory. It uploads to `outputs/{run_id}/{basename}` and returns `generate_presigned_url("get_object", ...)`, or `None` if no bucket is configured or the local file is missing (`src/reel_af/storage.py:21-40`, `src/reel_af/storage.py:43-73`).

The A1 `dsl_hooks_to_reels` reasoner has a keyword-only `uploader` seam, but production submit bodies cannot supply it. The web canonicalizer forwards only `source_url`, the three artifact refs, `clip_idx`, and optional finish overrides to the control plane (`web/reel_jobs.py:452-487`). Therefore production uses the in-function default: `from reel_af.storage import upload_reel as uploader` when `uploader is None` (`src/reel_af/app.py:1599-1612`, `src/reel_af/app.py:1633-1638`).

A1 delivery is stricter than the existing topic/article/composite/research reasoners. `dsl_hooks_to_reels` calls the uploader after finish, then returns terminal `{"error": "delivery_unavailable", "run_id": ...}` when the returned value is not an HTTP(S) URL with a host; it does not return `video_path` on that branch (`src/reel_af/app.py:1722-1738`). The matching web poll policy applies the same requirement only to `TARGET_DSL_HOOKS`, changing a CP success without browser delivery into a failed poll response and stripping the whole `result` dict so node-local paths do not leak (`web/reel_jobs.py:76-87`, `web/server.py:273-287`, `web/server.py:290-314`, `web/server.py:773-785`).

The working mp4 pattern for topic/article/composite/research is fail-soft: render locally, call `upload_reel()`, include `download_url` only if upload returns one, and keep local `video_path` in the result. `article_to_reel`, `topic_to_reel`, `composite_to_reel`, and `research_to_reel` all follow that shape (`src/reel_af/app.py:497-518`, `src/reel_af/app.py:651-676`, `src/reel_af/app.py:851-865`, `src/reel_af/app.py:1323-1371`). Carousel is not an mp4 path: it stores slide images through a `StoragePort.put()` image ref and serves slides through a web route that 302-redirects to a presigned object-storage URL (`src/reel_af/app.py:954-977`, `web/server.py:740-749`).

The UI expects a successful poll JSON body with `result.download_url` for the actual download. The browser code renders `result.video_path`/`path`/`output` as display text, but the download anchor uses `result.download_url` first and only falls back to a path string if that string itself starts with `http` (`web/index.html:1490-1525`, `web/index.html:1543-1555`). The DB `result_ref` is web-owned reconciliation metadata, resolved from CP `result.download_url` first, then `url`, then some URI/path fields, and finally a `cp-execution://.../result/video_path` placeholder for non-A1 fail-soft paths (`web/server.py:227-241`, `web/pg.py:358-370`).

## Detailed Findings

### 1. `src/reel_af/storage.py`: `upload_reel()` and object-storage client

`upload_reel()` supports S3-compatible object storage via `boto3`; there is no local-file delivery backend in this helper. `_client()` is lazy: an injected `client_factory` is used by tests, otherwise the function imports `boto3` only when an upload runs and builds `boto3.client("s3", ...)` (`src/reel_af/storage.py:29-40`). The selected bucket is only `REEL_BUCKET_NAME`; `_bucket()` returns `None` when it is unset (`src/reel_af/storage.py:21-23`).

Production object-storage env selection in `src/reel_af/storage.py`:

| Env var | Code use |
|---|---|
| `REEL_BUCKET_NAME` | Required bucket selector; missing bucket makes `upload_reel()` return `None` (`src/reel_af/storage.py:21-23`, `src/reel_af/storage.py:61-64`). |
| `REEL_BUCKET_ENDPOINT` | Optional S3-compatible endpoint URL passed to boto3 (`src/reel_af/storage.py:34-37`). |
| `REEL_BUCKET_ACCESS_KEY_ID` | Optional access key passed to boto3 (`src/reel_af/storage.py:34-38`). |
| `REEL_BUCKET_SECRET_ACCESS_KEY` | Optional secret key passed to boto3 (`src/reel_af/storage.py:34-39`). |
| `REEL_BUCKET_REGION` | Optional region, default `"auto"` (`src/reel_af/storage.py:34-40`). |
| `REEL_DELIVERY_TTL_S` | Output presign TTL, default 86400 seconds (`src/reel_af/storage.py:18`, `src/reel_af/storage.py:25-26`, `src/reel_af/storage.py:69-73`). |

The uploaded object key is `outputs/{run_id}/{basename}`. `basename` comes from `Path(filename).name` when a caller provides `filename`, otherwise from `local_path.name`, so the filename override cannot escape the `outputs/{run_id}/` prefix (`src/reel_af/storage.py:51-67`). The returned URL is the direct return value of `client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=...)`; it is a presigned GET URL, not an `s3://` URI, not a public-path string, and not a local path (`src/reel_af/storage.py:67-73`).

Failure behavior is fail-soft at this helper layer. If the bucket is missing or the file does not exist, `upload_reel()` returns `None` before constructing the S3 client (`src/reel_af/storage.py:61-64`). Tests pin the URL/key shape, default TTL, configurable TTL, missing-bucket `None`, missing-file `None`, and basename-only filename override (`tests/test_storage.py:27-44`, `tests/test_storage.py:47-56`, `tests/test_storage.py:59-94`).

There is a separate web-side object-storage client in `web/storage.py`. It uses the same `REEL_BUCKET_ENDPOINT`, `REEL_BUCKET_ACCESS_KEY_ID`, `REEL_BUCKET_SECRET_ACCESS_KEY`, and `REEL_BUCKET_REGION` env names through `_s3_client_from_env()` (`web/storage.py:44-59`). `web/uploads.BucketUploadStore` reuses that client builder for browser source uploads (`web/uploads.py:115-119`), and `web/storage.ObjectStorage` uses it for carousel media storage/serving (`web/storage.py:62-98`). Those are web ingress/media paths, not the reel-af node's rendered-mp4 `upload_reel()` helper.

### 2. `dsl_hooks_to_reels`: production uploader, default uploader, and delivery branches

`dsl_hooks_to_reels` exposes `uploader=None` as a keyword-only seam after `fetch_segment` (`src/reel_af/app.py:1599-1612`). In production, the control-plane dispatch input is created by `web/reel_jobs.build_submission()` for `TARGET_DSL_HOOKS`. That branch validates `source_url`, `composite_ref`, `words_ref`, `hook_ref`, `clip_idx`, optional `overrides`, and builds `cp_input` with only those fields; it does not include `uploader` (`web/reel_jobs.py:452-487`). The mounted submit path wraps that `cp_input` under `{"input": cp_input}` before calling `dispatch_async()` (`web/server.py:376-415`).

Because production cannot pass Python callables through the JSON submit path, the default uploader is the load-bearing production path: when `uploader is None`, `dsl_hooks_to_reels` imports `upload_reel` from `reel_af.storage` and aliases it as `uploader` (`src/reel_af/app.py:1633-1638`). Tests/direct callers pass fake uploaders to exercise success and failure, but that is not how the web production path supplies delivery (`tests/test_dsl_hooks_worker_closure.py:163-181`, `tests/test_dsl_hooks_worker_closure.py:184-204`, `tests/test_dsl_hooks_worker_closure.py:207-223`).

The real-upload branch is:

1. Render/finish produces a final local mp4 path in the DSL-hooks work directory (`src/reel_af/app.py:1700-1720`).
2. The reasoner computes a descriptive output filename with `reel_output_name()` from the hook title/hook text plus run id/date (`src/reel_af/app.py:1722-1727`).
3. It calls `uploader(str(final), run_id=run_id, filename=filename)` off-thread (`src/reel_af/app.py:1725-1727`).
4. If the returned value is browser-deliverable, the reasoner returns `download_url`, `run_id`, `target_workflow`, `clip_idx`, `segment_count`, `cut_in_count`, `duration_s`, and `source` (`src/reel_af/app.py:1736-1745`).

The unavailable branch is:

1. `_is_browser_deliverable_url()` accepts only non-empty strings whose parsed scheme is in `BROWSER_DELIVERABLE_SCHEMES` and whose `netloc` is present (`src/reel_af/app.py:1503-1509`).
2. `BROWSER_DELIVERABLE_SCHEMES` is `("http", "https")`; `A1_DELIVERY_UNAVAILABLE` is the string `"delivery_unavailable"` (`src/reel_af/dsl/models.py:49-57`).
3. If upload returns `None`, a local path, `file://...`, `s3://...`, `https://` with no host, or any other non-browser URL, `dsl_hooks_to_reels` logs a note and returns `{"error": A1_DELIVERY_UNAVAILABLE, "run_id": run_id}` (`src/reel_af/app.py:1728-1734`).
4. Tests pin that missing bucket/unavailable upload returns `delivery_unavailable` with no `download_url` and no `video_path`, and that a node-local path returned as "download_url" is rejected (`tests/test_dsl_hooks_worker_closure.py:184-223`).

There is no successful `file://` path in current A1 delivery code. `file:///tmp/x.mp4` is explicitly in the rejected set at the web poll boundary for DSL-hooks (`tests/web/test_dsl_hooks_poll.py:127-137`). The source URL validator also rejects `file:///etc/passwd` before planning/rendering (`tests/planner/test_reasoner.py:156-159`).

### 3. Other reasoner delivery patterns

#### Article

`article_to_reel()` renders through `_render_downstream()`, which returns local `video_path`, `duration_s`, narration, voice, and counts (`src/reel_af/app.py:482-490`, `src/reel_af/app.py:1459-1467`). After rendering, the article reasoner imports `upload_reel`, computes a descriptive filename from the core claim/domain plus run id/date, calls `upload_reel(final["video_path"], run_id=run_id, filename=filename)`, and includes `download_url` only if upload returns a truthy value (`src/reel_af/app.py:497-518`). It keeps `video_path` in the returned `final` payload even when no upload URL exists (`src/reel_af/app.py:507-518`).

#### Topic

`topic_to_reel()` uses the same shared downstream renderer and the same fail-soft upload shape. It computes a filename from the topic, calls `upload_reel(final["video_path"], run_id=run_id, filename=filename)`, returns `download_url` only when present, and still returns the local `video_path` through `**final` (`src/reel_af/app.py:636-644`, `src/reel_af/app.py:651-676`).

#### Composite

`composite_to_reel()` calls `_run_composite_reels()` in a thread. `_run_composite_reels()` downloads the source video, checks audio, transcribes, renders one or more windowed reels, and returns `{"video_path": reels[0], "reels": reels, "reel_count": len(reels), "source_seconds": ...}` (`src/reel_af/app.py:726-808`). `composite_to_reel()` then imports `upload_reel`, derives a filename from the public preset plus run id/date, uploads the first reel path, and merges `{"download_url": download_url}` only if upload succeeds (`src/reel_af/app.py:839-865`). Its comment states the input source URL is intentionally not surfaced as the reel result, because for file mode it may be a presigned source URL and the UI must not present it as the output (`src/reel_af/app.py:844-852`).

#### Research-to-reel

`research_to_reel()` is not named in the task list, but it is another mp4-producing reasoner using the same current pattern. It has a keyword-only `uploader` seam, defaults to `upload_reel` when none is injected, uploads `final["video_path"]`, and fail-softs by omitting `download_url` when upload returns `None` (`src/reel_af/app.py:1215-1231`, `src/reel_af/app.py:1323-1334`). It additionally emits a best-effort `reel.completed` DTO where `reel_ref` is `download_url or final.get("video_path", "")`; the returned result itself does not include `reel_ref`, only `**final`, optional `download_url`, source/provenance fields, `run_id`, and timings (`src/reel_af/app.py:1341-1371`). Tests pin both bucket URL delivery and the fail-soft local-path fallback for the DTO (`tests/test_research_to_reel.py:352-394`, `tests/test_research_to_reel.py:397-445`).

#### Carousel

Carousel is an image delivery path, not an mp4 path, and it does not call `upload_reel()`. `research_to_carousel()` defaults `storage` to `_default_storage_port()`, which returns `_FailClosedStoragePort`; callers/tests can inject a storage port (`src/reel_af/app.py:873-879`, `src/reel_af/app.py:980-1005`). Each slide is generated locally, then `_render_one_slide()` calls `storage.put(run_id=run_id, idx=idx, path=path)` and returns a slide record containing `image_ref` (`src/reel_af/app.py:954-977`). Per-slide storage failures are caught in the loop and returned as failed slide records (`src/reel_af/app.py:1026-1058`).

The web side owns carousel serving. `ObjectStorage.put()` stores bytes under an org-prefixed S3 key and returns that key as the ref (`web/storage.py:62-88`). `PgCarouselRepo.get()` reads `image_ref` values from `deepresearch.carousel_slide`, and `slide_ref()` resolves one org-scoped image ref (`web/pg.py:519-546`, `web/pg.py:570-575`). `GET /api/v1/carousels/{cid}/slides/{idx}` resolves the org-scoped ref, checks object existence, and 302-redirects to `deps.storage.presigned_url(ref)` (`web/server.py:740-749`). The browser sets slide `img.src` to that route, not to raw object refs (`web/index.html:1422-1438`).

### 4. `a1://` artifact refs and `A1_ARTIFACTS_BASE`

The A1 artifact resolver is explicitly split between remote production and co-located development. Comments say reel-af runs remotely on Railway, so A1 artifacts are not on this node's filesystem; production refs should arrive as HTTP(S) A1-served or presigned bucket URLs, while `a1://<rel>` is for co-located dev mapped under `$A1_ARTIFACTS_BASE` (`src/reel_af/app.py:1539-1547`).

Resolution rules in `_resolve_artifact_ref()`:

| Ref form | Current behavior |
|---|---|
| `http://` or `https://` with host | Fetch bytes using `artifact_fetch`, write them into `dest_dir/name`, return that local path (`src/reel_af/app.py:1550-1560`, `src/reel_af/app.py:1563-1578`). |
| `a1://<rel>` | Read `A1_ARTIFACTS_BASE`; if unset, raise `ValueError`; if set, return `Path(base) / rel` without copying (`src/reel_af/app.py:1579-1585`). |
| Anything else | Treat as a local path and return `Path(ref)`; comments mark this as tests/fixtures only (`src/reel_af/app.py:1568-1572`, `src/reel_af/app.py:1586`). |

Tests pin all three cases: bare local path passthrough, HTTPS fetch into the work dir, `a1://` mapping under `A1_ARTIFACTS_BASE`, unset-base failure, and remote fetch failure propagating as `OSError` for the reasoner to map to `dsl_artifact_unavailable` (`tests/dsl/test_artifact_resolver.py:19-82`). The web submit boundary rejects filesystem artifact refs before any row or CP dispatch: valid refs must start with `a1://`, `http://`, or `https://` (`web/reel_jobs.py:35-38`, `web/reel_jobs.py:215-227`, `tests/web/test_dsl_hooks_submit.py:93-118`).

`transcript_to_plan()` produces data artifacts only; it does not render mp4. It validates `source_url`, chooses `runs_dir("transcript-to-plan", run_id)` when `out_dir` is absent, calls planner `plan(..., out_dir=work)`, optionally lets an injected `artifact_writer` rewrite the result, and returns that result (`src/reel_af/app.py:1748-1792`). The planner writes `composite.ts.md`, `transcript.words.json`, `hook-plan.json`, plus sidecars, and returns local string refs for those files (`src/reel_af/planner/pipeline.py:218-238`, `src/reel_af/planner/pipeline.py:260-302`). Therefore the default `transcript_to_plan()` payload contains artifact refs such as `composite_ref`, `words_ref`, `hook_ref`, `mined_candidates_ref`, `accepted_candidates_ref`, `strategy_ref`, `blueprint_ref`, and `script_coherence_ref`; it does not contain `reel_ref`, `duration_s`, `segment_count`, or `beat_count` by default (`src/reel_af/planner/pipeline.py:293-302`, `tests/planner/test_reasoner.py:144-153`).

`dsl_hooks_to_reels()` consumes the A1 triple and does render mp4. On success it returns `download_url`, `run_id`, `target_workflow`, `clip_idx`, `segment_count`, `cut_in_count`, `duration_s`, and `source` (`src/reel_af/app.py:1736-1745`). It does not return `reel_ref` or `beat_count`. The web fixture for a successful DSL-hooks execution contains `download_url`, `duration_s`, and `segment_count` (`tests/web/fixtures/dsl_hooks_execution_result.snapshot.json:5-7`).

### 5. Web/UI reconciliation and browser-serving expectations

`web/uploads.py` is the source-upload ingress path, not the finished-reel output path. `LocalUploadStore` writes files under `REEL_UPLOAD_DIR/<org_id>/<uuid>-<name>` and returns `{"path": key}`, but its `presign()` always raises because a local volume is not reachable by the separate reel-af node (`web/uploads.py:55-89`). `BucketUploadStore` writes browser-uploaded source files to the shared S3-compatible bucket under an org-scoped key and later presigns that handle into a node-fetchable URL for composite file-mode dispatch (`web/uploads.py:92-148`). `default_deps()` chooses `BucketUploadStore()` when `REEL_BUCKET_NAME` is set, otherwise `LocalUploadStore()` for dev; the same default deps also wire `storage=ObjectStorage()` for carousel media serving (`web/deps.py:346-381`).

For execute submits, `_handle_submit()` resolves auth, authorizes create, canonicalizes input through `build_submission()`, resolves a file-upload handle to a presigned URL if needed, inserts a `deepresearch.reel_job` row, dispatches the identity-free body to the control plane, and attaches `execution_id` (`web/server.py:376-415`). The composite file-mode path specifically replaces the raw `source` handle with `url = deps.uploads.presign(...)` before the DB insert/CP dispatch (`web/server.py:322-335`). Tests pin that the dispatched body carries the presigned source URL and not the raw handle (`tests/web/test_submit.py:93-115`).

Polling is the output reconciliation point. `_handle_poll()` resolves auth, reads the owned `reel_job` by execution id, calls the control plane, normalizes status, computes `result_ref` only for succeeded CP executions, applies the A1-only delivery-required policy, persists status/result_ref/completed_at, and returns a normalized poll JSON body (`web/server.py:759-785`). `PgReelJobRepo.update_from_execution()` stores `result_ref = coalesce(result_ref, %s)` and preserves terminal statuses (`web/pg.py:358-370`).

`_resolve_result_ref()` resolves the browser-facing result reference from the CP body in this precedence order:

1. `result.download_url` if non-empty string (`web/server.py:227-234`).
2. `result.url` if non-empty string (`web/server.py:231-234`).
3. `result.object_uri`, `result.uri`, or `result.path` if it starts with `http://`, `https://`, `s3://`, or `gs://` (`web/server.py:235-238`).
4. `cp-execution://{execution_id}/result/video_path` if `result.video_path` exists (`web/server.py:239-240`).
5. `None` otherwise (`web/server.py:241`).

The A1-only delivery policy then requires that `result_ref` be browser-deliverable HTTP(S). It looks at `job.params["target"]`, returns no local error for non-DSL targets, and returns `A1_DELIVERY_UNAVAILABLE` only when the target is in `DELIVERY_REQUIRED_TARGETS`, the normalized CP status is `succeeded`, and `_is_browser_deliverable(result_ref)` is false (`web/reel_jobs.py:76-87`, `web/server.py:263-287`). On that local error, `_handle_poll()` changes the status to `failed`, clears `result_ref`, and `_poll_response_body()` removes the entire `result` dict before adding `error: delivery_unavailable` (`web/server.py:773-785`, `web/server.py:290-314`). Tests pin the golden success body, success with a bucket URL, terminal unavailable with only `video_path`, no node-local path leak, full result stripping, and rejection of `file://`, `s3://`, malformed HTTPS, and other non-browser refs (`tests/web/test_dsl_hooks_poll.py:77-145`). Tests also pin that non-DSL targets remain fail-soft and keep the CP result dict with `cp-execution://.../video_path` stored as result_ref (`tests/web/test_dsl_hooks_poll.py:156-174`).

The browser UI polls `GET /api/v1/executions/{id}` until `status === "succeeded"` or a terminal failure status appears (`web/index.html:1490-1525`). On success, `finish(result)` displays `result.video_path || result.path || result.output || "(see server)"`, but the actual download link is `result.download_url` first; it only falls back to `path` when that display path itself starts with `http`, and never falls back to `result.url` (`web/index.html:1543-1555`). So for mp4 outputs, the UI expects `result.download_url` to be the browser-deliverable URL.

## Code References

| Reference | What it establishes |
|---|---|
| `src/reel_af/storage.py:21-40` | Bucket/env/client selection for rendered-mp4 upload. |
| `src/reel_af/storage.py:43-73` | `upload_reel()` object key and presigned GET URL return shape. |
| `src/reel_af/app.py:497-518` | Article reasoner fail-soft upload pattern. |
| `src/reel_af/app.py:651-676` | Topic reasoner fail-soft upload pattern. |
| `src/reel_af/app.py:726-808` | Composite local render result shape. |
| `src/reel_af/app.py:851-865` | Composite reasoner fail-soft upload pattern. |
| `src/reel_af/app.py:1323-1371` | Research-to-reel upload pattern and `reel.completed` DTO `reel_ref`. |
| `src/reel_af/app.py:1539-1586` | A1 artifact ref resolution rules. |
| `src/reel_af/app.py:1599-1638` | DSL-hooks signature and production default uploader import. |
| `src/reel_af/app.py:1722-1745` | DSL-hooks required delivery branch and success payload. |
| `web/reel_jobs.py:35-38` | Web artifact-ref schemes allowlist. |
| `web/reel_jobs.py:76-87` | A1-only delivery-required target and error code. |
| `web/reel_jobs.py:452-487` | DSL-hooks submit canonicalization and CP input fields. |
| `web/uploads.py:55-89` | Local upload store cannot presign node-fetchable URLs. |
| `web/uploads.py:92-148` | Bucket upload store writes org-scoped source handles and presigns them. |
| `web/storage.py:44-59` | Shared web S3-compatible client builder from `REEL_BUCKET_*`. |
| `web/storage.py:62-98` | Web carousel/media object storage and presigned URL API. |
| `web/server.py:227-241` | CP result to `result_ref` precedence. |
| `web/server.py:273-287` | A1 delivery error derivation. |
| `web/server.py:290-314` | Poll response strips `result` on locally-derived delivery failure. |
| `web/server.py:376-415` | Submit row insert, CP dispatch, and execution id attach. |
| `web/server.py:759-785` | Poll reconciliation and DB update. |
| `web/index.html:1543-1555` | Browser download link selection. |

## Architecture Documentation

The code has three distinct object-storage paths:

1. Rendered reel mp4 egress from the reel-af node: `src/reel_af/storage.upload_reel()` uploads local produced mp4s to `outputs/{run_id}/{basename}` and returns a presigned GET URL (`src/reel_af/storage.py:43-73`).
2. Browser source-file ingress in the web service: `web/uploads.BucketUploadStore` stores uploaded source clips under `<org_id>/<uuid>-<name>` and presigns them into node-fetchable source URLs during submit (`web/uploads.py:92-148`, `web/server.py:322-335`).
3. Carousel image media serving in the web service: `web/storage.ObjectStorage` stores org-prefixed image refs and route handlers redirect to presigned object URLs (`web/storage.py:62-98`, `web/server.py:740-749`).

The mp4-producing reasoners currently have two policies:

| Policy | Targets | Current behavior |
|---|---|---|
| Fail-soft delivery | Article, topic, composite, research-to-reel | Keep local `video_path`; include `download_url` only when upload succeeds (`src/reel_af/app.py:497-518`, `src/reel_af/app.py:651-676`, `src/reel_af/app.py:851-865`, `src/reel_af/app.py:1323-1371`). |
| Required delivery | DSL-hooks/A1 | Return success only with browser-deliverable HTTP(S) `download_url`; otherwise return `delivery_unavailable` and no local path (`src/reel_af/app.py:1722-1745`). Web poll enforces the same target-scoped policy (`web/server.py:273-287`, `web/server.py:773-785`). |

The UI contract for mp4 download is `result.download_url`. `result_ref` is a server-side reconciliation/store field; the browser does not read `reel_job.result_ref` directly in the main poll UI. It receives the CP/poll JSON result and builds the anchor from `download_url` (`web/index.html:1490-1525`, `web/index.html:1543-1555`).

## Workflow Closure Map

Behavior mapped: a rendered reel mp4 becomes a browser-downloadable link when the reasoner returns an HTTP(S) `download_url`.

### Prose Map

| Depth | Node | Label | Adds/changes in this research | Evidence |
|---:|---|---|---|---|
| 0 | Local rendered mp4 on reel-af node | production-called through reasoner render pipeline | no | Article/topic/research use `_render_downstream()` and composite uses `_run_composite_reels()` to produce local `video_path` (`src/reel_af/app.py:482-490`, `src/reel_af/app.py:636-644`, `src/reel_af/app.py:807-808`, `src/reel_af/app.py:1308-1319`). DSL-hooks finishes to a local final mp4 (`src/reel_af/app.py:1700-1720`). |
| 1 | `upload_reel()` object-store egress | production-called by reasoners | no | Reasoners import/call `upload_reel()` and merge `download_url` (`src/reel_af/app.py:497-518`, `src/reel_af/app.py:651-676`, `src/reel_af/app.py:851-865`, `src/reel_af/app.py:1323-1334`, `src/reel_af/app.py:1722-1727`). |
| 2 | Control-plane execution result | production-called external boundary | no | Web submit dispatches to CP and poll later reads CP execution (`web/server.py:393-415`, `web/server.py:759-785`). |
| 3 | Web poll result reconciliation | production-called mounted route | no | `/api/<path:subpath>` routes execution poll to `_handle_poll()`, which resolves/stores `result_ref` and applies the A1 delivery policy (`web/server.py:975-981`, `web/server.py:803-808`, `web/server.py:759-785`). |
| 4 | Browser download link | production-called UI poll loop | no | Browser calls the poll route and then uses `result.download_url` as the anchor href (`web/index.html:1490-1525`, `web/index.html:1543-1555`). |

Edges:

| Edge | Producer -> consumer | Boundary | Data contract | Runtime/error behavior | Tests |
|---|---|---|---|---|---|
| 0 | Render output -> uploader | In-process reasoner call | Local file path string, `run_id`, descriptive `filename` | `upload_reel()` returns `None` if no bucket or file missing; legacy reasoners omit `download_url`, A1 fails terminal | `tests/test_storage.py:27-56`, `tests/test_dsl_hooks_worker_closure.py:163-223` |
| 1 | Uploader -> CP result | AgentField reasoner result | `result.download_url` should be HTTP(S) for browser download | A1 reasoner rejects missing/non-HTTP(S) before returning success | `tests/web/fixtures/dsl_hooks_execution_result.snapshot.json:5-7`, `tests/test_dsl_hooks_worker_closure.py:163-223` |
| 2 | CP result -> web poll | HTTP poll through web service | CP body with `status` and optional `result` dict | CP 429/5xx pass through as transient; 2xx is reconciled into DB | `tests/web/test_poll.py:60-90`, `tests/web/test_poll.py:102-137` |
| 3 | Web poll -> browser UI | Browser fetch JSON | `status === "succeeded"` and `result.download_url` for download | A1 non-delivery becomes `failed` + `delivery_unavailable`; legacy non-delivery remains fail-soft | `tests/web/test_dsl_hooks_poll.py:77-174` |

Load-bearing symbol labels:

| Symbol | Label | Evidence |
|---|---|---|
| `src/reel_af.storage.upload_reel` | production-called | Imported/called by article/topic/composite/research/A1 reasoners (`src/reel_af/app.py:499-506`, `src/reel_af/app.py:653-658`, `src/reel_af/app.py:853-860`, `src/reel_af/app.py:1326-1333`, `src/reel_af/app.py:1633-1635`, `src/reel_af/app.py:1725-1727`). |
| `src/reel_af.app.dsl_hooks_to_reels` | production-called | Registered by `@reel.reasoner()` and router included after all reasoners (`src/reel_af/app.py:1598-1612`, `src/reel_af/app.py:1833-1835`); web allowlists target `reel-af.reel_dsl_hooks_to_reels` (`web/reel_jobs.py:27-31`, `web/reel_jobs.py:70-72`). |
| `web.server._handle_submit` | production-called | Routed from mounted `/api/<path:subpath>` for submit targets (`web/server.py:124-128`, `web/server.py:803-805`, `web/server.py:975-981`). |
| `web.server._handle_poll` | production-called | Routed from mounted `/api/<path:subpath>` for `GET v1/executions/{id}` (`web/server.py:131-135`, `web/server.py:806-808`, `web/server.py:975-981`). |
| `web.server._resolve_result_ref` | production-called | Called inside `_handle_poll()` for succeeded executions (`web/server.py:227-241`, `web/server.py:772-773`). |
| `web.index.html finish(result)` | production-called | Called by the UI poll loop on succeeded status (`web/index.html:1517-1521`, `web/index.html:1543-1555`). |

`highest_new_connector`: none in this research artifact; no source code is added or changed here. The mapped current connector for A1 delivery is `src/reel_af.storage.upload_reel` as invoked by `dsl_hooks_to_reels`.

### ClosureMap (structured - derive() input)

```json
{
  "behavior": "A completed reel execution surfaces a browser-downloadable mp4 URL to the UI when the reasoner returns an HTTP(S) download_url.",
  "git_commit": "3515f10dce5d98565c82099cc4fea111f85cd354",
  "repo": "/home/maceo/ntm_Dev/reel-af-a1-producer-impl/silmari-reels-af",
  "nodes": [
    {
      "id": "rendered_mp4",
      "module": "src/reel_af/app.py rendered reasoner output",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": "local rendered mp4 path"
    },
    {
      "id": "upload_reel",
      "module": "src/reel_af/storage.py upload_reel",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "cp_execution_result",
      "module": "AgentField control plane execution result",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "web_poll",
      "module": "web/server.py _handle_poll",
      "is_entrypoint": true,
      "adds_or_changes": false,
      "read_path": null,
      "seedable_store": null
    },
    {
      "id": "browser_download_link",
      "module": "web/index.html finish(result)",
      "is_entrypoint": false,
      "adds_or_changes": false,
      "read_path": "web/index.html finish(result) result.download_url -> resultLink.href",
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
      "is_async": true,
      "cross_boundary": true,
      "driver": "web/server.py _handle_poll"
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

### Closure adapter

No sibling `.closure-adapter.py` file was written because this user request explicitly scoped the deliverable to one Markdown research artifact and "no code changes." The structural map above is included for downstream planning; any executable adapter should be promoted separately from this research pass.

## Historical Context

Related prior research:

| Path | Relevant context |
|---|---|
| `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md` | Establishes the prior recommendation that A1 artifact refs should be `a1://` or HTTP(S), that final mp4 delivery needs object storage or a serving route, and that local filesystem paths are not browser/cross-service delivery. |
| `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-migrate-research.md` | Documents current output root defaults and confirms A1 defaults now use `runs_dir()` for `dsl-hooks` and `transcript-to-plan`. |
| `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-code-seams.md` | Earlier code-seam inventory for output paths; this document supersedes only the mp4 upload/browser delivery details for AF-egx. |
| `thoughts/searchable/shared/research/2026-07-11-12-59-reels-af-ui-configuration-options.md` | Earlier UI/server routing research for execute submit, poll, composite upload, and result-display behavior. |

Coordination note: AmberRobin is covering the infra/config side for AF-egx and stated the infra direction as shared S3-compatible object storage with presigned HTTPS URLs as the primary production path; file-serving from `/app/resources` is awkward because `reel-af` is private and the resources directory is agent-local.

## Verification Notes

`silmari-oracle metadata` was run at 2026-07-20 10:03:56 -04:00 and returned commit `3515f10dce5d98565c82099cc4fea111f85cd354`, branch `reel-af-a1-producer-impl`, repository `silmari-reels-af`. The Semgrep verifier scripts named by the research skill (`SAI/skills/ResearchSemgrep/verify-citations.ts` and `closure-map.ts`) were not present under this repo or nearby workspace paths, so citations were verified with targeted `nl -ba` source reads instead. No tests were run; this was a read-only code research pass plus the requested documentation file.

## Open Questions

- No code-side production caller supplies the `uploader` seam; production uses `upload_reel()` by default. The infra-side research should confirm that the deployed reel-af node has `REEL_BUCKET_NAME` and matching S3-compatible credentials/endpoint configured.
- `transcript_to_plan()` returns local artifact file paths by default unless `artifact_writer` rewrites them. The web submit boundary accepts only `a1://` or HTTP(S) artifact refs for DSL-hooks, so the production producer side needs an artifact-ref publication path outside the default local-path return.
- Carousel image delivery uses `StoragePort.put()` and web-side presigned slide redirects, not `upload_reel()`. It is a useful object-storage pattern, but it is not an mp4 result pattern.
