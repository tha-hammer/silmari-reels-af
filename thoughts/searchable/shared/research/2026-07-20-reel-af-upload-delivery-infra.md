---
date: 2026-07-20T10:04:01-04:00
researcher: AmberRobin
git_commit: 3515f10dce5d98565c82099cc4fea111f85cd354
branch: reel-af-a1-producer-impl
repository: silmari-reels-af
topic: "AF-egx INFRA/CONFIG side of reel mp4 browser delivery"
tags: [research, codebase, reel-af, delivery, railway, object-storage, AF-egx]
status: complete
last_updated: 2026-07-20
last_updated_by: AmberRobin
beads: [AF-egx]
---

# Research: AF-egx INFRA/CONFIG side of reel mp4 browser delivery

**Date**: 2026-07-20T10:04:01-04:00  
**Researcher**: AmberRobin  
**Git Commit**: 3515f10dce5d98565c82099cc4fea111f85cd354  
**Branch**: reel-af-a1-producer-impl  
**Repository**: silmari-reels-af

## Research Question

Trace the infrastructure/config side of rendered A1 reel mp4 delivery: bucket/object-storage config,
Railway wiring, the `reel.completed.v1`/UI delivery contract, and the trade-off between uploading
to shared object storage versus serving files from the private reel-af agent.

## Summary

The code is already built around S3-compatible object storage. `src/reel_af/storage.py` selects a
bucket with `REEL_BUCKET_NAME`, builds a lazy boto3 S3 client from `REEL_BUCKET_ENDPOINT`,
`REEL_BUCKET_ACCESS_KEY_ID`, `REEL_BUCKET_SECRET_ACCESS_KEY`, and `REEL_BUCKET_REGION`, uploads the
produced mp4 under `outputs/{run_id}/{basename}`, and returns a presigned GET URL
(`src/reel_af/storage.py:21-39`, `src/reel_af/storage.py:61-73`).

The package dependency is boto3, not MinIO-specific: `pyproject.toml` adds `boto3>=1.34` for
produced-reel delivery (`pyproject.toml:31-33`), and the separate UI image also includes
`boto3>=1.34` for file-mode composite uploads/presigns (`web/requirements.txt:9-11`). I found no
`minio` or `aioboto3` package references in the manifests.

The deploy docs still record the original gap: the agent is private, the UI is public, and
rendered-file retrieval needs object storage or a file-serving route (`docs/railway-deployment.md:21-22`,
`docs/railway-deployment.md:225-228`, `deploy/RAILWAY-RUNBOOK.md:215-216`). Current read-only
Railway state on 2026-07-20 shows the project now has a bucket and env wiring: logical bucket
`reel-uploads`, actual `REEL_BUCKET_NAME=reel-uploads-uklwihvil-ta`, endpoint
`https://t3.storageapi.dev`, region `auto`, and access key/secret set on both `reel-af` and
`reel-af-ui` (secrets redacted). `reel-af` also has `REEL_AF_OUTPUT_ROOT=/app/resources` and a ready
Railway volume mounted at `/app/resources`.

The browser delivery contract is `result.download_url` as an HTTP(S) URL, usually presigned. The
public UI only displays a download link from `result.download_url` and will not fall back to the
input URL (`web/index.html:1543-1553`). The web poller prefers `download_url`/`url` for `result_ref`
(`web/server.py:227-240`), and for the A1 DSL-hooks target it marks success as failed with
`delivery_unavailable` unless that ref is a browser-fetchable HTTP(S) URL with a host
(`web/server.py:263-287`, `web/server.py:759-785`, `web/reel_jobs.py:76-86`,
`web/reel_jobs.py:252-257`).

Recommendation: use shared S3-compatible object storage and return a presigned/public HTTPS
`download_url` from the reel execution result. Keep the `/app/resources` volume as durable
agent-local staging/artifact storage. A file-serving route is possible, but because `reel-af` is
private and has no public domain, that route would need to be exposed through `reel-af-ui` or a new
public boundary with auth, path safety, range/large-file handling, and ownership checks.

## Detailed Findings

### 1. Object-storage and bucket configuration

`src/reel_af/storage.py` is the produced-reel delivery adapter:

- Bucket selection: `_bucket()` reads `REEL_BUCKET_NAME` and returns `None` if unset
  (`src/reel_af/storage.py:21-22`).
- Delivery URL TTL: `_delivery_ttl_s()` reads `REEL_DELIVERY_TTL_S`, defaulting to 86400 seconds
  (`src/reel_af/storage.py:18`, `src/reel_af/storage.py:25-26`).
- S3-compatible client: `_client()` lazy-imports boto3 and builds `boto3.client("s3", ...)` using
  `REEL_BUCKET_ENDPOINT`, `REEL_BUCKET_ACCESS_KEY_ID`, `REEL_BUCKET_SECRET_ACCESS_KEY`, and
  `REEL_BUCKET_REGION` with default `"auto"` (`src/reel_af/storage.py:29-40`).
- Upload contract: `upload_reel()` returns `None` when the bucket is unset or the file is missing,
  otherwise uploads to key `outputs/{run_id}/{basename}` and calls
  `generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=...)`
  (`src/reel_af/storage.py:43-73`).

The older reasoners all use this adapter when they try to deliver a rendered reel:

- `article_to_reel()` imports `upload_reel`, calls it with `final["video_path"]`, and conditionally
  spreads `download_url` into the result (`src/reel_af/app.py:497-517`).
- `topic_to_reel()` does the same (`src/reel_af/app.py:651-675`).
- `composite_to_reel()` explicitly notes that the source URL is not surfaced as the reel download,
  then uploads the produced result and conditionally returns `download_url`
  (`src/reel_af/app.py:844-865`).
- `research_to_reel()` uploads and returns `download_url` when present
  (`src/reel_af/app.py:1323-1371`).

The A1 DSL-hooks reasoner is stricter: it treats delivery as required and returns terminal
`delivery_unavailable` when the uploader returns `None` or a non-browser-deliverable URL
(`src/reel_af/app.py:1613-1626`, `src/reel_af/app.py:1722-1738`). Its URL predicate is
HTTP(S)-only with a host (`src/reel_af/app.py:1503-1508`, `src/reel_af/dsl/models.py:49-57`).

The UI side mirrors the same S3-compatible bucket config:

- `web/storage.py` uses `REEL_PRESIGN_TTL_S` with default from `web/media_config.json`
  (`web/storage.py:40-41`, `web/media_config.json:3-6`).
- `web/storage.py` builds the same boto3 client from `REEL_BUCKET_ENDPOINT`,
  `REEL_BUCKET_ACCESS_KEY_ID`, `REEL_BUCKET_SECRET_ACCESS_KEY`, and `REEL_BUCKET_REGION`
  (`web/storage.py:44-59`).
- `ObjectStorage._bucket()` fails closed with 503 when `REEL_BUCKET_NAME` is unset
  (`web/storage.py:70-74`).
- `web/uploads.py` has `LocalUploadStore` for `REEL_UPLOAD_DIR`, but local uploads cannot be
  presigned for the separate reel-af node and fail closed for file-mode composites
  (`web/uploads.py:55-89`).
- `BucketUploadStore` writes uploads to the shared bucket and presigns a GET URL for the render
  agent to fetch; it uses the same bucket env and `_s3_client_from_env()` helper
  (`web/uploads.py:92-148`).
- `default_deps()` chooses `BucketUploadStore()` when `REEL_BUCKET_NAME` is set, otherwise
  `LocalUploadStore()`, and wires `ObjectStorage()` as the media object store
  (`web/deps.py:346-382`).

Output-root config is separate from browser delivery. `REEL_AF_OUTPUT_ROOT` selects the generated
artifact root through `resolve_output_root()` (`src/reel_af/planner/paths.py:10-30`); it controls
where the agent writes local artifacts, not how the browser fetches the final mp4.

### 2. Railway state and deployment docs

The docs define the deploy shape as two services in the existing `silmari-deep-research` Railway
project: `reel-af` is private on port 8002, and `reel-af-ui` is public (`docs/railway-deployment.md:15-23`).
The source of truth is repo files plus Railway per-environment secrets (`docs/railway-deployment.md:6-9`,
`deploy/RAILWAY-RUNBOOK.md:7-8`). The agent service `railway.toml` calls it a private Railway
service (`railway.toml:1-5`), while the UI service `web/railway.toml` calls it public
(`web/railway.toml:1-3`).

