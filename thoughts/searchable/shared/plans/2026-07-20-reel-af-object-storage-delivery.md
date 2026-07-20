---
date: 2026-07-20T10:39:46-04:00
planner: DustyDune
revised_by: BrownMeadow
revised_at: 2026-07-20T11:02:00-04:00
git_commit: 3515f10dce5d98565c82099cc4fea111f85cd354
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "AF-egx TDD plan: A1 object-storage artifact publication and mp4 delivery regression"
tags: [tdd, plan, reel-af, a1, object-storage, delivery, artifacts, AF-egx]
status: revised-ready-for-implementation
beads: [AF-egx]
research_inputs:
  - thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-code.md
  - thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md
review_inputs:
  - thoughts/searchable/shared/plans/2026-07-20-reel-af-object-storage-delivery-REVIEW.md
---

# AF-egx Object-Storage Delivery - TDD Implementation Plan

## Revision Verdict

The review verdict was "Needs revision." This revision folds in the review findings and is ready for implementation once the red tests below are written first.

Core decisions added by this revision:

- Configured-bucket core artifact publication is all-or-error. When `REEL_BUCKET_NAME` is set, `composite_ref`, `words_ref`, and `hook_ref` are required; each source file must be readable; each upload must succeed; and each generated ref must parse as `http` or `https` with a non-empty `netloc`. Any violation raises from the publisher, and `transcript_to_plan()` must return `{"error": "dsl_artifact_unavailable", ...}` through the existing writer-failure path.
- A1 artifact presign TTL is a named policy: add `REEL_ARTIFACT_TTL_S`, defaulting to `REEL_DELIVERY_TTL_S` when unset, then to the current 86400-second delivery default. Operations must set this value above the expected queue and manual handoff delay between plan production and remote render consumption.
- The render consumer and web canonicalizer use only the core triple. In bucket-backed production results, known sidecar refs are scrubbed by default. No-bucket local dev keeps sidecar refs unchanged. Optional sidecar publication is allowed only behind a debug/config flag and has explicit exposure and failure semantics below.
- Hook-plan `idempotency_key` values are immutable after planning. Rewriting `clips[*].composite_ref` to the published composite URL must not recompute the key, because the key is part of the clip contract.
- Core object keys use fixed filenames, not arbitrary source basenames: `composite_ref -> composite.ts.md`, `words_ref -> transcript.words.json`, and `hook_ref -> hook-plan.json`.
- Artifact publisher tests use `put_object` for uploaded bytes and an exact-argument fake S3; `upload_reel()` continues to use its existing `upload_file` behavior.
- A minimal publisher-to-`_resolve_artifact_ref()` round-trip is validated before app-level writer wiring.

## Goal

Pin the already-wired A1 mp4 delivery path with regression tests, then close the real two-stage A1 producer gap: `transcript_to_plan()` must publish its generated DSL artifact refs into a form that a separate remote `dsl_hooks_to_reels()` execution and the web submit boundary can consume.

The production recommendation is storage-backed HTTPS refs. `upload_reel()` already uploads final mp4s to S3-compatible storage and returns a presigned GET URL (`src/reel_af/storage.py:43-73`). AmberRobin's infra research shows Railway now has the shared bucket and `REEL_BUCKET_*` env wiring for both `reel-af` and `reel-af-ui` (`thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md:137-159`). Therefore this plan does not rebuild mp4 egress. It verifies it and adds artifact publication for the plan-to-render handoff.

## Current State

### What Already Works

`dsl_hooks_to_reels()` has a keyword-only `uploader` test seam, but production JSON submit bodies cannot pass it. When no callable is supplied, the reasoner imports `reel_af.storage.upload_reel` as the default uploader (`src/reel_af/app.py:1599-1638`). After render/finish, it calls the uploader, requires an HTTP(S) URL with a host, and returns `download_url` only on success (`src/reel_af/app.py:1722-1745`). The storage adapter reads `REEL_BUCKET_NAME`, uses lazy boto3 with `REEL_BUCKET_ENDPOINT`, `REEL_BUCKET_ACCESS_KEY_ID`, `REEL_BUCKET_SECRET_ACCESS_KEY`, and `REEL_BUCKET_REGION`, stores mp4s under `outputs/{run_id}/{basename}`, and returns a presigned GET URL (`src/reel_af/storage.py:21-40`, `src/reel_af/storage.py:61-73`).

The web poll boundary already enforces the same delivery contract for the DSL-hooks target only. It resolves `result.download_url` first (`web/server.py:227-241`), marks A1 success without browser delivery as `failed` plus `delivery_unavailable` (`web/server.py:273-287`, `web/server.py:759-785`), and strips local result payload on that local error (`web/server.py:290-314`). The browser link uses `result.download_url` first and never falls back to `result.url` (`web/index.html:1543-1555`).

### What Is Missing

`transcript_to_plan()` produces local artifact paths by default. It creates a local run directory, calls planner `plan(...)`, and only rewrites the result if an injected `artifact_writer` exists (`src/reel_af/app.py:1748-1792`). The planner writes `composite.ts.md`, `transcript.words.json`, `hook-plan.json`, and sidecars, then returns local filesystem refs (`src/reel_af/planner/pipeline.py:260-302`). The web DSL-hooks submit boundary accepts only `a1://`, `http://`, or `https://` artifact refs and rejects filesystem paths before row creation or CP dispatch (`web/reel_jobs.py:35-38`, `web/reel_jobs.py:215-227`, `web/reel_jobs.py:452-487`).

