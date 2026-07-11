# Reels-AF UI Configuration Options - TDD Implementation Plan

## Overview

The current `reel-af` Cutting Room UI is a single static page with source selection,
three preset buttons, and one `ROLL` action. It hides a real job-safe composite setting
(`count`), does not explain the selected preset, and gives no visibility into the
polished-output defaults used later by the render pipeline.

This plan implements the smallest production-safe configuration pass:

- Make `count` a real editable composite job setting.
- Show exact, safe preset metadata copied from `src/reel_af/render/config/presets.json`.
- Show global finish/polished-output defaults as read-only context.
- Harden the authenticated web submit boundary so browser users can send only supported
  per-job fields.
- Bound browser submit-pending retries and preserve server retry hints during polling.

This plan has been revised from review
`thoughts/searchable/shared/plans/2026-07-11-14-52-tdd-reels-af-ui-configuration-options-REVIEW.md`.
The review found no critical blocker, but all warning items are converted below into explicit
contracts, tests, and implementation notes.

## Review-Driven Contract Decisions

### API Scope

- Only `POST /api/v1/execute/async/{target}` input canonicalization changes.
- `POST /api/v1/uploads`, `GET /api/v1/executions/{id}`, static routes, and unknown `/api/*`
  behavior stay unchanged.
- Rejecting previously accepted unknown per-job fields is an intentional authenticated UI-boundary
  hardening break. Auth and authorization still run before body validation, so unauthenticated
  invalid bodies return `401`, not `400`.
- No render-config endpoint is added in this pass. The deployed `web/` image is built from the
  `web/` context and must not import root render config at runtime.

### Submit Canonicalization

`input.client_request_id` is metadata accepted for every target. `web/server.py:_client_request_id()`
uses it only when the `Idempotency-Key` header is absent. The header wins when both are present.
`client_request_id` is never persisted in `submission.params` and never forwarded in `cp_input`.

Canonical persisted params and control-plane input are exact:

| Mode | Accepted input keys | `submission.params` | CP input before server presign | Dispatched CP input |
| ---- | ------------------- | ------------------- | ------------------------------ | ------------------- |
| Topic | `topic`, `client_request_id` | `{"target": TARGET_TOPIC}` | `{"topic": topic}` | same |
| Composite URL | `url`, `preset`, `count`, `client_request_id`, legacy `source` only when it equals normalized `url` | `{"target": TARGET_COMPOSITE, "source_mode": "url", "preset": preset, "count": count}` | `{"url": url, "preset": preset, "count": count}` | same |
| Composite file | `source`, `preset`, `count`, `client_request_id` | `{"target": TARGET_COMPOSITE, "source_mode": "file", "preset": preset, "count": count}` | `{"source": handle, "preset": preset, "count": count}` | `{"url": signed_url, "preset": preset, "count": count}` |

The legacy URL-mode duplicate `source` key is compatibility-only. It must match the normalized `url`,
is not persisted, and is not forwarded. A mismatch returns `400` with code `invalid_source`.

### Count Contract

- `COMPOSITE_COUNT_DEFAULT = 1`
- `COMPOSITE_COUNT_MIN = 1`
- `COMPOSITE_COUNT_MAX = 12`
- Missing `count` defaults to `1`.
- Integers and decimal digit strings such as `"3"` or `" 3 "` are accepted and normalized to `int`.
- Booleans, floats, fractional strings, non-numeric strings, values below `1`, and values above `12`
  return `400` with code `invalid_count`.
- Topic submissions do not accept `count`; they return `400` with code `unsupported_input_field`.
- `CFG.ui.countMin`, `CFG.ui.countMax`, and `CFG.ui.countDefault` must match backend constants.

### UI Config Mapping

Preset details are a static copy of `src/reel_af/render/config/presets.json` using this schema:

```json
{
  "description": "string",
  "reelSeconds": 120,
  "canvas": {"w": 1080, "h": 1920},
  "overlay": "middle_third",
  "remotionComposition": "MiddleThird",
  "overlayAccent": "#7E22CE",
  "phrase": {
    "maxWords": 6,
    "maxDurationS": 3.2,
    "gapS": 0.4,
    "holdS": 0.6,
    "uppercase": false,
    "verticalAnchor": 0.32,
    "captionsBurnedSource": false
  },
  "lowerThird": {
    "durationS": 6.0
  },
  "images": {
    "placement": "full",
    "count": 6,
    "everyS": 30,
    "zoom": true
  }
}
```

Fields not present for a preset are omitted. Browser rendering must build new metadata rows with DOM
nodes and `textContent`; do not expand config-sourced `innerHTML`.

Finish defaults are global finish-stage defaults, not selected-preset geometry. The panel label must
make that scope clear, because `finish.json` is `1080x1920` while the lower-third preset is `1920x1080`.
The read-only config maps from `src/reel_af/render/config/finish.json`:

```json
{
  "readOnly": true,
  "scope": "global_finish_stage_defaults",
  "geometry": {
    "canvas": {"w": 1080, "h": 1920},
    "centerX": 540,
    "captionSafeY": 1344,
    "dividerY": 772,
    "imageRegion": {"x": 0, "y": 800, "w": 1080, "h": 1120}
  },
  "captions": {
    "maxWords": 4,
    "maxDurationS": 1.8,
    "gapS": 0.35,
    "uppercase": true
  },
  "images": {
    "count": 3,
    "placement": "full",
    "minDurationS": 2.0,
    "maxDurationS": 3.0,
    "edgeGuardS": 2.0
  },
  "encode": {
    "crf": 19,
    "preset": "fast",
    "contextOnly": true
  }
}
```

