from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from reel_af.dsl.models import WordsSidecar

DSL_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_path() -> Callable[[str], Path]:
    def _fixture_path(name: str) -> Path:
        return DSL_FIXTURES_DIR / name

    return _fixture_path


@pytest.fixture
def read_fixture(fixture_path) -> Callable[[str], str]:
    def _read_fixture(name: str) -> str:
        return fixture_path(name).read_text(encoding="utf-8")

    return _read_fixture


@pytest.fixture
def source_words_json(fixture_path) -> dict:
    return json.loads(fixture_path("source.words.json").read_text(encoding="utf-8"))


@pytest.fixture
def source_words_sidecar(fixture_path) -> WordsSidecar:
    return WordsSidecar.model_validate_json(
        fixture_path("source.words.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def lavfi_mp4_factory(tmp_path) -> Callable[..., Path]:
    def _lavfi_mp4_factory(
        *,
        name: str = "source",
        duration_s: float = 1.0,
        frequency_hz: int = 440,
        size: str = "320x568",
        fps: int = 30,
    ) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise AssertionError("ffmpeg is required for DSL lavfi fixture generation")

        out = tmp_path / f"{name}.mp4"
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate={fps}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency_hz}:sample_rate=48000",
            "-t",
            str(duration_s),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise AssertionError(f"ffmpeg lavfi fixture generation failed:\n{proc.stderr}")
        return out

    return _lavfi_mp4_factory
