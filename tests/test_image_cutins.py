from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image
from util import make_fake_provider, square_png_bytes


class _ImageRegion:
    x = 8
    y = 120
    width = 144
    height = 180


class _FinishConfig:
    image_region = _ImageRegion()


async def test_generate_image_cutins_wraps_first_frame_generation(tmp_path):
    from reel_af.render.image_cutins import generate_image_cutins

    fake = make_fake_provider(image_data=square_png_bytes(256, color=(20, 40, 80)))
    cut_ins = await generate_image_cutins(
        provider=fake(),
        cut_ins=[(2.0, 4.0, "a clean evidence board")],
        out_dir=tmp_path,
        content_mode="scientific",
    )

    assert len(cut_ins) == 1
    assert cut_ins[0].image_path is not None
    assert cut_ins[0].image_path.exists()
    assert cut_ins[0].start_s == 2.0
    assert cut_ins[0].end_s == 4.0

    with Image.open(cut_ins[0].image_path) as image:
        assert image.size == (720, 1280)

    image_calls = [kwargs for method, kwargs in fake.calls if method == "image"]
    assert len(image_calls) == 1
    assert image_calls[0]["prompt"].startswith("a clean evidence board")


def test_image_overlay_filtergraph_scales_crops_and_overlays_each_cutin():
    from reel_af.render.image_cutins import build_image_overlay_filtergraph

    graph = build_image_overlay_filtergraph(
        [
            {"t_start": 2.0, "t_end": 4.5, "image_prompt": "first"},
            {"start_s": 6.0, "end_s": 8.0, "image_prompt": "second"},
        ],
        config=_FinishConfig(),
    )

    assert graph.image_input_count == 2
    assert "[1:v]scale=144:180:force_original_aspect_ratio=increase,crop=144:180" in graph.filter_complex
    assert "[2:v]scale=144:180:force_original_aspect_ratio=increase,crop=144:180" in graph.filter_complex
    assert "overlay=x=8:y=120:enable='between(t,2.000,4.500)'" in graph.filter_complex
    assert "overlay=x=8:y=120:enable='between(t,6.000,8.000)'" in graph.filter_complex
    assert graph.video_label == "[v]"


def test_image_overlay_filtergraph_zero_picks_is_passthrough():
    from reel_af.render.image_cutins import build_image_overlay_filtergraph

    graph = build_image_overlay_filtergraph([], config=_FinishConfig())

    assert graph.image_input_count == 0
    assert graph.filter_complex == "[0:v]null[v]"


def test_image_overlay_filtergraph_accepts_reel_finish_config_region():
    from reel_af.render.finish_config import ImageRegion, ReelFinishConfig
    from reel_af.render.image_cutins import build_image_overlay, build_image_overlay_filtergraph

    graph = build_image_overlay_filtergraph(
        [(1.0, 2.0, "configured region")],
        config=ReelFinishConfig(image_region=ImageRegion(x=4, y=6, w=80, h=90)),
    )

    assert "scale=80:90:force_original_aspect_ratio=increase,crop=80:90" in graph.filter_complex
    assert "overlay=x=4:y=6:enable='between(t,1.000,2.000)'" in graph.filter_complex
    assert build_image_overlay(
        [(1.0, 2.0, "configured region")],
        config=ReelFinishConfig(image_region=ImageRegion(x=4, y=6, w=80, h=90)),
    ) == graph.filter_complex


@pytest.mark.asyncio
async def test_render_image_cutins_runs_ffmpeg_and_paints_region(tmp_path):
    from reel_af.render.image_cutins import ImageCutIn, render_image_cutins

    _require_ffmpeg()
    base = tmp_path / "base.mp4"
    overlay = tmp_path / "overlay.png"
    out = tmp_path / "out.mp4"
    frame = tmp_path / "frame.png"

    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=180x320:d=1:r=15",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=48000",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(base),
        ]
    )
    Image.new("RGB", (50, 50), (250, 20, 20)).save(overlay)

    class SmallRegion:
        x = 0
        y = 160
        width = 180
        height = 160

    class SmallConfig:
        image_region = SmallRegion()

    await render_image_cutins(
        base_reel_path=base,
        cut_ins=[
            ImageCutIn(
                start_s=0.1,
                end_s=0.9,
                image_prompt="red block",
                image_path=overlay,
            )
        ],
        out_path=out,
        config=SmallConfig(),
        timeout_s=30.0,
    )

    assert out.exists()
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(out),
            "-frames:v",
            "1",
            str(frame),
        ]
    )
    with Image.open(frame) as image:
        top_pixel = image.getpixel((90, 60))
        bottom_pixel = image.getpixel((90, 240))

    assert top_pixel[2] > top_pixel[0]
    assert bottom_pixel[0] > bottom_pixel[2]


def _require_ffmpeg() -> None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.fail("ffmpeg and ffprobe are required for image cut-in integration")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