## Harness Reality

There is no JavaScript test harness in this repo. The browser UI is one vanilla-JS IIFE inside
`web/index.html`, and `tests/web/test_index_contract.py` currently performs string-contract checks
against the HTML. Therefore:

- UI wiring is covered by Python contract tests that parse/assert the inline `#config` and required
  HTML/JS strings.
- Browser interactions and layout are manually verified.
- Server-side submit/canonicalization behavior is fully automated with the existing Flask fake-port
  harness in `tests/web/conftest.py`.

## Current State Analysis

### Key Discoveries

- `web/index.html:12-64` owns the browser config block. It currently contains API paths, three
  visible presets, copy, stage labels, and status mappings.
- `web/index.html:389` has only `{ mode, preset, file, busy }` in browser state. There is no `count`
  or configuration state.
- `web/index.html:409-418` renders presets via `innerHTML` with only label/subtitle/ratio.
- `web/index.html:515-520` builds submit bodies as `{topic}`, `{source, preset}`, or
  `{url, source, preset}`. It never sends `count`, and URL mode includes a legacy duplicate `source`
  key.
- `web/index.html:578-591` retries `409 idempotent_request_pending` forever. `CFG.api.timeoutMs`
  only applies after an `execution_id` exists and `poll()` starts.
- `web/index.html:607-610` polls by parsing JSON and ignores non-OK status semantics and
  `Retry-After` headers preserved by the server for transient CP 429/5xx responses.
- `web/reel_jobs.py:87-102` strips identity/idempotency metadata but otherwise stores and forwards
  cleaned input. Target-specific allowed-key validation must happen before generic cleanup.
- `web/reel_jobs.py:116-175` handles topic first and otherwise assumes composite. This pass should
  add an explicit `elif target == TARGET_COMPOSITE` branch plus an unreachable unsupported-target
  guard.
- `web/server.py:67-74` gives `Idempotency-Key` header precedence over `input.client_request_id`.
- `web/server.py:140-153` turns file-mode upload handles into presigned URLs and removes the raw
  `source` handle before control-plane dispatch.
- `tests/web/test_submit.py` currently uses parametrization in the planned changes but does not
  import `pytest`; the implementation must add `import pytest`.
- `src/reel_af/app.py:698-727` already accepts `count: int = 1` for
  `reel-af.reel_composite_to_reel` and bounds output by available windows.
- `src/reel_af/render/config/presets.json:2-31` is the source for safe preset metadata.
- `src/reel_af/render/config/finish.json:2-64` is the source for displayed global finish defaults.
- `src/reel_af/render/finish_config.py:52,69,80` uses strict models with `extra="forbid"`;
  per-job finish overrides stay out of scope.
- `web/Dockerfile:8-11` builds the web service from the `web/` context and only installs
  `web/requirements.txt`; the deployed web image cannot safely import `reel_af.render`.

### Existing Test Patterns

- `tests/web/test_submit.py:24-187` uses Flask test clients and fakes to assert auth, forbidden
  identity fields, file presign, dispatch bodies, and no row/CP on failure.
- `tests/web/test_dispatch.py:28-59` asserts idempotency behavior and `Retry-After` on pending
  idempotent submits.
- `tests/web/test_index_contract.py:10-17` reads `web/index.html` and asserts literal browser
  contract strings.
- `tests/test_finish_config.py:24-123` pins finish defaults, position tags, overrides, and unknown
  field rejection.
- `tests/test_reels_cli.py:25-181` pins preset dispatch behavior and lower-third preset wiring.
- `pyproject.toml:50-63` configures pytest and Ruff. CI runs `uv run --extra dev ruff check tests/`
  and `uv run --extra dev python -m pytest tests/ -q`.

## Desired End State

A signed-in browser user can see what each reel format actually means, choose how many composite
reels to cut, and understand where the global polished-output stage will place captions, banners,
image cut-ins, and encode defaults. Submitting a composite job sends only supported fields:

- URL mode browser input: `{url, preset, count}`
- File mode browser input: `{source, preset, count}`
- File mode dispatched CP input after presign: `{url, preset, count}`
- Topic mode browser and CP input: `{topic}`

The backend rejects unsupported per-job fields such as `out_dir`, `finish_config`, `canvas_w`,
`whisper_model`, `encode_crf`, `remotion_project_dir`, and arbitrary unknown keys before DB insert,
presign, or control-plane dispatch where applicable.

### Observable Behaviors

1. Given a composite URL job with `count=3`, when the user rolls, then the web backend dispatches
   `{"input": {"url": "...", "preset": "...", "count": 3}}`.
2. Given a composite file job with `count=2`, when the user rolls, then the web backend presigns the
   file handle and dispatches `{"input": {"url": "<signed>", "preset": "...", "count": 2}}`.
3. Given a composite job omits `count`, when the user submits, then the backend persists and
   dispatches `count: 1`.
4. Given malformed or unsafe authenticated config input, when the user submits, then the backend
   returns `400` with a stable JSON `code` and makes no row or control-plane call.
5. Given file-mode input contains unsupported fields or invalid count, when the user submits, then
   the backend returns `400` before presign, DB insert, or CP dispatch.
6. Given `input.client_request_id` is supplied without the header, when the request is retried, then
   idempotency dedupes it; given both header and body key are supplied, the header wins.
7. Given the UI loads, when presets are rendered, then each composite preset displays safe metadata
   that exactly matches `presets.json`.
8. Given a topic preset is selected, when the user rolls, then no composite `count`, preset metadata,
   or finish defaults are sent in the job input.
