"""B7 → stitch_footage_reel normalizes real source footage to 1080x1920.

CHARACTERIZATION: footage_stitch already does
``scale=...:force_original_aspect_ratio=increase,crop=1080:1920``. This pins that
A1-shaped, landscape source footage really does come out vertical. If it fails,
the failure IS the finding.

Uses the ordinary requires_ffmpeg skipif — this is not a BLOCKING closure gate.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest

from reel_af.dsl.models import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    BlackSegment,
    FootageReel,
    SourceSegment,
    Transition,
)
from reel_af.render.footage_stitch import download_segments, stitch_footage_reel

SRC = "https://www.youtube.com/watch?v=abc123"

requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg + ffprobe on PATH",
)


def _probe_dimensions(path) -> tuple[int, int]:
    out = subprocess.run(
        [
            shutil.which("ffprobe"), "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=30, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return stream["width"], stream["height"]


def _reel(*, segment_id="s0", start_s=0.0, end_s=1.0) -> FootageReel:
    return FootageReel(
        source_url=SRC,
        segments=[
            SourceSegment(
                segment_id=segment_id, source_url=SRC, start_s=start_s, end_s=end_s, text="t"
            )
        ],
        transitions=[],
        duration_s=end_s - start_s,
    )


@requires_ffmpeg
def test_landscape_source_normalizes_to_vertical(lavfi_mp4_factory, tmp_path):
    landscape = lavfi_mp4_factory(name="landscape", size="1920x1080", duration_s=2.0)
    reel = _reel(end_s=1.0)
    assets = download_segments(reel, tmp_path / "seg", fetch=lambda req: landscape)

    out = asyncio.run(stitch_footage_reel(reel, assets, tmp_path / "out", run_id="b7"))

    assert _probe_dimensions(out) == (CANVAS_WIDTH, CANVAS_HEIGHT)


@requires_ffmpeg
def test_square_source_normalizes_to_vertical(lavfi_mp4_factory, tmp_path):
    square = lavfi_mp4_factory(name="square", size="720x720", duration_s=2.0)
    reel = _reel(end_s=1.0)
    assets = download_segments(reel, tmp_path / "seg", fetch=lambda req: square)

    out = asyncio.run(stitch_footage_reel(reel, assets, tmp_path / "out", run_id="b7"))

    assert _probe_dimensions(out) == (CANVAS_WIDTH, CANVAS_HEIGHT)


@requires_ffmpeg
def test_black_segment_renders_at_canvas_size(lavfi_mp4_factory, tmp_path):
    """Black segments request no asset (footage_stitch skips them)."""
    source = lavfi_mp4_factory(name="src", size="1920x1080", duration_s=2.0)
    reel = FootageReel(
        source_url=SRC,
        segments=[
            SourceSegment(segment_id="s0", source_url=SRC, start_s=0.0, end_s=1.0, text="t"),
            BlackSegment(duration_s=0.5),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="fade", duration_s=0.0)],
        duration_s=1.5,
    )
    assets = download_segments(reel, tmp_path / "seg", fetch=lambda req: source)

    assert set(assets) == {"s0"}  # black segment fetched nothing

    out = asyncio.run(stitch_footage_reel(reel, assets, tmp_path / "out", run_id="b7"))

    assert _probe_dimensions(out) == (CANVAS_WIDTH, CANVAS_HEIGHT)


def test_duplicate_segment_id_is_refused(lavfi_mp4_factory, tmp_path):
    from reel_af.render.footage_stitch import SegmentAssetValidationError

    reel = FootageReel(
        source_url=SRC,
        segments=[
            SourceSegment(segment_id="dup", source_url=SRC, start_s=0.0, end_s=1.0, text="a"),
            SourceSegment(segment_id="dup", source_url=SRC, start_s=2.0, end_s=3.0, text="b"),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="fade", duration_s=0.0)],
        duration_s=2.0,
    )

    with pytest.raises(SegmentAssetValidationError, match="duplicate"):
        download_segments(reel, tmp_path / "seg", fetch=lambda req: tmp_path / "nope.mp4")