The gap is therefore producer-side artifact publication, not mp4 upload. The existing `artifact_writer` seam is the right insertion point, but production currently supplies none.

## Recommended Approach

Add a storage-backed A1 artifact publisher in `src/reel_af/storage.py`, using the same bucket/client/env conventions as `upload_reel()` while giving artifacts a named TTL helper. Wire it as the default writer inside `transcript_to_plan()` when no explicit `artifact_writer` is supplied.

With no `REEL_BUCKET_NAME`, the publisher returns a copied result with local refs unchanged. This preserves co-located dev and existing direct-call tests.

With `REEL_BUCKET_NAME` configured, the publisher is strict for the core triple:

- Require `composite_ref`, `words_ref`, and `hook_ref`.
- Require each referenced source file to be readable.
- Upload each core artifact with `put_object(Bucket=bucket, Key=key, Body=bytes)`.
- Generate each ref with `generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=artifact_ttl_s)`.
- Parse each generated ref and require scheme `http` or `https` plus a non-empty host.
- Raise on any missing core file, unreadable file, upload exception, presign exception, or malformed presigned URL. Do not return partial published refs or fallback local refs when a bucket is configured.

Use fixed object keys under `plans/{run_id}/` for the core triple:

- `composite_ref` uploads to `plans/{run_id}/composite.ts.md`
- `words_ref` uploads to `plans/{run_id}/transcript.words.json`
- `hook_ref` uploads to `plans/{run_id}/hook-plan.json`

When publishing `hook-plan.json`, rewrite each clip's embedded `composite_ref` to the same published composite URL before uploading the hook-plan object. The planner currently builds the hook plan with the local composite path (`src/reel_af/planner/pipeline.py:218-225`), and the current consumer only checks that the field exists (`src/reel_af/app.py:1657-1658`), but a published hook plan must not carry a stale node-local ref.

Do not recompute `clips[*].idempotency_key` during this rewrite. The key is derived from the original composite ref at planning time (`src/reel_af/planner/serialize.py:368-379`) and is treated as part of the clip contract. The uploaded hook-plan JSON must preserve the key while ensuring no raw local path appears anywhere else in the JSON.

### Sidecar Classification

The render handoff contract is the core triple only. `dsl_hooks_to_reels()` consumes `composite_ref`, `words_ref`, and `hook_ref` (`src/reel_af/app.py:1599-1603`, `src/reel_af/app.py:1650-1656`), and the web submit canonicalizer forwards only those three artifact refs (`web/reel_jobs.py:465-487`).

Known sidecars returned by `_write_triple()` are:

- `mined_candidates_ref` -> `mined-candidates.json`
- `accepted_candidates_ref` -> `accepted-candidates.json`
- `strategy_ref` -> `strategy.json`
- `blueprint_ref` -> `blueprint.json`
- `script_coherence_ref` -> `script-coherence.json`

Default production behavior with `REEL_BUCKET_NAME` configured: scrub these known sidecar ref keys from the returned result instead of publishing them. This avoids leaking producer-local paths while minimizing presigned exposure for planner internals that are not needed by the renderer.

No-bucket local dev behavior: keep sidecar refs unchanged, matching today's local inspection workflow.

Optional debug behavior: if implementation adds `REEL_PUBLISH_A1_SIDECARS=1`, sidecars may be published with the same `REEL_ARTIFACT_TTL_S` policy. In that debug mode, a present sidecar ref is all-or-error for that sidecar: missing/unreadable sidecar file, upload failure, presign failure, or malformed sidecar URL raises from the publisher. The debug flag must be off by default.

Threat note: presigned sidecars can expose mined candidates, accepted strategy, blueprint decisions, and script coherence diagnostics. They may contain sensitive or proprietary intermediate reasoning. Keeping them out of bucket-backed production results by default is part of the delivery contract, not a cleanup preference.

### Artifact TTL Policy

Add `_artifact_ttl_s()` in `src/reel_af/storage.py`:

- Read `REEL_ARTIFACT_TTL_S` first.
- If unset, reuse `REEL_DELIVERY_TTL_S`.
- If both are unset, use the existing 86400-second default.

`REEL_ARTIFACT_TTL_S` governs the plan-to-render artifact handoff, not the final mp4 browser download. It must be long enough for queueing, retries, and manual "plan now, render later" workflows. Publisher unit tests must prove the value passed to `generate_presigned_url(..., ExpiresIn=...)` is configurable independently from mp4 delivery.

## What This Plan Does Not Do