9. Given the UI loads, when configuration panels render, then global finish defaults are visible as
   read-only context and cannot be edited or submitted.
10. Given a submit remains in `409 idempotent_request_pending`, when the submit-pending timeout
    expires, then the browser stops retrying and reports a timeout without cancelling CP work.
11. Given poll receives transient `429` or `5xx`, when the server includes `Retry-After`, then the
    browser honors that delay and continues polling within the normal timeout.

## What We're Not Doing

- Not surfacing or allowlisting `reel-af.reel_article_to_reel`.
- Not making finish/default positioning fields editable per job.
- Not sending `finish.json`, `ReelFinishConfig`, provider settings, local paths, or encode settings
  to the reasoner.
- Not changing the upload route request/response contract.
- Not adding a render-config API endpoint in this pass.
- Not building a JS/Jest/Vitest/Playwright test harness.
- Not implementing a cancel API. Browser timeout is observation-only and does not cancel CP
  execution or delete/reconcile the durable job.
- Not implementing Projects, persisted assets, source reuse, segment-window selection, or carousel UI.

## Testing Strategy

- Framework: `pytest` with the existing Flask fake-port harness.
- Server tests: extend `tests/web/test_submit.py` for count normalization, canonical params/dispatch,
  unsafe-field rejection, auth precedence, source mismatch, and no-presign/no-row/no-CP cases.
- Idempotency tests: extend `tests/web/test_dispatch.py` for `input.client_request_id` fallback and
  header precedence.
- UI contract tests: extend `tests/web/test_index_contract.py` to parse `#config`, compare static UI
  copies against `presets.json` and `finish.json`, assert backend/UI target/count/status parity, and
  assert required JS wiring strings exist.
- Manual UI checks: authenticated browser session, responsive desktop/mobile inspection, source
  mode/topic mode behavior, count stepper behavior, submit-pending timeout behavior, and no layout
  overlap.

Focused commands:

```bash
uv run --extra dev python -m pytest tests/web/test_submit.py tests/web/test_dispatch.py tests/web/test_index_contract.py -q
uv run --extra dev python -m pytest tests/web -m "not integration" -q
uv run --extra dev ruff check web/ tests/web/
```

Full gates:

```bash
uv run --extra dev ruff check tests/
uv run --extra dev python -m pytest tests/ -q
uv build
docker build -t reel-af-ui:verify web
```

## Behavior 1: Composite Count Is A Typed Job Setting

### Test Specification

Given a composite URL or file submission includes `count`, when the web backend canonicalizes the
submission, then it stores and dispatches an integer count within `1..12`.

Edge cases:

- Missing `count` defaults to `1`.
- String digits such as `"3"` normalize to `3`.
- `0`, negative, non-numeric, fractional, boolean, and values above `12` return `400` with code
  `invalid_count`.
- Topic submissions do not accept or dispatch `count`.
- `CFG.ui` count bounds match backend count constants.

### Red: Write Failing Tests

File: `tests/web/test_submit.py`

- Add `import pytest`.
- Add tests for:
  - Composite URL `count=3` dispatches and persists canonical params.
  - Composite file `count=2` survives presign and raw `source` is dropped.
  - Missing composite URL count defaults to `1`.
  - Missing composite file count defaults to `1`.
  - Numeric string count normalizes to an integer.
  - Invalid counts return `400`, JSON code `invalid_count`, no row, no CP.
  - Topic with `count` returns `400`, JSON code `unsupported_input_field`, no row, no CP.

Representative assertions:

```python
def test_composite_url_count_is_forwarded_as_integer():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_count"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={"input": {"url": "https://youtube.com/watch?v=abc",
                        "preset": "middle-third-dynamic", "count": 3}},
    )

    assert resp.status_code == 202
    _target, dispatched = cp.dispatch_calls[0]
    assert dispatched == {"input": {"url": "https://youtube.com/watch?v=abc",
                                    "preset": "middle-third-dynamic", "count": 3}}
    _ctx, submission, *_ = repo.inserted[0]
    assert submission.params == {
        "target": "reel-af.reel_composite_to_reel",
        "source_mode": "url",
        "preset": "middle-third-dynamic",
        "count": 3,
    }
```

```python
@pytest.mark.parametrize("bad_count", [0, -1, "two", "1.5", 1.5, True, 13])
def test_composite_rejects_invalid_count_before_row_or_cp(bad_count):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={"input": {"url": "https://youtube.com/watch?v=abc",
                        "preset": "middle-third-dynamic", "count": bad_count}},
    )

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_count"
    assert repo.inserted == []
    assert cp.dispatch_calls == []
```

File: `tests/web/test_index_contract.py`

```python
def test_ui_count_bounds_match_backend_constants():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    from reel_jobs import COMPOSITE_COUNT_DEFAULT, COMPOSITE_COUNT_MAX, COMPOSITE_COUNT_MIN

    assert cfg["ui"]["countDefault"] == COMPOSITE_COUNT_DEFAULT
    assert cfg["ui"]["countMin"] == COMPOSITE_COUNT_MIN
    assert cfg["ui"]["countMax"] == COMPOSITE_COUNT_MAX
```

### Green: Minimal Implementation

File: `web/reel_jobs.py`

- Add named constants near target constants:
  - `COMPOSITE_COUNT_DEFAULT = 1`
  - `COMPOSITE_COUNT_MIN = 1`
  - `COMPOSITE_COUNT_MAX = 12`
