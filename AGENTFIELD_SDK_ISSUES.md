# AgentField SDK issues surfaced by reel-af

Filed by reel-af during a real workload (URL → vertical viral reel).
Every model call in this project now goes through the SDK. The items below
are bugs / gaps that forced *consumer-side* workarounds; they do not
involve bypassing the SDK transport.

Tested against `agentfield` installed in `.venv` on 2026-05-24.

---

## 1. `OpenRouterProvider.generate_audio` rejects `format='wav'` because it hardcodes `stream=true`

**File**: `agentfield/media_providers.py` ≈ line 1229

```python
payload = {
    "model": send_model,
    "messages": [...],
    "modalities": ["text", "audio"],
    "audio": {"voice": voice, "format": audio_format},
    "stream": True,                                       # <— hardcoded
}
```

The SDK accepts `format` from `{"wav", "mp3", "flac", "opus", "pcm16"}` but
OpenRouter rejects anything other than `pcm16` when `stream=true`:

```
RuntimeError: OpenRouter audio request failed (400):
{"error":{"message":"Unsupported value: 'audio.format' does not support
'wav' when stream=true. Supported values are: 'pcm16'."}}
```

**Repro**: call `generate_audio(text=..., format='wav')` against
`openrouter/openai/gpt-audio-mini`.

**Suggested fix**: either
- auto-switch `stream=false` when `format != 'pcm16'`, or
- if a format other than pcm16 is requested, wrap the streamed pcm16 in
  the requested container before returning.

**Consumer workaround used here**: request `format='pcm16'` and add a WAV
header locally with stdlib `wave` (no transport bypass; the SDK still
performs the actual API call).

---

## 2. `OpenRouterProvider.generate_audio` provides no way to pass a system message

**File**: `agentfield/media_providers.py` ≈ line 1226

```python
"messages": [{"role": "user", "content": text}],
```

The `text` arg becomes the sole user message; there is no way (kwargs are
not merged into `payload`) to supply a system role.

This matters for chat-completions audio models like `gpt-audio-mini`:
without a system instruction telling the model "you are a narrator, read
verbatim", the model can RESPOND to the user message ("Sure, I can read
this…") instead of just reading it.

**Suggested fix**: accept either
- `system: Optional[str]` parameter, or
- `messages: Optional[list[dict]]` to fully override the payload.

**Consumer workaround used here**: prepend narrator directives to the user
text inline (see `_wrap_as_narration` in `tts_continuous.py`). Imperfect
because the directive lives inside the literal narration the model is
supposed to read; we offset with a visible divider so the model recognises
which segment is the script.

---

## 3. `OpenRouterProvider.generate_image` 404s for *every* request when `image_config={"aspect_ratio": "9:16"}` is passed

**File**: `agentfield/vision.py` `generate_image_openrouter`

Reproducible 404 from OpenRouter:

```
litellm.NotFoundError: NotFoundError: OpenrouterException -
{"error":{"message":"No endpoints found that support the requested
output modalities: image, text","code":404}}
```

Without `image_config`, the same call succeeds. OpenRouter's current
provider matrix for `google/gemini-2.5-flash-image` apparently has no
upstream replica that exposes the `image_config.aspect_ratio` parameter,
so the routing returns empty.

**Suggested fix**: when `image_config` is non-empty and OpenRouter returns
this specific 404, either
- transparently retry without `image_config` and surface a warning, or
- raise a clearer error: "no upstream provider for model X accepts
  image_config; drop the parameter or pick a different model".

**Consumer workaround used here**: drop the param entirely; ask for
vertical composition in the prompt and center-crop the square output to
9:16 ourselves (`_crop_to_9x16` in `video_gen.py`).

---

## 4. `ImageOutput.save()` fails on data URLs returned by Gemini image models

**File**: `agentfield/multimodal_response.py` ≈ line 99

```python
elif self.url:
    response = requests.get(self.url)         # <— bombs on `data:` URLs
    ...
```

`vision.generate_image_openrouter` sets `ImageOutput.url` to whatever the
OpenRouter response carries. For Gemini, that is a
`data:image/png;base64,...` URL — which `requests.get()` cannot
dereference (no connection adapter for the `data:` scheme).

```
InvalidSchema: No connection adapters were found for
'data:image/png;base64,iVBORw0KGgo...'
```

**Suggested fix**: in either
- `generate_image_openrouter` — when the response's `url` is a `data:`
  URL, base64-decode it once and populate `b64_json` instead, leaving
  `url=None`. Then the existing `save()` branch handles it.
- `ImageOutput.save()` — detect `data:` URLs and decode locally.

**Consumer workaround used here**: a `_save_image_output()` helper that
sniffs `data:` URLs and decodes them itself before falling back to the
SDK's `save()` for real HTTP URLs (see `video_gen.py`).

---

## 5. Intermittent provider-routing 404 on `google/gemini-2.5-flash-image` under concurrency

Even without `image_config`, occasional requests 404 with the same
"No endpoints found…" message. CLI tests (sequential and 7-way concurrent)
succeed 100%, but in long pipeline runs we see ~1-3% of frame generations
fail with that error.

**Likely cause**: OpenRouter routing momentarily lands on an upstream
replica that doesn't expose image modality. Surfaces as a litellm
`NotFoundError`.

