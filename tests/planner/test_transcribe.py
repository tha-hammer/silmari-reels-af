from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from reel_af.planner.config import AsrEntry
from reel_af.planner.transcribe import AsrError, transcribe_audio


@pytest.fixture
def tmp_audio(tmp_path: Path) -> Path:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    return audio


def native_entry() -> AsrEntry:
    return AsrEntry(
        model="openai/whisper-large-v3",
        word_ts="native",
        response_format="verbose_json",
        request_word_timestamps=True,
    )


def forced_entry() -> AsrEntry:
    return AsrEntry(
        model="openai/gpt-4o-mini-transcribe",
        word_ts="forced",
        response_format="json",
        request_word_timestamps=False,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_posts_multipart_and_normalizes_provider_words(tmp_audio: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/audio/transcriptions"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert request.headers["Content-Type"].startswith("multipart/form-data")
        body = request.content
        assert b'name="file"' in body
        assert b'name="model"' in body
        assert b"openai/whisper-large-v3" in body
        assert b"name=\"response_format\"" in body
        assert b"verbose_json" in body
        assert b'name="timestamp_granularities[]"' in body
        assert b"word" in body
        return httpx.Response(
            200,
            json={
                "text": "hello world",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.4},
                    {"word": "world", "start": 0.4, "end": 0.9, "confidence": 0.8},
                ],
            },
        )

    sidecar = await transcribe_audio(
        tmp_audio,
        entry=native_entry(),
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    assert [word.w for word in sidecar.words] == ["hello", "world"]
    assert sidecar.words[1].conf == 0.8
    assert sidecar.segments[0].text == "hello world"


async def test_forced_entry_does_not_request_unsupported_word_timestamp_fields(
    tmp_audio: Path,
):
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert b"verbose_json" not in body
        assert b"timestamp_granularities" not in body
        return httpx.Response(200, json={"text": "segment only", "duration": 1.25})

    sidecar = await transcribe_audio(
        tmp_audio,
        entry=forced_entry(),
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    assert sidecar.words == []
    assert sidecar.segments[0].text == "segment only"
    assert sidecar.segments[0].end_s == 1.25


async def test_retries_retryable_status_and_honors_retry_after(tmp_audio: Path):
    attempts: list[int] = []
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return httpx.Response(
            200,
            json={"text": "retry ok", "words": [{"word": "retry", "start": 0.0, "end": 0.5}]},
        )

    sidecar = await transcribe_audio(
        tmp_audio,
        entry=native_entry(),
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
        max_retries=1,
        sleep=sleep,
    )

    assert [word.w for word in sidecar.words] == ["retry"]
    assert len(attempts) == 2
    assert slept == [0.25]


async def test_terminal_status_is_not_retried_and_diagnostics_are_redacted(tmp_audio: Path):
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            401,
            headers={"X-Generation-Id": "gen-auth"},
            json={"error": {"message": "OPENROUTER_API_KEY sk-secret leaked upstream"}},
        )

    with pytest.raises(AsrError) as err:
        await transcribe_audio(
            tmp_audio,
            entry=native_entry(),
            api_key="sk-secret",
            transport=httpx.MockTransport(handler),
            max_retries=3,
            sleep=_no_sleep,
        )

    assert attempts == 1
    assert err.value.code == "asr_auth"
    assert err.value.status_code == 401
    assert err.value.generation_id == "gen-auth"
    assert "sk-secret" not in str(err.value)
    assert "OPENROUTER_API_KEY" not in str(err.value)


async def test_native_entry_requires_non_empty_word_timestamps(tmp_audio: Path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "segment only",
                "segments": [{"text": "segment only", "start": 0.0, "end": 1.0}],
            },
        )

    with pytest.raises(AsrError) as err:
        await transcribe_audio(
            tmp_audio,
            entry=native_entry(),
            api_key="sk-test",
            transport=httpx.MockTransport(handler),
            sleep=_no_sleep,
        )

    assert err.value.code == "asr_missing_word_timestamps"