- Add `_parse_composite_count(raw_input: dict) -> int`.
- Accept missing `count` as default.
- Accept `int` except `bool`.
- Accept `str` only after `strip()` and only if all characters are decimal digits.
- Reject all invalid values with `BadRequest(..., code="invalid_count")`.
- In the composite branch, put normalized `count` into both `params` and `cp_input`.
- In the topic branch, reject `count` via target-specific allowed-key validation before topic
  canonicalization.

### Refactor

- Keep count parsing pure and unit-testable.
- Keep count constants near target constants in `web/reel_jobs.py`.
- Avoid coupling `build_submission()` to Flask or repo objects.
- Normalize `topic`, `preset`, `url`, and `source` into locals before validation predicates.

### Success Criteria

Automated:

- [x] Red fails for missing/invalid count support:
  `uv run --extra dev python -m pytest tests/web/test_submit.py -k count -q`
- [x] Green passes:
  `uv run --extra dev python -m pytest tests/web/test_submit.py -k count -q`
- [x] Existing submit tests still pass:
  `uv run --extra dev python -m pytest tests/web/test_submit.py -q`

Manual:

- [ ] Selecting a composite preset shows count default `1`.
- [ ] Increment/decrement changes the displayed count without resizing the panel.
- [ ] Topic mode hides or disables count.

## Behavior 2: Submit Boundary Rejects Unsupported Config Fields

### Test Specification

Given a browser or attacker submits non-contract fields, when the backend validates input, then it
rejects them before DB insert, file presign, or control-plane dispatch as applicable.

Allowed keys:

- All targets: `client_request_id` metadata only.
- Topic: `topic`.
- Composite URL: `url`, optional legacy duplicate `source` if equal to normalized `url`, `preset`,
  `count`.
- Composite file: `source`, `preset`, `count`.

Rejected examples:

- `out_dir`
- `finish_config`
- `canvas_w`
- `whisper_model`
- `encode_crf`
- `remotion_project_dir`
- arbitrary unknown keys

### Red: Write Failing Tests

File: `tests/web/test_submit.py`

- Add parametrized unsupported-field tests that assert status `400`, JSON code
  `unsupported_input_field`, no row, and no CP.
- Add file-mode unsupported-field and invalid-count tests that also assert
  `uploads.presign_calls == []`.
- Add URL-mode legacy duplicate tests:
  - Equal `source`/`url` accepted.
  - Mismatched `source`/`url` rejected with code `invalid_source`.
  - Accepted duplicate `source` is absent from `submission.params` and dispatched `cp_input`.
- Add auth precedence test: unauthenticated invalid body returns `401`, no row, no CP.
- Add exact canonical params assertions for topic, composite URL, and composite file.

Representative assertions:

```python
def test_composite_url_source_mismatch_is_invalid_source():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={"input": {"url": "https://youtube.com/watch?v=abc",
                        "source": "https://vimeo.com/other",
                        "preset": "middle-third-dynamic"}},
    )

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_source"
    assert repo.inserted == []
    assert cp.dispatch_calls == []
```

```python
def test_file_mode_unsupported_field_rejects_before_presign_row_or_cp():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    uploads = FakeUploadStore()
    deps = make_deps(identity=FakeIdentity(make_ctx("member")), reel_jobs=repo,
                     control_plane=cp, uploads=uploads)

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={"input": {"source": f"{ORG_ID}/clip.mp4",
                        "preset": "middle-third-dynamic",
                        "finish_config": {"caption_safe_y": 1200}}},
    )

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "unsupported_input_field"
    assert uploads.presign_calls == []
    assert repo.inserted == []
    assert cp.dispatch_calls == []
```

```python
def test_unauthenticated_invalid_body_returns_401_before_validation():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(
        identity=FakeIdentity(error=Unauthorized("no session")),
        reel_jobs=repo,
        control_plane=cp,
    )

    resp = _client(deps).post(
        COMPOSITE_URL,
        json={"input": {"url": "not-a-url", "finish_config": {}}},
    )

    assert resp.status_code == 401
    assert repo.inserted == []
    assert cp.dispatch_calls == []
```

### Green: Minimal Implementation

File: `web/reel_jobs.py`

- Add target-specific allowed-key sets:
  - `TOPIC_ALLOWED_INPUT_KEYS`
  - `COMPOSITE_URL_ALLOWED_INPUT_KEYS`
  - `COMPOSITE_FILE_ALLOWED_INPUT_KEYS`
  - all include `client_request_id`
- Validate unsupported keys after identity rejection and before `_clean_input()` or params creation.
- Determine composite source mode from normalized locals:
  - URL mode when `url` is provided.
  - File mode when `url` is absent and `source` is a non-empty handle.
- For composite URL mode, accept `source` only when normalized `source == url`; otherwise raise
  `BadRequest(..., code="invalid_source")`.
- For composite file mode, preserve `source` only in `submission.source_handle` and pre-presign
  `submission.cp_input`.
- Return exact canonical `params` described in "Submit Canonicalization".
- Use explicit `elif target == TARGET_COMPOSITE`; keep an unreachable `raise BadRequest(...,
  code="unsupported_target")` guard after known target branches.

File: `web/server.py`

- No route shape change should be required.
- `_resolve_cp_input()` continues to replace file handles with presigned URLs before DB insert.

### Refactor

- Split helpers by responsibility:
  - `_reject_unsupported_fields(raw_input, allowed)`
  - `_normalize_non_empty_string(value, code, message)`
  - `_legacy_source_matches_url(raw_source, normalized_url)`
  - `_canonical_params(target, source_mode, preset, count)`