**Suggested fix (SDK side)**: retry on `NotFoundError` from
`generate_image_openrouter` when the message matches the
"No endpoints found that support the requested output modalities" shape.
3 retries with exponential backoff would mask the routing blip.

**Consumer workaround used here**: 4-retry loop with exponential backoff
around `provider.generate_image(...)` in `video_gen._gen_first_frame`,
plus a placeholder-frame fallback so a permanently-failing shot doesn't
take down the whole reel.

---

## 6. `OpenRouterProvider.generate_video` returns HTTP 401 on every download

**File**: `agentfield/media_providers.py` ≈ lines 1018-1041

```python
unsigned_urls = poll_data.get("unsigned_urls", [])
...
video_url = unsigned_urls[0]
_assert_safe_download_url(video_url)

# Download without auth headers — video_url is a public CDN URL
async with session.get(video_url) as resp:           # <— no headers
    if resp.status != 200:
        raise RuntimeError(f"Failed to download video from {video_url}: HTTP {resp.status}")
```

The comment is wrong. OpenRouter's `unsigned_urls` are of the form

```
https://openrouter.ai/api/v1/videos/<job_id>/content?index=0
```

— that's an API endpoint, not a CDN. It requires the `Authorization`
header. Every call to `generate_video` 401s on the download step and the
SDK raises before returning the video bytes. End result: 100% video-gen
failure rate when using `generate_video`.

Confirmed by polling OpenRouter directly — the completed-job response only
ever contains `unsigned_urls`, never `signed_urls`:

```json
{
  "id": "IVF9g3DZu84cpX6rAWXX",
  "status": "completed",
  "unsigned_urls": ["https://openrouter.ai/api/v1/videos/IVF.../content?index=0"],
  "usage": {"cost": 0.2, "is_byok": false}
}
```

**Suggested fix**: pass `headers` to the download `session.get(video_url, headers=headers)`. One-line change.

**Consumer workaround used here**: runtime monkey-patch in
`src/reel_af/sdk_patches.py` that re-defines
`OpenRouterProvider.generate_video` with the auth header included on the
download. Same SDK call path otherwise. Remove once upstream is fixed.

---

## 7. `app.ai()` has no per-call timeout override

**File**: `agentfield/agent_ai.py` ≈ line 257 (`async def ai`), 534-537

The `ai()` signature accepts `temperature`, `max_tokens`, `stream`, `tools`,
etc., but NOT a `timeout` parameter. The effective timeout is read from
`agent.async_config.llm_call_timeout` (default 120 s) and used for both
the inner `litellm.acompletion` call and a `2× safety net` wrap:

```python
# line 595
return await asyncio.wait_for(
    litellm_module.acompletion(**params),
    timeout=timeout * 2,
)
```

To bump it, you must bump the whole agent's config (via
`AGENTFIELD_ASYNC_LLM_CALL_TIMEOUT` env var). That affects every
LLM call the agent makes.

This bites pipelines that mix fast and slow calls — e.g., reel-af's
distiller and scene_breaker want ~60 s, but compose_script on a long
arxiv preprint needs 10+ minutes. Today we have to set the agent-wide
timeout to the slowest case.

**Suggested fix**: accept `timeout: Optional[float] = None` in
`ai()` (and friends) and prefer it over `async_config.llm_call_timeout`
when set. One-line addition.

**Consumer workaround used here**: bump
`AGENTFIELD_ASYNC_LLM_CALL_TIMEOUT` to the slowest expected call.

---

## Confirmed correct: SDK's async patterns

For completeness — the SDK gets video generation right:

| Operation | OpenRouter shape | SDK pattern | Status |
| --- | --- | --- | --- |
| Video gen (Veo) | Async: POST `/videos` (202) → poll `/videos/{id}` → download | SDK polls until `status=completed`, then GETs `unsigned_urls[0]` | Correct shape (the download-auth bug in #6 is separate) |
| Image gen (Gemini) | Sync: chat completions returns data URL inline | SDK uses single `chat/completions` call with `modalities=["image","text"]` | Correct |
| Audio gen (TTS) | Sync streaming: chat completions SSE | SDK uses `stream=true` and accumulates audio deltas | Correct (the stream=true / format='wav' clash in #1 is separate) |
| Chat completions | Sync only — no async submit-and-poll from OpenRouter | SDK uses `litellm.acompletion` | Correct — OpenRouter doesn't expose an async chat job queue, nothing to wire up |

So the SDK's async-job-queue handling is in good shape where the provider
supports it. The "timeouts on long chat calls" pain is a model-speed
issue (and a missing per-call timeout override, #7 above) — not an
async-pattern gap.

---

## What we are NOT bypassing

All model calls go through the SDK:

| Call             | SDK entry point                                         |
| ---------------- | ------------------------------------------------------- |
| Chat / structured | `app.ai(...)` (AgentField agent)                       |
| Image            | `OpenRouterProvider().generate_image(...)`              |
| Video (Veo i2v)  | `OpenRouterProvider().generate_video(...)`              |
| Audio (TTS)      | `OpenRouterProvider().generate_audio(...)`              |

Non-model HTTP (article fetching in `navigator.py`) intentionally uses
`aiohttp` directly — it is not a provider call and has no SDK equivalent.