- No CP/Go event-contract change for browser/UI delivery. For the current UI, poll `result.download_url` is the browser delivery contract, not the `com.silmari.reel.completed.v1` event surface (`thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md:161-212`).
- CP follow-up for downstream event consumers is explicitly out of scope for browser/UI delivery only. Downstream event consumers must dereference `cp-execution://.../result` through CP, or CP owners need a CP-owned event change. The current event exposes `reel_ref`, not `download_url`, so direct-download event delivery is not solved by this browser/UI plan. Also track the adjacent existing CP issue `AF-u8u`: `reel.completed` can fire for `reel_transcript_to_plan`, which is a triple producer rather than a reel producer.
- No Railway deploy or variable changes. AmberRobin found the bucket/env wiring already present; verifying live end-to-end delivery after merge still requires an ops redeploy if the current Railway image lags this commit.
- No bucket lifecycle, retention, cost, or secret-rotation work.
- No file-serving route from `/app/resources`. The private `reel-af` service has no public domain, so object storage remains the production delivery boundary (`thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md:214-229`).
- No web submit/poll contract rewrite. The submit allowlist and A1 delivery-required policy stay unchanged and are protected by tests.

## File-Level Change Plan

| File | Planned change |
|---|---|
| `src/reel_af/storage.py` | Add `publish_a1_artifacts(...)`, `_artifact_ttl_s()`, fixed core key mapping, HTTP(S)+host presign validation, and sidecar scrub/debug behavior. Reuse `_bucket()` and `_client()`. Keep `upload_reel()` caller behavior unchanged. |
| `src/reel_af/app.py` | In `transcript_to_plan()`, default `artifact_writer` to the storage-backed publisher when no writer is injected, passing the generated `run_id`. Keep explicit writer precedence and async-aware behavior. Ensure publisher exceptions map to `dsl_artifact_unavailable`. |
| `tests/test_storage.py` or `tests/test_a1_artifact_storage.py` | Add fake-S3 unit tests for artifact publishing, all-or-error core failures, malformed presigned URLs, TTL configurability, no-bucket no-op, sidecar scrub/debug behavior, and hook-plan internal ref rewrite. |
| `tests/planner/test_reasoner.py` and `tests/planner/test_paths.py` | Add/adjust reasoner tests proving default publication when bucket is configured, strict failure mapping when publication fails, and unchanged local refs when no bucket is configured. |
| `tests/planner/test_pipeline.py` | Add a publisher -> `_resolve_artifact_ref()` round-trip directly after publisher tests, then a plan -> publish -> HTTP fetch -> DSL-hooks consume round-trip using fake render seams. |
| `tests/test_dsl_hooks_worker_closure.py` | Add default-uploader mp4 delivery regression tests that exercise `upload_reel()` through the reasoner rather than an injected success uploader. |
| `tests/web/test_dsl_hooks_submit.py` | Add a boundary test that published HTTPS artifact refs are accepted and forwarded unchanged. Keep filesystem-ref rejection tests. |
| `tests/web/test_dsl_hooks_poll.py` | No expected implementation change; keep this suite green to prove delivery-required policy is unchanged. |

## Testable Behaviors

### B1 - DSL-hooks default mp4 uploader delivers with a configured bucket

Given `REEL_BUCKET_NAME` is set and the S3 client is fake-injected at the storage boundary, when `dsl_hooks_to_reels()` runs without an `uploader` argument, then the result contains an HTTP(S) `download_url` from `upload_reel()`, and the fake S3 client observed an upload under `outputs/{run_id}/...`.

Red test:

- Add a fast worker test near `tests/test_dsl_hooks_worker_closure.py:163-223`.
- Do not pass `uploader=...`.
- Set `REEL_BUCKET_NAME`.
- Monkeypatch the storage S3 client seam so `upload_reel()` uses a fake client like `tests/test_storage.py:13-24`.
- Monkeypatch expensive render seams if needed, following the fast consumer pattern in `tests/planner/test_pipeline.py:347-389`.
- Assert:
  - `"error" not in result`
  - `result["download_url"]` parses as `http` or `https` with a non-empty host
  - fake S3 uploaded one object whose key starts with `outputs/`
  - `result["target_workflow"] == "dsl_hooks"`

Green implementation:

- Ideally none. This should pass if the current default import path is working (`src/reel_af/app.py:1633-1635`) and bucket env is configured.
- If it fails because the test cannot inject the S3 client cleanly, add the smallest test-only-safe seam in storage, not a new delivery path.

Refactor guard:

- Do not weaken `_is_browser_deliverable_url()` (`src/reel_af/app.py:1503-1508`).
- Do not return `video_path` from the DSL-hooks success payload.

### B2 - DSL-hooks default mp4 uploader fails closed with no bucket

Given no `REEL_BUCKET_NAME`, when `dsl_hooks_to_reels()` runs without an injected `uploader`, then it returns `{"error": "delivery_unavailable", "run_id": ...}` and exposes no `download_url` or `video_path`.

Red test:

- Add a default-uploader variant next to the existing injected failure test at `tests/test_dsl_hooks_worker_closure.py:184-204`.
- Delete `REEL_BUCKET_NAME` with `monkeypatch.delenv`.
- Do not pass `uploader`.
- Use the same fake render seams as B1.
- Assert the existing terminal contract from `src/reel_af/app.py:1728-1734`.

Green implementation:

- Ideally none. `upload_reel()` already returns `None` when the bucket is missing (`src/reel_af/storage.py:61-64`), and DSL-hooks already maps that to `delivery_unavailable`.

Refactor guard:

