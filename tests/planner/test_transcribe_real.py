from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reel_af.planner.config import AsrEntry
from reel_af.planner.transcribe import transcribe_audio

pytestmark = pytest.mark.requires_openrouter(
    reason="real OpenRouter whisper-large-v3 word timestamp probe"
)


@pytest.fixture
def spoken_audio(tmp_path: Path) -> Path:
    audio = tmp_path / "hello-world.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "flite=text='hello world':voice=kal",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )
    return audio


async def test_whisper_large_v3_returns_word_offsets(spoken_audio: Path):
    sidecar = await transcribe_audio(
        spoken_audio,
        entry=AsrEntry(
            model="openai/whisper-large-v3",
            word_ts="native",
            response_format="verbose_json",
            request_word_timestamps=True,
        ),
        max_retries=1,
    )

    assert sidecar.words
    assert all(word.end >= word.start for word in sidecar.words)
    assert sidecar.words[0].start >= 0