- Keep error codes stable: `unsupported_input_field`, `invalid_count`, `invalid_source`.
- Preserve `_CP_STRIP` behavior for identity and idempotency metadata.

### Success Criteria

Automated:

- [x] Unsupported field tests fail before implementation:
  `uv run --extra dev python -m pytest tests/web/test_submit.py -k unsupported -q`
- [x] Source mismatch test fails before implementation:
  `uv run --extra dev python -m pytest tests/web/test_submit.py -k invalid_source -q`
- [x] Server tests pass after implementation:
  `uv run --extra dev python -m pytest tests/web/test_submit.py -q`
- [x] No auth/ownership regressions:
  `uv run --extra dev python -m pytest tests/web -m "not integration" -q`

Manual:

- [ ] Browser URL submissions still work after duplicate `source` cleanup.
- [ ] File submissions still upload, presign, and dispatch.

## Behavior 3: Idempotency Metadata Remains Metadata

### Test Specification

Given a caller provides idempotency metadata in either the header or `input.client_request_id`, when
the server handles submit, then the header has precedence, the body fallback dedupes when no header
is present, and the metadata never reaches `submission.params` or CP input.

### Red: Write Failing Tests

File: `tests/web/test_dispatch.py`

```python
def test_input_client_request_id_fallback_dedupes_without_header():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_body_key"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)
    client = _client(deps)
    body = {"input": {"topic": "black holes", "client_request_id": "BODY-K"}}

    first = client.post(TOPIC_URL, json=body)
    second = client.post(TOPIC_URL, json=body)

    assert first.status_code == 202 and second.status_code == 202
    assert len(cp.dispatch_calls) == 1
    _ctx, submission, _job, _now, crid = repo.inserted[0]
    assert crid == "BODY-K"
    assert "client_request_id" not in submission.params
    assert "client_request_id" not in cp.dispatch_calls[0][1]["input"]
```

```python
def test_idempotency_header_precedes_input_client_request_id():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_header_key"}, {}))
    deps = make_deps(identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(
        TOPIC_URL,
        headers={"Idempotency-Key": "HEADER-K"},
        json={"input": {"topic": "black holes", "client_request_id": "BODY-K"}},
    )

    assert resp.status_code == 202
    _ctx, submission, _job, _now, crid = repo.inserted[0]
    assert crid == "HEADER-K"
    assert "client_request_id" not in submission.params
    assert "client_request_id" not in cp.dispatch_calls[0][1]["input"]
```

### Green: Minimal Implementation

File: `web/reel_jobs.py`

- Include `client_request_id` in every target-specific allowed-key set.
- Keep `_CP_STRIP = FORBIDDEN_IDENTITY_FIELDS | {"client_request_id"}`.
- Build canonical params from normalized values rather than from `_clean_input(raw_input)` so
  metadata cannot leak into persistence.

File: `web/server.py`

- Preserve current `_client_request_id()` precedence:
  1. `Idempotency-Key` header
  2. `input.client_request_id`
  3. generated UUID hex

### Success Criteria

Automated:

- [x] Idempotency tests pass:
  `uv run --extra dev python -m pytest tests/web/test_dispatch.py -k client_request_id -q`
- [x] Existing dispatch tests still pass:
  `uv run --extra dev python -m pytest tests/web/test_dispatch.py -q`

## Behavior 4: UI Renders Safe Preset Metadata

### Test Specification

Given the UI config contains composite presets, when the page renders the Format panel, then each
composite preset exposes safe descriptive metadata that exactly matches `presets.json` and no
operator/local path fields.

Displayed fields:

- `description`
- `reelSeconds`
- `canvas.w`, `canvas.h`
- `overlay`
- `remotionComposition`
- `overlayAccent`
- Middle-third phrase settings: max words, max duration, gap, hold, uppercase, vertical anchor,
  captions-burned-source flag.
- Lower-third settings: duration.
- Image settings: count, every, placement, zoom.

Fields not to display:

- `remotion_project_dir`
- `lower_third_project_dir`
- `out_dir`
- `whisper_*`
- `encode_*`

### Red: Write Failing Tests

File: `tests/web/test_index_contract.py`

- Add `_config(html)` helper with `json` and `re` imports.
- Add a helper that maps `presets.json` snake_case fields to the UI details schema.
- Test exact per-preset static mapping.
- Test unsafe strings are absent from serialized config.
- Test the renderer uses a named detail function and `textContent` for metadata rows.

Representative assertions:

```python
def test_composite_preset_details_match_render_config_exactly():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)
    source = json.loads(
        (INDEX_HTML.parents[1] / "src/reel_af/render/config/presets.json").read_text(
            encoding="utf-8"
        )
    )

    by_id = {p["id"]: p for p in cfg["presets"]}
    for preset_id, raw in source.items():
        assert by_id[preset_id]["details"] == _expected_preset_details(raw)
```

```python
def test_preset_renderer_uses_text_content_for_detail_rows():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "function renderPresetDetails" in html
    details_fn = html.split("function renderPresetDetails", 1)[1].split("function", 1)[0]
    assert ".textContent" in details_fn
    assert ".innerHTML" not in details_fn
```

### Green: Minimal Implementation

File: `web/index.html`

- Expand `CFG.presets[]` for composite presets with `details` using the schema in "UI Config
  Mapping".
- Add `renderPresetDetails(p)` and call it from `renderPresets()`.
- Construct new metadata rows with `document.createElement`, `dataset.detail`, and `textContent`.
- Keep topic preset without composite details or render a topic-safe short description only.

### Refactor