The deploy docs did not originally list bucket vars for reel-af. They list the core agent and UI
env vars (`docs/railway-deployment.md:63-86`), and then mark rendered-file retrieval as a known
limitation requiring object storage or a file-serving route (`docs/railway-deployment.md:225-228`).
The runbook repeats that a finished reel lands on the agent filesystem and the browser cannot
download it without object storage or a file-serving route (`deploy/RAILWAY-RUNBOOK.md:215-216`).

Read-only Railway CLI snapshot from `/home/maceo/ntm_Dev/silmari-agentfield-system` on
2026-07-20:

- Project: `silmari-deep-research` (`5dcbd074-f4f2-4284-b355-3e332d4538a5`), workspace
  `"Maceo's Projects"`, matching the docs (`docs/railway-deployment.md:42-54`).
- Bucket inventory: logical Railway bucket `reel-uploads`
  (`06435a19-5261-4d51-a4f6-8e98b0c2455d`), `region=iad`, `objects=57`, `storage=2.9 GB`.
- `reel-af` env keys present: `REEL_AF_OUTPUT_ROOT=/app/resources`,
  `REEL_BUCKET_NAME=reel-uploads-uklwihvil-ta`, `REEL_BUCKET_ENDPOINT=https://t3.storageapi.dev`,
  `REEL_BUCKET_REGION=auto`, plus `REEL_BUCKET_ACCESS_KEY_ID` and
  `REEL_BUCKET_SECRET_ACCESS_KEY` set (secret values redacted).
- `reel-af-ui` env keys present: `REEL_UPLOAD_DIR=/data/uploads`,
  `REEL_BUCKET_NAME=reel-uploads-uklwihvil-ta`, `REEL_BUCKET_ENDPOINT=https://t3.storageapi.dev`,
  `REEL_BUCKET_REGION=auto`, plus the access key and secret set (secret values redacted).
- Volumes: `reel-af` has ready volume `reel-af-volume` at `/app/resources`; `reel-af-ui` has ready
  volume `reel-af-ui-volume` at `/data/uploads`.
- Domains: `reel-af` has no public domains; `reel-af-ui` has active public domains
  `reel-af-ui-production.up.railway.app` and `reels.silmari.ai`.

Current infra conclusion: production no longer looks blocked by "no bucket exists" or "bucket env
unset". The current infra state matches the object-storage path. If A1 still returns
`delivery_unavailable`, the remaining cause is more likely code/result-contract/deploy freshness
than absent Railway bucket wiring.

### 3. Delivery contract: event `reel_ref` versus UI `download_url`

There are two related but different reference surfaces.

The frozen `com.silmari.reel.completed.v1` event schema requires `run_id`, `status`,
`reel_ref`, `source_execution_id`, `duration_s`, and `beat_count`, with no extra fields
(`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/sdk/python/agentfield/handoff/contracts/com.silmari.reel.completed/v1.schema.json:1-17`).
The repo specs state the same DTO shape (`specs/provenance-handoff-plug-in-guide.md:15-18`) and
note that the control-plane production emitter reads reel completion fields from the execution
result payload (`specs/reels-planner.a1-producer.spec.md:336-340`).

In the control-plane production event builder, however, `reel_ref` is currently a CP execution
reference, not a bucket URL:

- The builder defines `ReelCompletedEventType = "com.silmari.reel.completed.v1"` and
  `reelResultRefScheme = "cp-execution"`
  (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/control-plane/internal/events/reel_completed.go:18-31`).
- `reelResultRef(executionID)` returns `cp-execution://{executionID}/result`
  (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/control-plane/internal/events/reel_completed.go:75-78`).
- The event data sets `ReelRef` to that CP execution ref, while `source_execution_id`,
  `duration_s`, and `beat_count` come from the result payload snapshot
  (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/control-plane/internal/events/reel_completed.go:99-112`).
- The builder is registered in the same completion-outbox path as research completions
  (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/control-plane/internal/events/completed_outbox.go:10-17`),
  and `completeExecution` calls the shared outbox builder in the terminal state-write transaction
  (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/control-plane/internal/handlers/execute.go:1910-1917`).

`src/reel_af/app.py` also has a best-effort SDK announce path for `research_to_reel`, but its own
comment says the CP-side builder is the production producer (`src/reel_af/app.py:1341-1343`).
That reference surface sets `"reel_ref": download_url or final.get("video_path", "")`
(`src/reel_af/app.py:1344-1359`), and tests assert the bucket URL is used when upload succeeds
(`tests/test_research_to_reel.py:352-394`) while a local path is used when upload returns `None`
(`tests/test_research_to_reel.py:397-424`).

The active browser/UI path does not read the event `reel_ref`. The UI polls execution state and
renders a download button only from `result.download_url`:

- The page has a `resultLink` download anchor (`web/index.html:642-648`).
- `finish(result)` sets the displayed path from `video_path`/`path`/`output`, but the actual link is
  `result.download_url` first; it only falls back to an HTTP-looking output path and never to
  `result.url` (`web/index.html:1543-1553`).
- `_resolve_result_ref()` on the UI server prefers `result.download_url` or `result.url` when present,
  then scheme-gates `object_uri`/`uri`/`path`, and only wraps raw `video_path` as an internal
  `cp-execution://.../result/video_path` placeholder (`web/server.py:227-240`).
- For DSL-hooks only, `_delivery_error()` rejects a succeeded execution unless the resolved ref is a
  valid HTTP(S) URL with a host (`web/server.py:263-287`, `web/reel_jobs.py:252-257`).
- `_handle_poll()` applies that policy before updating the `reel_job` row and strips local result
  payload on `delivery_unavailable` (`web/server.py:759-785`, `web/server.py:290-314`).

Contract conclusion: for browser delivery, production should put a browser-fetchable HTTP(S) URL in
the execution result as `download_url`. The `reel.completed.v1` event's CP-produced `reel_ref` is an
internal by-reference CP pointer today; it is not the same thing as the UI download URL.

### 4. Delivery options for a private agent with `/app/resources`

Option A: upload mp4 to shared object storage, then return a presigned/public HTTPS URL.

- Fits the current code: `upload_reel()` already uploads produced reels and returns a presigned GET
  URL (`src/reel_af/storage.py:43-73`).
- Fits the current infra: Railway has a bucket, both reel services have `REEL_BUCKET_*`, and the
  agent has `REEL_AF_OUTPUT_ROOT=/app/resources` for local staging.
- Fits the UI contract: the public UI expects `result.download_url` and the DSL-hooks poll path
  requires HTTP(S) with host (`web/index.html:1543-1553`, `web/server.py:273-287`).
- Fits a private agent: the browser never needs to reach `reel-af.railway.internal`; it fetches the
  object-store URL directly.
- Trade-offs: requires bucket lifecycle/retention decisions, secret rotation, cost/egress tracking,
  and making sure returned URLs live long enough for user download. `REEL_DELIVERY_TTL_S` defaults
  to 24 hours for produced reels (`src/reel_af/storage.py:18`, `src/reel_af/storage.py:25-26`);
  UI upload presigns default to 1 hour (`web/uploads.py:21-31`).

Option B: add a file-serving route.

- Directly uses the existing `/app/resources` volume and avoids the upload step.
- Could be auth-gated and org-scoped if implemented behind `reel-af-ui`, similar to carousel image
  serving, where `_handle_slide()` auths, verifies the object exists, and 302s to a presigned URL
  (`web/server.py:740-749`).
- But a browser cannot call the private agent: `reel-af` has no public domains, while `reel-af-ui`
  has public domains. A route "on the reel-af agent" would require exposing the agent publicly or
  proxying through another public service.
- A proxy/streaming route needs careful path authorization, path traversal protection, support for
  large mp4/range requests, MIME and download headers, and tenant ownership mapping. It also ties
  browser delivery to the single service-local volume where the file was produced.
- It is weaker for cross-app delivery: other apps and event consumers still need a public or
  fetchable reference. A private service-local file path or `cp-execution://` pointer is not enough
  for a browser.

Recommended approach: treat object storage as the production delivery path. Keep the file-serving
route as a fallback or debugging route only if it is placed behind the public UI/auth boundary, not
as a direct browser route on the private agent.

## Workflow Closure Map

Behavior studied: a rendered A1 DSL-hooks mp4 becomes browser-deliverable through the public UI.

Current production chain:

`browser submit/poll -> reel-af-ui auth/job row -> control-plane async execution -> private
reel-af dsl_hooks_to_reels -> local render under output root -> upload_reel S3-compatible bucket ->
execution result.download_url -> reel-af-ui poll resolver/delivery policy -> UI download link`.

Evidence:

- UI dispatch/poll boundary is public, authenticated, and writes/reads `reel_job` rows before/after
  control-plane calls (`docs/railway-deployment.md:27-35`, `web/server.py:759-785`).
- DSL-hooks worker says delivery is required and does not return node-local `video_path` as success
  (`src/reel_af/app.py:1613-1626`, `src/reel_af/app.py:1722-1738`).
- Bucket upload is the produced-mp4 delivery step (`src/reel_af/storage.py:43-73`).
- Poll resolution and A1 delivery policy require browser-fetchable HTTP(S)
  (`web/server.py:227-240`, `web/server.py:263-287`).
- UI rendering consumes `result.download_url` for the browser link (`web/index.html:1543-1553`).

Load-bearing labels:

- `src/reel_af.app.dsl_hooks_to_reels`: production-called by AgentField reasoner registration;
  private agent entrypoint (`src/reel_af/app.py:1598-1639`).
- `src/reel_af.storage.upload_reel`: production-called by reasoners; returns presigned URL or
  `None` based on bucket/file state (`src/reel_af/storage.py:43-73`).
- `web.server._resolve_result_ref`: production-called by `_handle_poll` on every successful CP
  poll (`web/server.py:227-240`, `web/server.py:772-784`).
- `web.server._delivery_error`: production-called by `_handle_poll`; DSL-hooks-only guard
  (`web/server.py:273-287`, `web/server.py:778-780`).
- `web.index.html finish(result)`: production UI consumer of `download_url`
  (`web/index.html:1543-1553`).

No closure adapter file was staged: this pass is infra/config research only, and the requested
deliverable was this single research document.

## Historical Context

- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md:83-84` states that
  browser-deliverable mp4s should use object storage or a serving route, because a producer-local
  volume is durable storage for the service, not a cross-service API.
- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md:99-102` identifies
  shared bucket upload as the browser-delivery path and cites the same storage adapter.
- `thoughts/searchable/shared/research/2026-07-19-reel-af-output-path-sota.md:396-400` says final
  mp4 delivery should use object storage or a file-serving route, and notes `upload_reel()` already
  models bucket delivery.
- `thoughts/searchable/shared/plans/2026-07-11-prd-carousel-image-pipeline-and-research-handoff.md:130-135`
  resolved the earlier carousel media-serving gap toward `REEL_BUCKET_*` and presigned URLs rather
  than an app filesystem streaming route.
- `thoughts/searchable/shared/plans/2026-07-11-tdd-03-media-serving-storageport.md:18-26` defined
  the `StoragePort`/`ObjectStorage` pattern with S3-compatible env, org-scoped keys, presigned GET
  URLs, and fail-closed behavior.
- `thoughts/searchable/shared/plans/2026-07-15-12-44-tdd-reel-af-dsl-hooks-target.md:206-211`
  scoped DSL-hooks success to browser-deliverable HTTP(S) refs, and
  `thoughts/searchable/shared/plans/2026-07-15-12-44-tdd-reel-af-dsl-hooks-target.md:1599-1609`
  records the completed poll behavior: `download_url` succeeds, local/non-HTTP delivery becomes
  terminal `delivery_unavailable`.

## Open Questions

- Whether the currently deployed `reel-af` image includes the exact local commit that enforces
  DSL-hooks `download_url` success; Railway latest deployment in the status snapshot pointed at a
  separate upstream commit, not this worktree commit.
- Whether downstream event consumers expect `reel.completed.v1.data.reel_ref` to be a browser URL.
  The CP production builder currently emits `cp-execution://{execution_id}/result`; changing that
  event surface would be CP/contract work, separate from the UI browser delivery path.
- Bucket lifecycle/retention policy for produced mp4s is not documented in this repo. Presigned URL
  TTL controls URL validity, not object expiration.
