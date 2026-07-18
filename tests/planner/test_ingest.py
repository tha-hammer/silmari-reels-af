from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from reel_af.dsl.models import WordsSidecar
from reel_af.planner.ingest import (
    sidecar_from_vtt,
    sidecar_from_whisper_json,
    transcribe,
    youtube_id,
)


def test_whisper_json_becomes_monotonic_sidecar():
    path = Path("tests/planner/fixtures/whisper_sample.json")

    side = WordsSidecar.model_validate(sidecar_from_whisper_json(path, vtt=""))

    assert [w.w for w in side.words] == ["They", "dont", "reason"]
    assert side.words[1].conf == 0.9
    assert side.words[2].conf == 0.8
    assert side.segments and side.segments[0].text == "They dont reason"


def test_whisper_json_without_words_keeps_fallback_segments(tmp_path: Path):
    payload = tmp_path / "audio.json"
    payload.write_text('{"segments":[{"text":"cue only","start":1.0,"end":2.0}]}')

    side = WordsSidecar.model_validate(sidecar_from_whisper_json(payload, vtt=""))

    assert side.words == []
    assert side.segments[0].text == "cue only"


def test_vtt_becomes_segments_only_sidecar():
    vtt = "WEBVTT\n\n00:00:04.120 --> 00:00:05.010\nThey don't reason\n"

    side = WordsSidecar.model_validate(sidecar_from_vtt(vtt))

    assert side.words == []
    assert side.segments[0].start_s == 4.12
    assert side.segments[0].text == "They don't reason"


def test_vtt_multiline_cue_text_is_collapsed():
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nhello\nthere\n"

    side = WordsSidecar.model_validate(sidecar_from_vtt(vtt))

    assert side.segments[0].text == "hello there"


def test_non_monotonic_words_rejected():
    with pytest.raises(ValidationError):
        WordsSidecar.model_validate(
            {
                "schema_version": "1",
                "words": [
                    {"w": "b", "start": 5.0, "end": 5.2},
                    {"w": "a", "start": 1.0, "end": 1.1},
                ],
                "segments": [],
            }
        )


def test_conf_out_of_range_rejected():
    with pytest.raises(ValidationError):
        WordsSidecar.model_validate(
            {
                "schema_version": "1",
                "words": [{"w": "a", "start": 1.0, "end": 1.1, "conf": 1.5}],
                "segments": [],
            }
        )


def test_wordless_captions_escalate_to_whisper():
    calls = {"whisper": 0}

    def run_caption(_video_id: str) -> str:
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello there\n"

    def run_whisper(source: str) -> dict:
        calls["whisper"] += 1
        assert source == "https://youtu.be/abc123"
        return {
            "schema_version": "1",
            "words": [{"w": "hello", "start": 1.0, "end": 1.2}],
            "segments": [],
        }

    side = transcribe("https://youtu.be/abc123", run_caption=run_caption, run_whisper=run_whisper)

    assert side.words
    assert calls["whisper"] == 1


def test_wordless_captions_allow_coarse_skips_whisper():
    def run_caption(_video_id: str) -> str:
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello there\n"

    def run_whisper(_source: str) -> dict:
        raise AssertionError("whisper must not run under allow_coarse")

    side = transcribe(
        "https://youtu.be/abc123",
        run_caption=run_caption,
        run_whisper=run_whisper,
        allow_coarse=True,
    )

    assert side.words == []
    assert side.segments


def test_caption_sidecar_with_word_timing_skips_whisper():
    def run_caption(_video_id: str) -> dict:
        return {
            "schema_version": "1",
            "words": [{"w": "caption", "start": 0.0, "end": 0.4}],
            "segments": [],
        }

    def run_whisper(_source: str) -> dict:
        raise AssertionError("whisper must not run when captions provide words")

    side = transcribe("https://www.youtube.com/watch?v=abc123", run_caption=run_caption, run_whisper=run_whisper)

    assert side.words[0].w == "caption"


def test_captions_unavailable_falls_back_to_whisper():
    def run_caption(_video_id: str) -> None:
        return None

    def run_whisper(_source: str) -> dict:
        return {
            "schema_version": "1",
            "words": [{"w": "hello", "start": 1.0, "end": 1.4}],
            "segments": [],
        }

    side = transcribe("https://youtu.be/abc123", run_caption=run_caption, run_whisper=run_whisper)

    assert side.words[0].w == "hello"


def test_local_file_uses_whisper_directly(tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"")
    calls = {"caption": 0}

    def run_caption(_video_id: str) -> None:
        calls["caption"] += 1
        return None

    def run_whisper(source: str) -> dict:
        assert source == str(media)
        return {
            "schema_version": "1",
            "words": [{"w": "local", "start": 0.0, "end": 0.2}],
            "segments": [],
        }

    side = transcribe(str(media), run_caption=run_caption, run_whisper=run_whisper)

    assert side.words[0].w == "local"
    assert calls["caption"] == 0


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://youtu.be/abc123", "abc123"),
        ("https://www.youtube.com/watch?v=abc123", "abc123"),
        ("https://youtube.com/shorts/abc123", "abc123"),
        ("/tmp/local.mp4", None),
    ],
)
def test_youtube_id_routes_supported_urls(source: str, expected: str | None):
    assert youtube_id(source) == expected