- Keep all display labels/copy in the `#config` block.
- Keep renderer functions small:
  - `renderPresets()`
  - `renderPresetSummary(p)`
  - `renderPresetDetails(p)`
  - `appendDetailRow(parent, key, label, value)`
- Do not introduce a new endpoint until the web image can consume root render config reliably.

### Success Criteria

Automated:

- [x] Config contract test fails before metadata exists:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -k preset -q`
- [x] Config contract test passes after implementation:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -q`

Manual:

- [ ] Format cards show duration, dimensions, composition, and overlay details.
- [ ] Cards remain scannable on mobile and desktop.
- [ ] No local path/provider/encode setting appears in the browser.

## Behavior 5: Count Control Is Composite-Only And Included In Browser Input

### Test Specification

Given a composite preset is selected, when the user adjusts the count control and rolls, then
`buildInput()` includes normalized `count`. Given topic is selected, `buildInput()` returns only
`{topic}`.

Preset changes preserve the current count after clamping to `CFG.ui.countMin..countMax`; they do not
reset count to default. This avoids accidental loss when a user compares composite presets. Topic mode
hides/disables the control but does not mutate the stored count.

Because there is no JS harness, automated coverage is a string/config contract. Manual verification
is required for actual click behavior.

### Red: Write Failing Tests

File: `tests/web/test_index_contract.py`

```python
def test_count_control_contract_exists_and_is_composite_only():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    assert cfg["ui"]["countDefault"] == 1
    assert cfg["ui"]["countMin"] == 1
    assert cfg["ui"]["countMax"] == 12
    assert 'id="countInput"' in html
    assert "function selectedCount" in html
    assert "function setCount" in html
    assert "function renderJobSettings" in html
    assert "KIND_TOPIC" in html
    assert "state.preset.kind === KIND_TOPIC" in html
    assert "count: selectedCount()" in html
```

```python
def test_build_input_does_not_send_legacy_url_source_or_finish_defaults():
    html = INDEX_HTML.read_text(encoding="utf-8")

    build_input = html.split("function buildInput", 1)[1].split("function goToLogin", 1)[0]
    assert "source: u" not in build_input
    assert "finishDefaults" not in build_input
    assert "finish_config" not in build_input
    assert "canvas_w" not in build_input
    assert "caption_safe_y" not in build_input
```

### Green: Minimal Implementation

File: `web/index.html`

- Add named constants in JS:
  - `MODE_FILE`
  - `MODE_URL`
  - `KIND_TOPIC`
  - `TARGET_TOPIC`
  - `TARGET_COMPOSITE`
- Extend state with `count: CFG.ui.countDefault`.
- Add count controls in a "Job Settings" panel:
  - decrement button
  - numeric input
  - increment button
- Add `selectedCount()` and `setCount(next)`.
- Add `renderJobSettings()` or equivalent to hide/disable count for topic mode.
- Update `buildInput(handle)`:
  - Topic: `{ topic }`
  - File composite: `{ source: handle, preset: state.preset.id, count: selectedCount() }`
  - URL composite: `{ url: u, preset: state.preset.id, count: selectedCount() }`

### Refactor

- Do not use ad hoc number literals; read count min/max/default from `CFG.ui`.
- Keep count layout dimensions stable so button labels cannot resize the panel.
- Preserve idempotency flow in `roll()` except for bounded pending retries covered below.

### Success Criteria

Automated:

- [x] HTML contract fails before count controls exist:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -k count -q`
- [x] HTML contract passes after implementation:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -q`

Manual:

- [ ] Increment/decrement clamps to `1..12`.
- [ ] Typing an invalid value resets/clamps before submit.
- [ ] Switching presets preserves the clamped count.
- [ ] Topic mode hides/disables the count controls.
- [ ] URL roll submits without duplicate `source`.

## Behavior 6: Global Finish Defaults Are Read-Only Context

### Test Specification

Given the UI loads, when the user views the configuration panels, then global finish-stage geometry
and advanced defaults appear as disabled/read-only context and are not included in submitted input.

Read-only fields:

- Canvas: `1080 x 1920`
- Center X: `540`
- Caption anchor: `caption_safe_y = 1344`
- Banner anchor: `divider_y = 772`
- Image region: `x=0, y=800, w=1080, h=1120`
- Caption grouping: `4 words / 1.8s / 0.35s gap`
- Images: `3`, `full`, `2.0-3.0s`, edge guard `2.0s`
- Encode context: `CRF 19`, `fast`

These are display-only. No `finish_config` or per-field override reaches `buildInput()`.

### Red: Write Failing Tests

File: `tests/web/test_index_contract.py`

- Add a helper that maps `finish.json` snake_case fields to the UI finish schema.
- Test exact static mapping for every displayed default.
- Test the panel declares global scope and read-only state.
- Test finish defaults are not referenced in `buildInput()`.

Representative assertions:

```python
def test_finish_defaults_match_render_config_exactly():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)
    source = json.loads(
        (INDEX_HTML.parents[1] / "src/reel_af/render/config/finish.json").read_text(
            encoding="utf-8"
        )
    )

    assert cfg["finishDefaults"] == _expected_finish_defaults(source)
    assert cfg["finishDefaults"]["readOnly"] is True
    assert cfg["finishDefaults"]["scope"] == "global_finish_stage_defaults"
```

```python
def test_finish_defaults_are_not_submitted():
    html = INDEX_HTML.read_text(encoding="utf-8")

    build_input = html.split("function buildInput", 1)[1].split("function goToLogin", 1)[0]
    assert "finishDefaults" not in build_input
    assert "finish_config" not in build_input
    assert "canvas_w" not in build_input
    assert "caption_safe_y" not in build_input
```

