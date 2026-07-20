---
date: 2026-07-20T10:56:00-04:00
reviewer: CyanBarn
repository: silmari-reels-af
plan: thoughts/searchable/shared/plans/2026-07-20-reel-af-object-storage-delivery.md
research_inputs:
  - thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-code.md
  - thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md
beads: [AF-egx]
status: complete
---

# Review: AF-egx Object-Storage Delivery Plan

## Verdict

Needs revision.

The core architecture is right: use object storage for production A1 producer artifacts, keep `a1://` for co-located dev, leave the web submit/poll boundary intact, and keep final mp4 delivery on the existing `upload_reel()` path. The plan is not yet implementation-ready because the configured-bucket failure contract, URL validity contract, artifact TTL, sidecar exposure, and event-surface scoping need explicit amendments and tests.

## Review Summary

| Category | Status | Notes |
|---|---|---|
| Contracts | Needs revision | Configured-bucket artifact publication must be all-or-error for the core triple. |
| Interfaces | Needs revision | Publisher URL validation and fake-S3 method shape are underspecified. |
| Promises | Needs revision | Artifact URL TTL and retry/idempotency promises are thin. |
| Data Models | Needs revision | Sidecar refs and hook-plan internal rewrite need sharper public/private classification. |
| APIs | Needs revision | CP event scope is not a browser blocker, but the plan overstates safety for downstream consumers. |
| CodeCleanup Gates | Ready | Proposed control-flow shape is consistent with existing guard-clause and pure-question patterns. |

## Critical Findings

1. Configured-bucket core publication is not specified as all-or-error.

Evidence: B3 says the publisher uploads known refs and rewrites them to presigned URLs, but its assertions only check `startswith("https://")`, uploaded keys, and non-mutation (`thoughts/searchable/shared/plans/2026-07-20-reel-af-object-storage-delivery.md:124-147`). B4 only names hook JSON parse failure as a raise path (`...object-storage-delivery.md:167-171`). The existing `transcript_to_plan()` wrapper can correctly convert a raised writer failure into `dsl_artifact_unavailable` (`src/reel_af/app.py:1787-1792`), and the render consumer already maps unresolvable artifacts to the same error (`src/reel_af/app.py:1650-1660`). But the plan does not require the new publisher to raise on missing core files, partial S3 upload failure, presign failure, or malformed presigned URLs.

This matters because `_resolve_artifact_ref()` fetches only parsed HTTP(S) refs with a host, then falls through to local-path treatment for everything else (`src/reel_af/app.py:1574-1586`). The web submit boundary currently validates artifact refs with a prefix check only (`web/reel_jobs.py:215-227`), unlike final mp4 poll delivery, which uses a scheme+host URL validator (`web/server.py:263-287`, `tests/web/test_dsl_hooks_poll.py:127-137`). A malformed presign such as `https://` could pass submit and fail later in the render worker instead of failing at the producer publication boundary.

Amendment: add a B3/B6 negative contract: when `REEL_BUCKET_NAME` is set, the core triple (`composite_ref`, `words_ref`, `hook_ref`) is required, each source file must be readable, each upload must succeed, and each generated ref must parse as HTTP(S) with `netloc`. Any failure raises from the publisher and `transcript_to_plan()` returns `{"error": "dsl_artifact_unavailable", ...}`. Add tests for missing core file, S3 upload exception after a prior upload, presign exception, presign returning `https://`, and returned refs containing no stale local paths.

## Should-Fix Findings

2. Artifact presigned-URL TTL is a real handoff risk, not just a footnote.

Evidence: `upload_reel()` uses `REEL_DELIVERY_TTL_S` with a 24-hour default (`src/reel_af/storage.py:18`, `src/reel_af/storage.py:25-26`, `src/reel_af/storage.py:69-73`), and the plan reuses that TTL for artifact refs (`...object-storage-delivery.md:367-373`). But artifact URLs are consumed by a separate later `dsl_hooks_to_reels()` execution, which fetches the three refs at render start (`src/reel_af/app.py:1650-1656`), not by the browser immediately after production.

