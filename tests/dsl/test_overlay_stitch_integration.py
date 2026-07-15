"""B8 · real-ffmpeg closure for the overlay-stitch seam (fail-closed).

download(stub) -> apply_overlays() -> stitch_footage_reel(), then prove the
cut-ins are visible in the final mp4, the canvas is spatially normalized once,
and audio survives (including a synthesized-silence source segment).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from reel_af.dsl.models import FootageReel, SourceSegment, Transition
from reel_af.render.footage_stitch import (
    build_footage_filtergraph,
    download_segments,
    stitch_footage_reel,
)
from reel_af.render.overlay_stitch import apply_overlays

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _require_ffmpeg() -> None:
    if not FFMPEG or not FFPROBE:
        pytest.fail("B8 overlay-stitch closure requires ffmpeg + ffprobe on PATH (fail-closed)")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(cmd)}\n{proc.stderr}")
    return proc


def _gradient_png(path: Path, w: int = 360, h: int = 640) -> Path:
    from PIL import Image

    img = Image.new("RGB", (w, h))
    for y in range(h):
        v = int(255 * y / (h - 1))
        img.paste(Image.new("RGB", (w, 1), (v, v, v)), (0, y))
    img.save(path)
    return path


def _solid_png(path: Path, colour: tuple[int, int, int], w: int = 1080, h: int = 1920) -> Path:
    from PIL import Image

    Image.new("RGB", (w, h), colour).save(path)
    return path


def _img_to_video(png: Path, out: Path, dur: float, *, with_audio: bool) -> Path:
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-i", str(png)]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-t", f"{dur:.3f}", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(out)]
    _run(cmd)
    return out


def _frame_mean(video: Path, t: float, tmp: Path, box: tuple[int, int, int, int] | None = None):
    from PIL import Image, ImageStat

    png = tmp / f"frame-{t:.3f}.png"
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", str(video), "-ss", f"{t:.3f}",
          "-frames:v", "1", str(png)])
    im = Image.open(png).convert("RGB")
    if box:
        im = im.crop(box)
    return tuple(ImageStat.Stat(im).mean)


def _duration(path: Path) -> float:
    proc = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(proc.stdout.strip())


def _has_audio(path: Path) -> bool:
    proc = _run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=index", "-of", "csv=p=0", str(path)])
    return bool(proc.stdout.strip())


async def test_overlay_stitch_closure(tmp_path):
    _require_ffmpeg()
    pytest.importorskip("PIL")

    # ── fixtures (all generated locally, no network provider) ──
    grad = _gradient_png(tmp_path / "grad.png")
    magenta = _solid_png(tmp_path / "magenta.png", (255, 0, 255))
    src_a = _img_to_video(grad, tmp_path / "src_a.mp4", 2.0, with_audio=True)   # has audio
    src_b = _img_to_video(grad, tmp_path / "src_b.mp4", 1.0, with_audio=False)  # no audio
    assert _has_audio(src_a) and not _has_audio(src_b)

    reel = FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(segment_id="s-a", source_url="fixture", start_s=0.0, end_s=2.0, text="a"),
            SourceSegment(segment_id="s-b", source_url="fixture", start_s=0.0, end_s=1.0, text="b"),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="none", duration_s=0.0)],
        duration_s=3.0,
    )
    sources = {"s-a": src_a, "s-b": src_b}

    def _fetch(request):
        shutil.copy(sources[request.segment_id], request.target_path)
        return request.target_path

    assets = download_segments(reel, tmp_path / "dl", _fetch)

    async def _provider(prompt, idx, images_dir):
        return magenta

    plan = {
        "s-a": [
            {"type": "zoom", "at_s": 0.2, "until_s": 0.8},
            {"type": "visual", "at_s": 1.2, "until_s": 1.8, "image_prompt": "magenta full-frame"},
        ],
        "s-b": [{"type": "zoom", "at_s": 0.2, "until_s": 0.8}],  # no-audio → silence synthesized
    }
    overlaid = await apply_overlays(
        reel, assets, plan, tmp_path / "out", "run-b8", image_provider=_provider
    )
    assert overlaid["s-a"].pre_normalized is True
    assert overlaid["s-b"].pre_normalized is True

    # C2 · pre-normalized inputs skip a second spatial scale/crop
    graph = build_footage_filtergraph(reel, overlaid)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" not in graph.filter_complex
    assert "crop=1080:1920" not in graph.filter_complex
    assert "trim=start=" in graph.filter_complex

    final = await stitch_footage_reel(reel, overlaid, tmp_path / "out", "run-b8")
    assert final.exists()

    # duration + audio survived (incl. the synthesized-silence segment)
    assert abs(_duration(final) - 3.0) < 0.25
    assert _has_audio(final)

    # ── visual cut-in: window frame ≈ magenta, neutral frame is grey ──
    vis_r, vis_g, vis_b = _frame_mean(final, 1.5, tmp_path)      # inside visual window
    assert vis_r > 150 and vis_b > 150 and vis_g < 90            # magenta
    neu_r, neu_g, neu_b = _frame_mean(final, 1.0, tmp_path)      # neutral gradient (grey)
    assert abs(neu_r - neu_g) < 25 and abs(neu_g - neu_b) < 25   # grey, not magenta
    assert (vis_r + vis_b) - (neu_r + neu_b) > 120               # overlay clearly differs

    # ── zoom cut-in: top band differs between zoom and neutral windows ──
    band = (0, 150, 1080, 450)
    zoom_mean = _frame_mean(final, 0.5, tmp_path, band)[0]       # inside zoom window
    base_mean = _frame_mean(final, 1.0, tmp_path, band)[0]       # neutral (no effect)
    assert abs(zoom_mean - base_mean) > 8.0