### Green: Minimal Implementation

File: `web/index.html`

- Add `CFG.finishDefaults` using the schema in "UI Config Mapping".
- Add a read-only "Global Polished Defaults" panel or similarly scoped label.
- Add `renderFinishDefaults()` and call it during boot.
- Render values as text-only rows or disabled/read-only visual elements.
- Do not bind these fields to form inputs that can alter `buildInput()`.

### Refactor

- Keep Remotion preset positioning and global finish-stage positioning visually separate:
  - Preset details show `overlay_vertical_anchor` or lower-third duration/image behavior.
  - Finish defaults show caption/banner/image/encode defaults from `finish.json`.
- Avoid "advanced JSON" editing.

### Success Criteria

Automated:

- [x] Finish-default contract fails before config exists:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -k finish_defaults -q`
- [x] Finish-default contract passes after implementation:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -q`

Manual:

- [ ] Positioning/defaults panel is visible and clearly read-only.
- [ ] It is labeled as global polished-output defaults, not selected-preset geometry.
- [ ] Controls do not imply editable per-job overrides.
- [ ] No read-only values appear in the network submit body.

## Behavior 7: Browser Async Promises Are Bounded

### Test Specification

Given submit returns `409 idempotent_request_pending`, when the browser retries, then it reuses the
same idempotency key but stops after a bounded submit-pending timeout.

Given poll receives transient CP backpressure or outage via the server (`429` or `5xx`), when a
`Retry-After` header is present, then the browser sleeps that amount before continuing. `401` still
redirects to login. Non-transient `4xx` fails with a useful error. Poll timeout remains
observation-only and does not cancel CP work.

### Red: Write Failing Tests

File: `tests/web/test_index_contract.py`

```python
def test_execute_pending_retries_are_bounded():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    assert cfg["api"]["submitPendingTimeoutMs"] > 0
    execute_fn = html.split("async function execute", 1)[1].split("async function poll", 1)[0]
    assert "submitPendingStartedAt" in execute_fn
    assert "CFG.api.submitPendingTimeoutMs" in execute_fn
    assert "Idempotency-Key" in execute_fn
    assert "CFG.ui.idempotentPendingCode" in execute_fn
```

```python
def test_poll_handles_retry_after_for_transient_non_ok_statuses():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "function retryAfterMs" in html
    poll_fn = html.split("async function poll", 1)[1].split("function mapStatus", 1)[0]
    assert "pollResponse.status === STATUS_UNAUTHORIZED" in poll_fn
    assert "isTransientPollStatus" in poll_fn
    assert "Retry-After" in poll_fn
```

### Green: Minimal Implementation

File: `web/index.html`

- Add `CFG.api.submitPendingTimeoutMs`, for example `120000`.
- Add named JS constants:
  - `STATUS_UNAUTHORIZED = 401`
  - `STATUS_CONFLICT = 409`
  - `MILLISECONDS_PER_SECOND = 1000`
  - `NO_DOWNLOAD_HREF = "#"`
- Add helpers:
  - `retryAfterMs(response)`
  - `isTransientPollStatus(status)`
- In `execute()`, record `submitPendingStartedAt` before the retry loop. If elapsed time exceeds
  `CFG.api.submitPendingTimeoutMs`, throw a submit-pending timeout error.
- Continue to reuse the same idempotency key across pending retries.
- In `poll()`, retain the overall `CFG.api.timeoutMs` deadline. For `429` and `5xx`, log, sleep
  `retryAfterMs(response)`, and continue. For `401`, redirect to login. For other non-OK statuses,
  throw an error containing the truncated response body.

### Refactor

- Keep cancellation explicitly out of scope in code comments and user-facing behavior.
- Do not add CP cancel calls or durable reconciliation from browser timeout.
- Replace touched sentinel/status literals with named constants.

### Success Criteria

Automated:

- [x] Async contract checks fail before bounded retry work:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -k "execute_pending or poll_handles" -q`
- [x] HTML contract tests pass after implementation:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -q`

Manual:

- [ ] A simulated pending submit eventually reports timeout instead of retrying forever.
- [ ] Browser logs clearly say the timeout does not cancel already accepted CP work.
- [ ] Transient poll `Retry-After` delays do not create tight retry loops.

## Behavior 8: Backend/UI Contract Parity Stays Explicit

### Test Specification

Given target, count, and status aliases are duplicated between backend and static UI config, when
tests parse the UI config, then visible targets and count constants match backend constants, and UI
status aliases are understood by backend normalization.

### Red: Write Failing Tests

File: `tests/web/test_index_contract.py`

```python
def test_visible_preset_targets_match_backend_allowlist():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    from reel_jobs import ALLOWLISTED_TARGETS, TARGET_COMPOSITE, TARGET_TOPIC

    targets = {p["target"] for p in cfg["presets"]}
    assert targets == {TARGET_COMPOSITE, TARGET_TOPIC}
    assert targets == set(ALLOWLISTED_TARGETS)
```

```python
def test_ui_status_aliases_are_known_by_backend_normalizer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    from reel_jobs import normalize_reel_status

    for status in cfg["ui"]["statusStageByExecutionStatus"]:
        assert normalize_reel_status(status) in {"queued", "producing", "succeeded"}
    for status in cfg["ui"]["terminalFailureStatuses"]:
        assert normalize_reel_status(status) in {"failed", "cancelled"}
```

### Green: Minimal Implementation

File: `web/index.html`