Amendment: split or explicitly document the TTL policy for producer artifacts. Prefer `REEL_ARTIFACT_TTL_S` with a conservative default, or explicitly state that `REEL_DELIVERY_TTL_S` governs both mp4 download and plan-artifact handoff and must exceed expected queue/manual delay. Add a B8 case where `artifact_fetch` raises a 403/expired-presign `OSError` and assert the worker returns `dsl_artifact_unavailable`, plus a publisher unit test proving the TTL passed to presign is configurable.

3. The CP `reel.completed.v1` event is not a UI blocker, but the plan's out-of-scope language is too broad for downstream consumers.

Evidence: the plan says no CP/Go event-contract change because the browser UI uses poll `result.download_url`, not the event (`...object-storage-delivery.md:54-57`, `...object-storage-delivery.md:317-323`). That is true for the current UI: the browser link uses `result.download_url` first (`web/index.html:1543-1553`), and poll stores `download_url` as `result_ref` when present (`web/server.py:227-241`). However the frozen event schema exposes only `reel_ref`, not `download_url` (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/sdk/python/agentfield/handoff/contracts/com.silmari.reel.completed/v1.schema.json:8-16`), and the CP production builder deliberately sets `reel_ref` to `cp-execution://{executionID}/result` (`/home/maceo/ntm_Dev/silmari-agentfield-system/agentfield/control-plane/internal/events/reel_completed.go:75-109`), with a test pinning that value (`.../reel_completed_test.go:98-103`). The infra research leaves downstream event-consumer expectations open (`thoughts/searchable/shared/research/2026-07-20-reel-af-upload-delivery-infra.md:310-317`).

Amendment: keep CP event changes out of this implementation plan, but narrow the statement to "out of scope for browser/UI delivery only." Add a follow-up note for BrownFox/CP owners: downstream event consumers either must dereference `cp-execution://.../result` through the CP or need a CP-owned event change. Also mention the adjacent existing CP issue that `reel.completed` emission can fire for `reel_transcript_to_plan`, which is a triple producer rather than a reel producer (`/home/maceo/ntm_Dev/silmari-agentfield-system/specs/reels-planner.a1-producer.spec.md:262-266`).

4. Publishing every sidecar may add exposure and failure surface without a current consumer.

Evidence: the plan publishes `mined_candidates_ref`, `accepted_candidates_ref`, `strategy_ref`, `blueprint_ref`, and `script_coherence_ref` to avoid local-path leakage (`...object-storage-delivery.md:44-52`, `...object-storage-delivery.md:124-140`). The actual render consumer accepts and resolves only `composite_ref`, `words_ref`, and `hook_ref` (`src/reel_af/app.py:1599-1603`, `src/reel_af/app.py:1650-1656`), and the web submit canonicalizer forwards only those three artifact refs (`web/reel_jobs.py:465-487`). The sidecars are returned by `_write_triple()` but are not part of the render handoff (`src/reel_af/planner/pipeline.py:293-302`).

Amendment: classify sidecars explicitly. Either publish them only behind a debug/config flag, or omit/scrub sidecar refs from bucket-backed production results while keeping them in no-bucket local dev. If they remain published by default, add tests and a threat note covering presigned sidecar exposure, sidecar upload failure semantics, and TTL.

5. The hook-plan rewrite needs an explicit idempotency-key decision.

Evidence: B4 rewrites `clips[*].composite_ref` before uploading the hook plan (`...object-storage-delivery.md:154-171`). `build_hook_plan()` writes both `composite_ref` and `idempotency_key` into each clip (`src/reel_af/planner/serialize.py:198-205`, `src/reel_af/planner/serialize.py:214-226`), and the key hash includes the original `composite_ref` (`src/reel_af/planner/serialize.py:368-379`). Rewriting only `composite_ref` preserves the existing key but makes the key derive from a producer-local path that no longer appears in the published hook plan.

Amendment: state whether `idempotency_key` is intentionally immutable after planning or should be recomputed from the published ref/run id. If immutable, add a B4 assertion that the key is preserved and no raw local path appears anywhere else in the uploaded JSON. If recomputed, add compatibility notes because hook-plan fixtures and specs treat `idempotency_key` as part of the clip contract (`/home/maceo/ntm_Dev/silmari-agentfield-system/specs/reels-planner.a1-producer.spec.md:245-258`).

## Nice-To-Have Findings

6. Use expected core filenames, not arbitrary source basenames, for core keys.

Basename-only construction is path-safe and matches `upload_reel()` (`src/reel_af/storage.py:53-67`, `tests/test_storage.py:86-94`). For the core triple, the safer publisher contract is field-to-fixed-name mapping: `composite_ref -> composite.ts.md`, `words_ref -> transcript.words.json`, and `hook_ref -> hook-plan.json`. That avoids duplicate-basename surprises if a future caller passes non-pipeline-shaped refs.

7. Make the fake-S3 seam match the production method shape.

B3 allows `put_object(...)` or `upload_file(...)` (`...object-storage-delivery.md:128-147`). The existing `FakeS3` only implements `upload_file` and `generate_presigned_url` (`tests/test_storage.py:13-24`). Because B4 rewrites hook JSON bytes, `put_object` is the cleaner artifact publisher interface; otherwise the implementation must write a temporary rewritten hook file and tests must verify that temp file's uploaded content. Pick one method in the plan and make the fake enforce the exact boto3 arguments.

8. Move one remote-consumer round-trip assertion earlier in the implementation order.

The order defers B8 until after `transcript_to_plan()` wiring (`...object-storage-delivery.md:357-365`). A minimal publisher -> `_resolve_artifact_ref()` round-trip can run immediately after B3/B4 and would catch malformed URL, query-string parsing, and uploaded-body mapping before the app-level writer is wired.

## Direct Answers To Review Questions

- Approach correctness: sound, once core publication is all-or-error. HTTPS object refs are right for the private Railway agent because existing comments define HTTP(S) as the production artifact path and `a1://` as co-located dev (`src/reel_af/app.py:1539-1547`). Keeping web submit/poll unchanged preserves the browser boundary and A1 delivery-required policy (`web/reel_jobs.py:35-38`, `web/reel_jobs.py:76-87`, `web/server.py:273-287`).
- Gating/fail-soft: sound for no-bucket local dev. With no `REEL_BUCKET_NAME`, preserving local refs matches the current local resolver/test behavior (`src/reel_af/app.py:1579-1586`, `tests/dsl/test_artifact_resolver.py:19-69`). With a configured bucket, fail-soft must stop; core publication errors should raise and map to `dsl_artifact_unavailable`.
- TTL: concern is real for delayed/manual/queued second-stage renders. A 24-hour default may be operationally acceptable, but the plan needs a named artifact TTL policy and an expired-fetch test.
- Sidecars: not required for rendering today and likely add more risk than value unless explicitly needed for debugging or audit.
- CP event scope: not necessary for current UI download, but not fully safe for downstream event consumers. The plan should name this as a CP-owned follow-up, not imply `download_url` solves the event surface.
- Test coverage: B1-B10 are a good skeleton, but missing negative tests for malformed presigned URL, partial upload, missing core artifact, hook-plan JSON unparseable, stale local refs in returned/published JSON, and sidecar failure/exposure semantics.

## Concrete Amendment Set

Add these plan changes before implementation:

```diff
+ Add B3a: configured-bucket publisher is core-triple all-or-error.
+ Add tests: missing core file, upload exception, presign exception, malformed presign URL, no stale local core refs in returned result.
+ Add B4 assertions: no local path anywhere in uploaded hook-plan JSON except intentionally preserved opaque hashes; document idempotency_key preservation or recomputation.
+ Add B5/B6 assertion: no S3 client is constructed only when bucket is absent; configured bucket failures never return local refs.
+ Add B8 expired-presign fetch case returning dsl_artifact_unavailable.
+ Add artifact TTL policy: separate REEL_ARTIFACT_TTL_S or documented REEL_DELIVERY_TTL_S operational bound.
~ Reconsider sidecars: publish only if explicitly classified, or scrub/omit from bucket-backed production result.
~ Narrow CP event out-of-scope statement to UI-only delivery and create/name a CP-owned follow-up for downstream event consumers.
```