- This test pins BrownFox's framing: local `delivery_unavailable` is a no-bucket/dev result, not proof that production mp4 egress is unwired.

### B3 - Storage-backed artifact publisher rewrites the core triple to valid HTTPS refs

Given a successful planner result containing local core refs, when the bucket is configured and the A1 artifact publisher runs, then it uploads the core triple and returns a copied result whose core refs are valid presigned HTTP(S) URLs.

Red tests:

- Create `tests/test_a1_artifact_storage.py` or extend `tests/test_storage.py`.
- Use a dedicated fake S3 with exact boto3 method shapes:
  - `put_object(Bucket, Key, Body)`
  - `generate_presigned_url(operation, Params, ExpiresIn)`
- Keep the existing `FakeS3.upload_file(...)` tests for `upload_reel()` unchanged; the artifact publisher fake should enforce `put_object` so rewritten hook-plan bytes are observable without temp-file indirection.
- Seed local files for the core triple returned by `_write_triple()` (`src/reel_af/planner/pipeline.py:260-302`).
- Set `REEL_BUCKET_NAME`.
- Call the new publisher directly, for example `publish_a1_artifacts(result, run_id="abc123", client_factory=lambda: fake_s3)`.
- Assert:
  - returned core refs parse as `http` or `https` with non-empty hosts
  - uploaded keys are exactly `plans/abc123/composite.ts.md`, `plans/abc123/transcript.words.json`, and `plans/abc123/hook-plan.json`
  - `put_object` receives exact `Bucket`, `Key`, and byte `Body` args for each object
  - non-ref result fields are preserved exactly
  - the original input dict is not mutated
  - no stale local core path appears anywhere in the returned JSON

Green implementation:

- Add `publish_a1_artifacts(result: Mapping[str, Any], *, run_id: str, client_factory=None, ttl_s: int | None = None, prefix: str = "plans") -> dict`.
- Reuse `_bucket()` and `_client()` from `src/reel_af/storage.py:21-40`.
- Use `_artifact_ttl_s()` for artifact presign TTL unless `ttl_s` is explicitly passed.
- If no bucket is configured, return a shallow copy of the result unchanged without constructing the client.
- For configured buckets, upload core artifacts under the fixed keys above and replace each core ref with `generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=artifact_ttl_s)`.

Refactor guard:

- Keep `upload_reel()` byte-compatible from a caller perspective. Existing `tests/test_storage.py:27-94` must keep passing.
- Do not use source basenames for core keys. The field-to-fixed-name mapping is the public contract for the core triple.

### B3a - Configured-bucket core publication is all-or-error

Given `REEL_BUCKET_NAME` is set, when any required core artifact cannot be read, uploaded, presigned, or validated as a hosted HTTP(S) URL, then the publisher raises and never returns a result containing local fallback refs.

Red tests:

- Missing core file: seed `words_ref` and `hook_ref`, point `composite_ref` at a missing path, and assert `publish_a1_artifacts(...)` raises.
- Mid-way S3 upload exception: fake `put_object` succeeds for `composite.ts.md`, then raises for `transcript.words.json`; assert the publisher raises and does not return partial refs.
- Presign exception: fake upload succeeds but `generate_presigned_url(...)` raises; assert the publisher raises.
- Malformed presign: fake returns `https://` with no host for one core ref; assert the publisher raises.
- No stale path: for every negative case routed through `transcript_to_plan()`, assert the returned error JSON has `error == "dsl_artifact_unavailable"` and contains no producer-local artifact path, including under nested metadata.

Green implementation:

- Validate all three core refs are present before uploading.
- Read all three source files before or during upload, but never tolerate a missing/unreadable core file in bucket mode.
- Validate every generated URL with `urllib.parse.urlparse`; require scheme in `{"http", "https"}` and non-empty `netloc`.
- Let publisher exceptions propagate to `transcript_to_plan()` so its existing writer exception handler returns `dsl_artifact_unavailable` (`src/reel_af/app.py:1790-1792`).

Refactor guard:

- No configured-bucket path may silently behave like no-bucket dev. Bucket mode is strict for the core triple.
- Do not rely on the web submit prefix check to catch malformed artifact URLs; producer publication owns this validation.

### B4 - Published hook plan rewrites composite refs while preserving idempotency keys

Given a hook-plan file whose `clips[*].composite_ref` points at the local composite file, when the publisher uploads the hook plan, then the uploaded hook-plan body contains the published HTTPS `composite_ref`, preserves each clip's `idempotency_key`, and contains no raw local path anywhere else in the JSON.

Red test:

- Extend the B3 fake-S3 test to capture the bytes passed to `put_object` for `plans/{run_id}/hook-plan.json`.
- Seed the hook plan with a clip shaped like the planner output from `build_hook_plan(...)` (`src/reel_af/planner/pipeline.py:218-227`).
- Include an `idempotency_key` value known to have been derived from the original local `composite_ref`.
- Assert:
  - no local temp path appears anywhere in the uploaded hook-plan JSON body
  - every clip with `composite_ref` now points at the returned `composite_ref` HTTP(S) URL
  - each original `idempotency_key` is preserved byte-for-byte
  - the top-level returned `hook_ref` is the presigned URL for the uploaded rewritten hook-plan object

