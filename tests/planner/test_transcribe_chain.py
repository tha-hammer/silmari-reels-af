from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

from reel_af.dsl.aligner import align
from reel_af.dsl.models import MATCH_QUALITY_FLOOR, WordsSidecar
from reel_af.planner.config import AsrEntry, PlannerConfig
from reel_af.planner.transcribe import (
    AsrError,
    build_transcriber,
    force_align_words,
    materialize_audio,
    transcribe_chain,
)

WORDS = WordsSidecar.model_validate(
    {
        "schema_version": "1",
        "words": [{"w": "local", "start": 0.0, "end": 0.4}],
        "segments": [],
    }
)


def asr_entry(model: str, word_ts: str) -> AsrEntry:
    return AsrEntry(
        model=model,
        word_ts=word_ts,
        response_format="verbose_json" if word_ts == "native" else "json",
        request_word_timestamps=word_ts == "native",
    )


@pytest.fixture
def tiny_audio(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * 16_000)
    return path


async def test_materialize_audio_cleans_up_on_cancellation(tmp_path: Path, monkeypatch):
    async def fake_download(_source: str, run_dir: Path) -> Path:
        audio = run_dir / "audio.wav"
        audio.write_bytes(b"RIFF....WAVEfmt ")
        return audio

    monkeypatch.setattr("reel_af.planner.transcribe._download_audio", fake_download)

    with pytest.raises(asyncio.CancelledError):
        async with materialize_audio("https://youtu.be/x", tmp_root=tmp_path) as audio:
            assert audio.path.exists()
            raise asyncio.CancelledError()

    assert not any(tmp_path.iterdir())


async def test_chain_order_caption_remote_local():
    order: list[str] = []

    def raise_transient() -> None:
        raise AsrError(code="asr_network", message="temporary", retryable=True)

    transcriber = build_transcriber(
        caption=lambda _source: order.append("caption") or None,
        remote=lambda _source: order.append("remote") or raise_transient(),
        local=lambda _source: order.append("local") or WORDS,
    )

    words = await transcriber("https://youtu.be/x")

    assert words.words[0].w == "local"
    assert order == ["caption", "remote", "local"]


async def test_forced_alignment_uses_real_audio_fixture(tiny_audio: Path):
    sidecar = await force_align_words(tiny_audio, "hello world", engine="whisperx")
    result = align("hello world", sidecar)

    assert result.kind == "aligned"
    assert result.quality >= MATCH_QUALITY_FLOOR
    assert sidecar.words[0].start == 0.0
    assert sidecar.words[-1].end == pytest.approx(1.0)


async def test_transcribe_chain_forced_entry_aligns_segment_only_remote(tiny_audio: Path):
    cfg = PlannerConfig(remote_asr_chain=[asr_entry("openai/gpt-4o-mini-transcribe", "forced")])
    calls: list[str] = []

    async def remote(_audio: Path, entry: AsrEntry) -> WordsSidecar:
        calls.append(entry.model)
        return WordsSidecar.model_validate(
            {
                "schema_version": "1",
                "words": [],
                "segments": [{"text": "hello world", "start_s": 0.0, "end_s": 1.0}],
            }
        )

    sidecar = await transcribe_chain(str(tiny_audio), cfg=cfg, remote=remote)
    result = align("hello world", sidecar)

    assert calls == ["openai/gpt-4o-mini-transcribe"]
    assert result.kind == "aligned"
    assert result.quality >= MATCH_QUALITY_FLOOR


async def test_transcribe_chain_uses_configured_fallback_order(tiny_audio: Path):
    cfg = PlannerConfig(
        remote_asr_chain=[
            asr_entry("openai/whisper-large-v3", "native"),
            asr_entry("openai/gpt-4o-mini-transcribe", "forced"),
        ]
    )
    calls: list[str] = []

    async def remote(_audio: Path, entry: AsrEntry) -> WordsSidecar:
        calls.append(entry.model)
        if entry.word_ts == "native":
            raise AsrError(code="asr_provider_unavailable", message="temporary", retryable=True)
        return WordsSidecar.model_validate(
            {
                "schema_version": "1",
                "words": [],
                "segments": [{"text": "hello world", "start_s": 0.0, "end_s": 1.0}],
            }
        )

    sidecar = await transcribe_chain(str(tiny_audio), cfg=cfg, remote=remote)

    assert [word.w for word in sidecar.words] == ["hello", "world"]
    assert calls == ["openai/whisper-large-v3", "openai/gpt-4o-mini-transcribe"]
