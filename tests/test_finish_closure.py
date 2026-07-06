"""B9 closure gate — real ffmpeg burn of banner + captions + image cut-ins.

This is the BLOCKING integration test. It runs a **real** ffmpeg pass over a
tiny synthetic fixture reel, composing:
  - SapphireBarn's combined caption+banner ASS (real ``build_finish_ass``),
  - CobaltMeadow's image-overlay filtergraph (real ``build_image_overlay_filtergraph``),
  - the finish module's single-pass burn.

Only whisper (B2), the LLM (B5/B6) and image *generation* (B7) are faked — the
render mechanics are all real. Fail-closed if ffmpeg/ffprobe is absent (never
skip-to-green).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from reel_af.render import captions, image_cutins
from reel_af.render.finish import (
    FinishContext,
    FinishDeps,
    ReelFinishConfig,
    finish_reel,
    probe_duration,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

REEL_DUR_S = 4.0


def _require_ffmpeg() -> None:
    if not FFMPEG or not FFPROBE:
        pytest.fail("B9 closure requires ffmpeg + ffprobe on PATH (fail-closed)")


def _make_fixture_reel(path: Path) -> Path:
    """A 1080x1920 solid-navy reel with a silent audio track."""
    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=navy:s=1080x1920:d={REEL_DUR_S}:r=30",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-t", str(REEL_DUR_S), str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_fixture_image(path: Path, colour: str) -> Path:
    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=1080x1120:d=1",
            "-frames:v", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _closure_deps(tmp_path: Path) -> FinishDeps:
    """Real render mechanics; faked whisper / LLM / image-gen."""

    async def generate_hook(transcript, provider):
        return "this changes everything"

    def caption_words(reel_path, cfg):
        # Fixed word timings covering the reel so captions are active mid-reel.
        return [
            (0.2, 0.8, "this"),
            (0.8, 1.4, "little"),
            (1.4, 2.0, "trick"),
            (2.0, 2.6, "works"),
            (2.6, 3.4, "everywhere"),
        ]

    async def pick_image_moments(transcript, provider, cfg, duration_s):
        # Two non-overlapping cut-ins inside the safe interior.
        return [(0.5, 2.0, "a diagram"), (2.2, 3.5, "a chart")]

    async def gen_cutins(provider, cut_ins, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        colours = ["red", "green"]
        built = []
        for i, c in enumerate(image_cutins.normalize_image_cutins(cut_ins)):
            img = _make_fixture_image(out_dir / f"cutin-{i}.png", colours[i % 2])
            built.append(c.model_copy(update={"image_path": img}))
        return built

    def overlay_graph(cut_ins, cfg):
        return image_cutins.build_image_overlay_filtergraph(cut_ins, config=cfg)

    async def run_ffmpeg(cmd, timeout_s):
        proc = subprocess.run(list(cmd), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1000:]}")

    return FinishDeps(
        caption_words=caption_words,
        build_finish_ass=captions.build_finish_ass,
        write_ass=captions.write_ass,
        generate_hook=generate_hook,
        pick_image_moments=pick_image_moments,
        generate_image_cutins=gen_cutins,
        build_overlay_graph=overlay_graph,
        image_paths_for_cutins=image_cutins.image_paths_for_cutins,
        run_ffmpeg=run_ffmpeg,
        probe_duration=probe_duration,
    )


def _band(video: Path, t: float, y0: int, y1: int, tmp: Path):
    """Extract the frame at ``t`` and return the [y0,y1) band as a PIL image."""
    from PIL import Image

    frame = tmp / f"frame-{t:.2f}.png"
    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error",
            "-ss", f"{t:.3f}", "-i", str(video),
            "-frames:v", "1", str(frame),
        ],
        check=True,
        capture_output=True,
    )
    return Image.open(frame).convert("RGB").crop((0, y0, 1080, y1))


def _band_variance(video: Path, t: float, y0: int, y1: int, tmp: Path) -> float:
    """Sum of per-channel stddev — 0 for flat background, >0 once text/box burns in."""
    from PIL import ImageStat

    return float(sum(ImageStat.Stat(_band(video, t, y0, y1, tmp)).stddev))


def _band_mean(video: Path, t: float, y0: int, y1: int, tmp: Path) -> tuple[float, ...]:
    from PIL import ImageStat

    return tuple(ImageStat.Stat(_band(video, t, y0, y1, tmp)).mean)


async def test_finish_reel_closure_burns_all_layers(tmp_path) -> None:
    _require_ffmpeg()
    base = _make_fixture_reel(tmp_path / "base.mp4")
    base_dur = probe_duration(base)

    cfg = ReelFinishConfig()
    ctx = FinishContext(
        source_url="http://example/x", transcript="a talk about a trick", text_provider=object(), image_provider=object()
    )
    out = await finish_reel(base, ctx, cfg, deps=_closure_deps(tmp_path), out_dir=tmp_path)

    # Output exists and duration ≈ base.
    assert out.exists()
    out_dur = probe_duration(out)
    assert out_dur == pytest.approx(base_dur, abs=0.3)

    # Caption band (around caption_safe_y=1330) has burned pixels at a caption time.
    cap_var = _band_variance(out, t=1.6, y0=cfg.caption_safe_y - 60, y1=cfg.caption_safe_y + 60, tmp=tmp_path)
    assert cap_var > 1.0, "expected caption text pixels in the safe-zone band"

    # Banner band (around divider_y=772) has burned pixels (full-duration).
    ban_var = _band_variance(out, t=1.6, y0=cfg.divider_y - 40, y1=cfg.divider_y + 40, tmp=tmp_path)
    assert ban_var > 1.0, "expected banner pixels on the divider band"

    # Image cut-in toggles with its enable window: a solid overlay is uniform,
    # so compare the region's MEAN colour when a cut-in is active (t=1.0, window
    # 0.5–2.0) vs after all cut-ins end (t=3.8, last ends 3.5) — it must change.
    y0, y1 = cfg.image_region.y + 300, cfg.image_region.y + 500
    active = _band_mean(out, t=1.0, y0=y0, y1=y1, tmp=tmp_path)
    inactive = _band_mean(out, t=3.8, y0=y0, y1=y1, tmp=tmp_path)
    delta = sum(abs(a - b) for a, b in zip(active, inactive))
    assert delta > 30.0, f"expected image cut-in to change the screenshare pane (Δ={delta:.1f})"


async def test_finish_reel_closure_raw_is_untouched(tmp_path) -> None:
    _require_ffmpeg()
    base = _make_fixture_reel(tmp_path / "base.mp4")
    ctx = FinishContext(source_url=None, transcript="t", text_provider=object(), image_provider=object())
    out = await finish_reel(base, ctx, ReelFinishConfig(), deps=_closure_deps(tmp_path), raw=True, out_dir=tmp_path)
    assert out == base  # fast path yields the plain stitched reel