Green implementation:

- Publish and validate the composite first, because the hook-plan rewrite needs the published composite URL.
- Before uploading `hook-plan.json`, parse JSON, rewrite each clip's `composite_ref` to the published composite URL, serialize deterministically, and upload that body with `put_object`.
- If hook JSON cannot be parsed while the bucket is configured, raise so `transcript_to_plan()` maps the problem to `dsl_artifact_unavailable`.
- Treat `idempotency_key` as immutable after planning; do not recompute it from the published composite URL or run id.

Refactor guard:

- Do not change planner `_write_triple()` local outputs. Local dev files should remain readable on disk.
- Do not introduce any raw local producer path into rewritten hook-plan JSON.

### B4a - Published refs resolve through `_resolve_artifact_ref()` before app wiring

Given the publisher returns presigned HTTPS core refs, when `_resolve_artifact_ref()` receives those refs with an `artifact_fetch` fake, then it writes fetched artifact bytes into the consumer work directory with the expected filenames.

Red test:

- Add a focused test near the publisher tests or artifact resolver tests before wiring `transcript_to_plan()`.
- Publish a seeded core triple with fake S3.
- Build an `artifact_fetch(url)` fake that maps each presigned URL back to the uploaded `put_object` bytes.
- Call `_resolve_artifact_ref()` for the returned `composite_ref`, `words_ref`, and `hook_ref`.
- Assert fetched local files exist under the render work dir, contain the uploaded bytes, and do not use producer-local paths.

Green implementation:

- B3 and B4 should be sufficient. `_resolve_artifact_ref()` already handles HTTP(S) fetch into `dest_dir/name` (`src/reel_af/app.py:1563-1578`).

Refactor guard:

- This early round-trip must run before app-level writer wiring. It catches malformed URLs, query-string handling, and uploaded-body mapping while the failure surface is still small.

### B5 - No-bucket artifact publication preserves local dev behavior

Given no `REEL_BUCKET_NAME`, when the publisher runs, then it returns local refs unchanged, keeps known sidecar refs unchanged, and performs no S3 client construction.

Red test:

- In the publisher unit test file, unset `REEL_BUCKET_NAME`.
- Pass a `client_factory` that raises if called.
- Include both the core triple and known sidecar refs in the input result.
- Assert the returned dict equals the input for known refs and metadata.
- Assert the returned dict is a copy, not the same object.

Green implementation:

- The publisher must check `_bucket()` before resolving, reading, uploading, scrubbing, or constructing the client.
- Return a copy, not the same object, so callers can safely treat publisher output as a result value.

Refactor guard:

- This no-bucket behavior is intentional. It preserves direct co-located dev usage where `_resolve_artifact_ref()` can still accept local fixture paths (`src/reel_af/app.py:1563-1586`, `tests/dsl/test_artifact_resolver.py:19-29`).

### B6 - Bucket-backed production results scrub sidecar refs by default

Given a planner result containing the core triple and known sidecar refs, when `REEL_BUCKET_NAME` is configured and `REEL_PUBLISH_A1_SIDECARS` is unset/false, then the publisher returns valid core HTTPS refs and omits the known sidecar ref keys from the returned result.

Red tests:

- Seed all known sidecar files plus the core triple.
- Set `REEL_BUCKET_NAME`.
- Leave `REEL_PUBLISH_A1_SIDECARS` unset.
- Assert no `mined_candidates_ref`, `accepted_candidates_ref`, `strategy_ref`, `blueprint_ref`, or `script_coherence_ref` key remains in the returned result.
- Assert fake S3 received only the three core keys.
- Assert no sidecar local path appears anywhere in the returned JSON.

Optional debug-mode red tests if `REEL_PUBLISH_A1_SIDECARS=1` is implemented:

- Set `REEL_PUBLISH_A1_SIDECARS=1` and assert known sidecar refs are uploaded, presigned with `REEL_ARTIFACT_TTL_S`, and returned as valid hosted HTTP(S) URLs.
- In debug mode, make one present sidecar unreadable or make sidecar `put_object` fail; assert the publisher raises rather than returning a mixed local/published result.

Green implementation:

- Default bucket-backed publisher output should remove known sidecar ref keys after core publication succeeds.
- If the debug flag is implemented, apply the same read/upload/presign/URL-validation rules to each present sidecar and raise on sidecar publication failure.

Refactor guard:

- Do not make sidecars part of the render handoff contract.
- Do not publish sidecars by default just to avoid local-path leakage; scrubbing is the default production leak-prevention behavior.

### B7 - `transcript_to_plan()` defaults to artifact publication when a bucket exists

Given a successful transcript-to-plan result and a configured bucket, when production calls `transcript_to_plan()` without injecting `artifact_writer`, then returned core artifact refs are valid hosted HTTP(S) URLs and known sidecar refs are scrubbed by default.

Red tests:

- Add a planner reasoner test near `tests/planner/test_reasoner.py:144-153` or `tests/planner/test_paths.py:80-107`.
- Monkeypatch `uuid.uuid4()` to a deterministic run id.
- Monkeypatch planner `plan(...)` to write realistic local artifact files and return local refs.
- Set `REEL_BUCKET_NAME`.
- Fake the storage S3 client.
- Call `transcript_to_plan(...)` with no `artifact_writer`.
- Assert:
  - `composite_ref`, `words_ref`, and `hook_ref` parse as `http` or `https` with non-empty hosts
  - fake S3 uploaded under `plans/{run_id}/...`
  - sidecar refs are absent unless the debug flag is explicitly enabled
  - existing non-ref payload fields survive unchanged
  - no local producer path appears anywhere in the returned/published JSON

Failure-mapping red tests:

- Route the B3a publisher failures through `transcript_to_plan()`:
  - missing core file
  - S3 upload exception after one prior upload
  - presign exception
  - presign returning `https://`
- Assert each returns `{"error": "dsl_artifact_unavailable", "run_id": ...}` through the current app error path and contains no stale local path anywhere in the JSON payload.

Green implementation:

- In `src/reel_af/app.py:1787-1789`, if `artifact_writer is None`, import the new publisher and create a small closure that passes `run_id=run_id`.
- Preserve the existing explicit writer seam: if a caller supplied `artifact_writer`, call that writer and do not also publish.
- Preserve the existing async-aware call shape (`inspect.isawaitable(...)`) so async tests/custom writers keep working.

Refactor guard:

- Keep the invalid-source guard before transcription/planning (`src/reel_af/app.py:1765-1766`).
- Do not add new web fields or identity-bearing data to the plan result.
- Configured bucket failures must never return fallback local refs.

### B7a - `transcript_to_plan()` keeps local refs in no-bucket dev

Given no bucket is configured, when `transcript_to_plan()` runs without an explicit `artifact_writer`, then the existing local-ref behavior remains unchanged, including local sidecar refs.

Red tests:

- Keep or strengthen the path test that currently expects local default refs under the resolved output root (`tests/planner/test_paths.py:80-107`).
- Add an assertion that no storage client is constructed when `REEL_BUCKET_NAME` is absent.
- Assert known sidecar refs remain present in no-bucket local dev.

Green implementation:

- The default publisher no-ops on no bucket.
- The `transcript_to_plan()` result stays behavior-compatible with `tests/planner/test_reasoner.py:144-153`.

Refactor guard:

- Do not make no-bucket transcript planning fail just because its refs are not web-submit-safe. The web boundary already rejects filesystem refs for browser-submitted remote render jobs (`tests/web/test_dsl_hooks_submit.py:175-186`).

### B8 - Published refs resolve through the DSL-hooks artifact consumer

Given `transcript_to_plan()` publishes HTTPS refs, when a separate `dsl_hooks_to_reels()` execution receives those refs, then `_resolve_artifact_ref()` fetches the artifacts into the render work dir and the worker compiles/renders from the fetched copies.

Red integration test:

- Add a test near `tests/planner/test_pipeline.py:347-389`.
- Run planner `plan(...)` or `transcript_to_plan(...)` to produce the artifact set.
- Publish with fake S3 and obtain HTTPS refs.
- Build an `artifact_fetch(url)` fake that reads the uploaded object bytes by URL/key.
- Call `dsl_hooks_to_reels()` with the published `composite_ref`, `words_ref`, and `hook_ref`.
- Fake segment fetch, stitch, finish, image, text, and mp4 uploader as needed to avoid network/ffmpeg.
- Assert:
  - `artifact_fetch` is called for all three HTTPS refs
  - local fetched copies are used by the worker path (`src/reel_af/app.py:1651-1656`)
  - result succeeds with `download_url`
  - no local producer path appears in the result JSON

Expired-presign red case:

- In the same consumer area, make `artifact_fetch(url)` raise `OSError("403 expired presign")` for one of the three core refs.
- Assert `dsl_hooks_to_reels()` returns `{"error": "dsl_artifact_unavailable", "run_id": ...}` and does not expose local producer or partially fetched paths.

Green implementation:

- B3 through B7a should be sufficient. `_resolve_artifact_ref()` already handles HTTP(S) fetch into `dest_dir/name` and the worker already maps artifact resolver failures to `dsl_artifact_unavailable` (`src/reel_af/app.py:1563-1578`, `src/reel_af/app.py:1650-1660`).

Refactor guard:

- Do not extend `_ARTIFACT_REF_SCHEMES`; `a1://` and HTTP(S) are the intended submit-safe forms (`web/reel_jobs.py:35-38`).
- Do not convert expired artifact fetches into `delivery_unavailable`; that error is for final mp4 delivery, not the plan artifact handoff.

### B9 - Published refs pass the web DSL-hooks submit allowlist unchanged

Given published HTTPS artifact refs, when the UI server builds a DSL-hooks submission, then the refs pass `_validated_artifact_ref()` and are forwarded unchanged in `cp_input`.

Red test:

- Add `test_published_https_artifact_refs_are_accepted()` near `tests/web/test_dsl_hooks_submit.py:109-118`.
- Use `https://s3.example/reel-uploads/plans/abc123/composite.ts.md?X-Amz-Expires=86400` style refs for all three artifact fields.
- Assert `build_submission(TARGET_DSL_HOOKS, {"input": body}).cp_input` contains exactly those values plus `source_url` and `clip_idx`.

Green implementation:

- Ideally none. Current allowlist accepts HTTP(S) refs (`web/reel_jobs.py:35-38`, `web/reel_jobs.py:215-227`).

Refactor guard:

- Keep local/filesystem ref rejection tests green (`tests/web/test_dsl_hooks_submit.py:175-186`).
- Do not add `uploader` or storage credentials to the web submit body; production cannot pass Python callables through CP JSON (`web/reel_jobs.py:452-487`).

### B10 - A1 poll delivery-required policy stays green

Given a succeeded DSL-hooks CP result, when poll sees an HTTP(S) `download_url`, then the job stays succeeded; when poll sees missing/non-browser delivery, the job becomes failed with `delivery_unavailable`.

Red tests:

- No new failing tests are required unless implementation touches web code. Existing tests already pin this behavior:
  - success with golden `download_url`: `tests/web/test_dsl_hooks_poll.py:80-98`
  - missing `download_url` terminal failure: `tests/web/test_dsl_hooks_poll.py:103-110`
  - no node-local path leak: `tests/web/test_dsl_hooks_poll.py:112-124`
  - non-browser URL rejection: `tests/web/test_dsl_hooks_poll.py:127-143`

Green implementation:

- No web implementation change.

Refactor guard:

- Keep `_resolve_result_ref()`, `_delivery_error()`, `_poll_response_body()`, and `_handle_poll()` unchanged unless a failing test proves a real bug (`web/server.py:227-314`, `web/server.py:759-785`).

## Acceptance Criteria

Automated acceptance:

- New artifact publisher unit tests pass with fake S3 and no real network.
- Publisher tests prove configured-bucket core publication is all-or-error for missing core files, mid-way `put_object` exceptions, presign exceptions, malformed `https://` presigns, and no stale local paths in returned/published JSON.
- Publisher tests prove `REEL_ARTIFACT_TTL_S` controls artifact presign `ExpiresIn`, falling back to `REEL_DELIVERY_TTL_S` and then 86400 when unset.
- Publisher tests prove fixed core keys: `plans/{run_id}/composite.ts.md`, `plans/{run_id}/transcript.words.json`, and `plans/{run_id}/hook-plan.json`.
- Hook-plan tests prove embedded `composite_ref` values are rewritten, `idempotency_key` values are preserved, and no raw local path appears anywhere else in uploaded hook-plan JSON.
- Sidecar tests prove bucket-backed production results scrub `mined_candidates_ref`, `accepted_candidates_ref`, `strategy_ref`, `blueprint_ref`, and `script_coherence_ref` by default, while no-bucket local dev keeps them unchanged.
- If debug sidecar publication is implemented, tests prove debug sidecar refs are valid hosted HTTP(S) URLs and debug sidecar publication failures raise rather than returning mixed local/published refs.
- New publisher-to-`_resolve_artifact_ref()` round-trip test passes before app-level writer wiring.
- New transcript-to-plan default-publication tests pass for bucket and no-bucket modes, including `dsl_artifact_unavailable` mapping for configured-bucket publisher failures.
- New round-trip integration test proves published HTTPS refs resolve through `_resolve_artifact_ref()` and can drive `dsl_hooks_to_reels()`.
- Expired-presign fetch test proves a 403/expired `OSError` from `artifact_fetch` returns `dsl_artifact_unavailable`.
- New/default-uploader DSL-hooks mp4 delivery regression tests pass for configured bucket and no bucket.
- Existing web submit and poll tests stay green, especially the filesystem-ref rejection and `delivery_unavailable` policy.

Behavioral acceptance:

- Production `transcript_to_plan()` with `REEL_BUCKET_NAME` set returns fetchable HTTP(S) refs for the generated A1 core artifact triple or returns `dsl_artifact_unavailable`; it never returns partial/stale local core refs in bucket mode.
- Production `transcript_to_plan()` with `REEL_BUCKET_NAME` set scrubs known sidecar refs by default; sidecar publication is debug-only if implemented.
- Production `dsl_hooks_to_reels()` still returns `result.download_url` for the final mp4 when object storage is configured.
- No-bucket local development remains fail-soft: transcript planning returns local core and sidecar refs, and DSL-hooks mp4 delivery returns `delivery_unavailable` rather than leaking a local mp4 path as success.
- Browser/UI contract remains `result.download_url`; no event-surface or web boundary changes are required for browser delivery.
- CP event downstream behavior is explicitly left to CP owners: event consumers must dereference `cp-execution://.../result` through CP or receive a CP-owned event contract change because the event exposes `reel_ref`, not `download_url`; `AF-u8u` remains the adjacent follow-up for gating `reel.completed` emission to reel-producing executions only.

## Validation Commands

Focused red/green loop:

```bash
uv run --extra dev python -m pytest tests/test_storage.py tests/test_a1_artifact_storage.py -q
uv run --extra dev python -m pytest tests/dsl/test_artifact_resolver.py tests/planner/test_pipeline.py -q
uv run --extra dev python -m pytest tests/planner/test_reasoner.py tests/planner/test_paths.py -q
uv run --extra dev python -m pytest tests/test_dsl_hooks_worker_closure.py -q
uv run --extra dev python -m pytest tests/web/test_dsl_hooks_submit.py tests/web/test_dsl_hooks_poll.py -q
```

