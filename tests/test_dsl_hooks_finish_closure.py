"""B8 (BLOCKING, CT-1 leg) — the DSL-hooks reel really has banner/captions/cut-ins burned in.

Asserts on PIXELS of the mp4 the worker actually produced, not on mock calls.

Scope note: the "image cut-ins" here are finish_reel's OWN LLM-picked image
moments (image_cutins.ImageCutIn — final-reel-relative, images-only), which are
live and in scope. A1's zoom/visual cut-ins are a DIFFERENT subsystem
(overlays.CutInOverlay — absolute source time); Slice A only MAPS those (B9a).
Overlay RENDERING is B9b, deferred. Do not conflate the two.

Only the LLM and image GENERATION are faked — the same boundary
tests/test_finish_closure.py draws. Every render mechanic (ASS, filtergraph,
ffmpeg burn) is real.

Fail-closed on missing ffmpeg: a skipped closure test is UNVERIFIED, not green.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from test_dsl_hooks_worker_closure import (  # reuse the CT-1 fakes — one set, not two
    _FakeImageProvider,
    _FakeTextProvider,
    _fetch,
)

from reel_af.app import dsl_hooks_to_reels
from reel_af.render.finish_config import ReelFinishConfig

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

FIXTURES = Path(__file__).resolve().parent / "dsl" / "fixtures"
A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"

CANVAS_W, CANVAS_H = 1080, 1920


def _require_ffmpeg() -> None:
    if not FFMPEG or not FFPROBE:
        pytest.fail("B8 closure requires ffmpeg + ffprobe on PATH (fail-closed)")


def _band(video: Path, t: float, y0: int, y1: int, tmp: Path):
    from PIL import Image

    frame = tmp / f"frame-{t:.2f}-{y0}.png"
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", str(frame)],
        check=True, capture_output=True,
    )
    return Image.open(frame).convert("RGB").crop((0, y0, CANVAS_W, y1))


def _band_variance(video: Path, t: float, y0: int, y1: int, tmp: Path) -> float:
    from PIL import ImageStat

    return float(sum(ImageStat.Stat(_band(video, t, y0, y1, tmp)).stddev))


@pytest.fixture
def a1_refs() -> dict:
    return {
        "source_url": A1_SOURCE_URL,
        "composite_ref": str(FIXTURES / "a1_composite.ts.md"),
        "words_ref": str(FIXTURES / "source.words.json"),
        "hook_ref": str(FIXTURES / "a1_hook_plan.json"),
        "clip_idx": 1,
    }


@pytest.fixture
def lavfi_source(tmp_path) -> Path:
    _require_ffmpeg()
    out = tmp_path / "source.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=navy:s=1920x1080:r=30",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "90", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(out)],
        capture_output=True, text=True, timeout=180, check=True,
    )
    return out


@pytest.fixture
def finished_reel(a1_refs, lavfi_source, tmp_path) -> Path:
    """Run the REAL worker and keep the mp4 it produced."""
    _require_ffmpeg()
    captured: dict = {}

    def _capture_uploader(local_path, *, run_id, filename=None, **kw):
        captured["path"] = Path(local_path)
        return "https://bucket.example.com/outputs/x/reel.mp4"

    result = asyncio.run(
        dsl_hooks_to_reels(
            **a1_refs,
            out_dir=str(tmp_path / "work"),
            fetch_segment=_fetch(lavfi_source),
            text_provider=_FakeTextProvider(),
            image_provider=_FakeImageProvider(),
            uploader=_capture_uploader,
        )
    )
    assert "error" not in result, result
    assert captured["path"].is_file()
    return captured["path"]


def test_finished_reel_is_vertical(finished_reel):
    import json

    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(finished_reel)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (CANVAS_W, CANVAS_H)


def test_hook_banner_is_burned_in(finished_reel, tmp_path):
    """The source is flat navy — any variance in the banner band is burned content."""
    cfg = ReelFinishConfig()
    variance = _band_variance(finished_reel, 0.5, 0, cfg.divider_y or 700, tmp_path)

    assert variance > 1.0, f"hook banner band is flat ({variance}) — nothing burned in"


def test_captions_are_burned_in_the_safe_zone(finished_reel, tmp_path):
    """Captions burn below the divider, inside the safe zone."""
    cfg = ReelFinishConfig()
    y0 = cfg.divider_y or 700
    variance = _band_variance(finished_reel, 1.0, y0, CANVAS_H, tmp_path)

    assert variance > 1.0, f"caption band is flat ({variance}) — nothing burned in"


def test_image_cutins_change_the_frame(finished_reel, tmp_path):
    """finish_reel's own image cut-ins (red PNGs from the fake generator) burn in.

    The base is flat navy, so a red image overlay measurably shifts the frame.
    """
    from PIL import ImageStat

    reds = [
        ImageStat.Stat(_band(finished_reel, t, 0, CANVAS_H, tmp_path)).mean[0]
        for t in (0.2, 1.0, 2.0, 3.0, 4.0)
    ]

    assert max(reds) > min(reds) + 1.0, f"frame never changes (reds={reds})"