- Add target constants in `CFG.targets` or named JS constants derived from config.
- Keep `CFG.presets[].target` values equal to backend target constants.
- Keep status aliases centralized in `CFG.ui.statusStageByExecutionStatus` and
  `CFG.ui.terminalFailureStatuses`.

File: `web/reel_jobs.py`

- Export count constants and keep target constants as the backend source used by tests.

### Success Criteria

Automated:

- [x] Parity tests pass:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -k "parity or visible_preset or status_aliases" -q`

## Behavior 9: UI Layout Remains Usable Across Modes

### Test Specification

Given the page is viewed on desktop and mobile widths, when the user switches among file, URL, and
topic presets, then controls remain visible, labels do not overlap, and the primary workflow remains
source -> format -> job settings -> roll.

### Red: Define Manual Verification Before Styling

Manual failing checks before implementation:

- Format card lacks detail rows.
- Count control absent.
- Global finish/defaults panel absent.
- Topic mode still shows irrelevant composite job controls.
- Submit-pending retry has no browser deadline.

### Green: Minimal Implementation

File: `web/index.html`

- Add panels inside the existing right-column `controls`.
- Reuse current film-house tokens (`--film`, `--film-2`, `--print`, `--grease`, `--line`).
- Use compact repeated detail rows instead of nested cards.
- Maintain stable dimensions for count buttons/input and detail rows.
- If the right column becomes too long, use concise collapsible sections for read-only defaults.

### Refactor

- Keep the first viewport focused on the actual tool, not marketing copy.
- Avoid new nested cards.
- Verify mobile text does not overlap or overflow buttons/detail rows.

### Success Criteria

Automated:

- [x] HTML contract tests pass:
  `uv run --extra dev python -m pytest tests/web/test_index_contract.py -q`

Manual:

- [ ] Desktop: 1280px wide, no text overlap, roll button visible after normal scroll.
- [ ] Mobile: 390px wide, panels stack cleanly, count controls fit.
- [ ] Topic mode hides/disables composite-only settings.
- [ ] File mode and URL mode still show expected source controls.
- [ ] Result/download display is unchanged.

## CodeCleanup Gates For Implementation

Apply these during implementation, before final verification:

- `web/reel_jobs.py`
  - Validate allowed keys before `_clean_input()` or params creation.
  - Use explicit `if target == TARGET_TOPIC` / `elif target == TARGET_COMPOSITE` / unsupported guard.
  - Normalize `topic`, `preset`, `url`, and `source` locals before validation predicates.
  - Keep count parser and source mismatch helpers pure.
- `web/index.html`
  - Add named constants for source modes, topic kind, target strings, HTTP statuses,
    milliseconds conversion, and no-download sentinel.
  - Do not deepen compact multi-effect event branches; extract helpers where touched.
  - New config-sourced display rows use DOM construction and `textContent`, not `innerHTML`.
  - Preserve existing idempotency behavior while adding a submit-pending deadline.
- Tests
  - Add `import pytest` before using parametrization in `tests/web/test_submit.py`.
  - Assert JSON `code` values for typed `400` cases.
  - Assert no side effects: no row, no CP, and for file-mode validation failures no presign.

## Integration And Regression Testing

Run after all behaviors are green:

```bash
uv run --extra dev python -m pytest tests/web/test_submit.py tests/web/test_dispatch.py tests/web/test_index_contract.py -q
uv run --extra dev python -m pytest tests/web -m "not integration" -q
uv run --extra dev ruff check web/ tests/web/
```

Optional broader confidence:

```bash
uv run --extra dev python -m pytest tests/test_finish_config.py tests/test_reels_cli.py -q
uv run --extra dev python -m pytest tests/ -q
docker build -t reel-af-ui:verify web
```

## Implementation Order

1. Add failing server tests for count, canonical params, unsupported fields, source mismatch,
   idempotency metadata, auth precedence, and file-mode no-presign validation failures.
2. Implement backend canonicalization in `web/reel_jobs.py`, keeping `web/server.py` route shapes
   unchanged.
3. Add failing HTML/config contract tests for preset details, count controls, finish defaults,
   async retry bounds, target/count/status parity, and no-submitted-finish-defaults.
4. Expand `web/index.html` config and render the new panels/controls with DOM/textContent helpers.
5. Update browser async helpers for bounded submit-pending retries and transient poll `Retry-After`.
6. Run focused web tests and Ruff.
7. Manually verify layout and submit payloads in a browser.

## References

- Review: `thoughts/searchable/shared/plans/2026-07-11-14-52-tdd-reels-af-ui-configuration-options-REVIEW.md`
- Research: `thoughts/searchable/shared/research/2026-07-11-12-59-reels-af-ui-configuration-options.md`
- Current UI config and browser state: `web/index.html:12-64`, `web/index.html:389`
- Current submit builder: `web/index.html:515-520`
- Current roll/execute/poll: `web/index.html:527-639`
- Backend submission validation: `web/reel_jobs.py:105-175`
- Idempotency fallback: `web/server.py:67-74`
- File handle presign/canonical CP input: `web/server.py:140-153`
- Composite reasoner count support: `src/reel_af/app.py:698-727`
- Preset config source: `src/reel_af/render/config/presets.json:1-32`
- Finish defaults/schema: `src/reel_af/render/config/finish.json:1-97`,
  `src/reel_af/render/finish_config.py:77-168`
- Browser contract precedent: `tests/web/test_index_contract.py:10-17`
- Submit test precedent: `tests/web/test_submit.py:29-187`
- Idempotency test precedent: `tests/web/test_dispatch.py:28-59`