Final gate:

```bash
set -a; . ./.env; set +a; uv run --extra dev python -m pytest tests/planner tests/dsl tests/web tests/test_storage.py tests/test_a1_artifact_storage.py tests/test_dsl_hooks_worker_closure.py -q
```

If the implementation keeps artifact publisher tests inside `tests/test_storage.py` instead of creating `tests/test_a1_artifact_storage.py`, drop the non-existent file from the focused and final commands.

The broad fallback is the full repository suite:

```bash
set -a; . ./.env; set +a; uv run --extra dev python -m pytest tests -q
```

## Deploy Note

Code validation is not the same as live delivery validation. AmberRobin's infra snapshot shows the Railway bucket and env vars already exist (`thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md:137-159`), but the current deployed `reel-af` image may lag this worktree. After implementation lands, an end-to-end production check needs an ops redeploy of `reel-af` and then a two-stage plan -> render run through the public UI. That redeploy is out of scope for this code plan.

Operations must also set `REEL_ARTIFACT_TTL_S` high enough for expected remote render queueing and manual plan-to-render delay. If unset, artifact URLs inherit `REEL_DELIVERY_TTL_S`; that may be acceptable only if the shared delivery TTL already covers those handoff delays.

## Implementation Order

1. Write B1 and B2 first to establish that mp4 delivery is already wired through the default uploader.
2. Write B3 and B3a to define the strict storage publisher core contract in isolation, including fixed core keys, exact `put_object` fake-S3 shape, URL validation, TTL configurability, and all-or-error failures.
3. Write B4 to pin hook-plan internal ref rewrite, immutable `idempotency_key`, and no local path leakage in uploaded hook-plan JSON.
4. Write B4a immediately after B3/B4 to prove published refs round-trip through `_resolve_artifact_ref()` before app-level writer wiring.
5. Write B5 and B6 to pin no-bucket local behavior, sidecar scrub behavior, and optional debug sidecar semantics.
6. Implement the publisher and TTL helper in `src/reel_af/storage.py`.
7. Write B7 and B7a around `transcript_to_plan()` default writer behavior, no-bucket behavior, and failure mapping.
8. Wire the default publisher closure in `src/reel_af/app.py`.
9. Write B8 and B9 to prove the producer output can feed the remote consumer and web boundary, including the B8 expired-presign fetch case.
10. Run B10's existing web poll suite and the final gate without changing the web submit/poll boundary.

## Risks And Review Focus

- The publisher must not construct boto3 when `REEL_BUCKET_NAME` is absent; no-bucket dev behavior is intentionally local.
- With a bucket configured, core triple publication must be all-or-error. Missing files, unreadable files, partial upload failures, presign failures, and malformed hosted URLs must raise from the publisher and map through `transcript_to_plan()` to `dsl_artifact_unavailable`.
- Producer publication must validate presigned artifact URLs with parsed scheme and host. A malformed `https://` ref must fail at the publisher boundary, not later in web submit or render consumption.
- The hook-plan internal `composite_ref` should be rewritten before upload to avoid embedding a producer-local path in a published artifact.
- Hook-plan `idempotency_key` values must remain immutable after planning, even though they were derived from the original local composite ref.
- Presigned artifact URLs must live long enough for the downstream render execution to start and fetch them. `REEL_ARTIFACT_TTL_S` is the explicit handoff TTL and should be reviewed with expected queue/manual delay.
- Sidecar refs are not part of the render contract. Publishing them by default expands exposure and failure surface; production should scrub them unless a debug flag is deliberately enabled.
- Web code should not need changes. Any implementation that requires changing `web/reel_jobs.py`, `web/server.py`, or `web/index.html` should be treated as suspect unless a red test proves the current boundary is wrong.
- CP event changes are out of scope for browser/UI delivery only. Downstream event semantics remain a CP-owned follow-up, especially event consumers of `cp-execution://.../result` and the existing `AF-u8u` issue for non-reel-producing executions.

## References

- Code-side research: `thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-code.md`
- Infra-side research: `thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md`
- Review folded into this revision: `thoughts/searchable/shared/plans/2026-07-20-reel-af-object-storage-delivery-REVIEW.md`
- MP4 storage adapter: `src/reel_af/storage.py:21-40`, `src/reel_af/storage.py:43-73`
- A1 artifact resolver: `src/reel_af/app.py:1539-1586`
- DSL-hooks default uploader and delivery: `src/reel_af/app.py:1599-1638`, `src/reel_af/app.py:1722-1745`
- Transcript-to-plan writer seam: `src/reel_af/app.py:1748-1792`
- Planner artifact outputs: `src/reel_af/planner/pipeline.py:218-238`, `src/reel_af/planner/pipeline.py:260-302`
- Hook-plan idempotency key: `src/reel_af/planner/serialize.py:198-205`, `src/reel_af/planner/serialize.py:368-379`
- Web artifact-ref allowlist: `web/reel_jobs.py:35-38`, `web/reel_jobs.py:215-227`, `web/reel_jobs.py:452-487`
- Web poll delivery policy: `web/server.py:227-314`, `web/server.py:759-785`
- Browser download link: `web/index.html:1543-1555`
