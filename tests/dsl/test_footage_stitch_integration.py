from __future__ import annotations

import subprocess

import pytest

from reel_af.dsl.models import (
    BlackSegment,
    DownloadedSegment,
    FootageReel,
    SourceSegment,
    Transition,
)
from reel_af.render.footage_stitch import stitch_footage_reel


@pytest.mark.asyncio
async def test_stitch_footage_reel_renders_black_xfade_and_audio_cut(
    tmp_path,
    lavfi_mp4_factory,
):
    src1 = lavfi_mp4_factory(name="src1", duration_s=2.0, frequency_hz=440)
    src2 = lavfi_mp4_factory(name="src2", duration_s=2.0, frequency_hz=880)
    reel = FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(
                segment_id="seg-1",
                source_url="fixture",
                start_s=0.0,
                end_s=1.0,
                text="one",
            ),
            BlackSegment(duration_s=0.4),
            SourceSegment(
                segment_id="seg-2",
                source_url="fixture",
                start_s=1.0,
                end_s=2.0,
                text="two",
            ),
        ],
        transitions=[
            Transition(before_index=0, after_index=1, effect="none", duration_s=0.0),
            Transition(
                before_index=1,
                after_index=2,
                effect="dissolve",
                duration_s=0.2,
                audio_fade=False,
            ),
        ],
        duration_s=2.2,
    )
    assets = {
        "seg-1": DownloadedSegment(
            segment_id="seg-1",
            path=src1,
            source_start_s=0.0,
            source_end_s=2.0,
        ),
        "seg-2": DownloadedSegment(
            segment_id="seg-2",
            path=src2,
            source_start_s=1.0,
            source_end_s=3.0,
        ),
    }

    out = await stitch_footage_reel(reel, assets, tmp_path / "out", "run-1")

    assert out.exists()
    assert out.stat().st_size > 0
    assert _probe_duration(out) == pytest.approx(2.2, abs=0.15)
    assert _max_rgb_at(out, 1.1) <= 3


def _probe_duration(path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(proc.stdout.strip())


def _max_rgb_at(path, at_s: float) -> int:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{at_s:.3f}",
            "-i",
            str(path),
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    assert proc.stdout
    return max(proc.stdout)
