"""TASK 2 — computed high-contrast banner + captions.

Covers the four render changes validated on real reels (see ``banner_fix_proto.py``):
  1. ``compute_divider_y`` finds the dark divider bar (with a confidence
     fallback when there's no distinct bar).
  2. banner font-fit — long hooks get a smaller computed ``\\fs`` than short ones.
  3. banner style — PURPLE text on an opaque WHITE box.
  4. caption style — white text on a semi-opaque dark box, at ``int(0.70·H)``.

The pure pieces are unit-tested with an injected frame; one integration test
drives the real ffmpeg frame-extraction path (fail-closed if ffmpeg absent).
"""

from __future__ import annotations

import re
import subprocess

from util_captions import StubFinishConfig, parse_dialogues, requires_ffmpeg

from reel_af.render import captions

_FS_RE = re.compile(r"\\fs(\d+)")


def _make_gray_with_band(w, h, band_y0, band_y1, *, dark=20, light=200):
    """A grayscale PIL image, uniformly ``light`` except a dark horizontal band."""
    from PIL import Image

    im = Image.new("L", (w, h), light)
    px = im.load()
    for y in range(band_y0, band_y1):
        for x in range(w):
            px[x, y] = dark
    return im


# ───── compute_divider_y ─────────────────────────────────────────────


def test_compute_divider_y_finds_a_known_dark_band():
    cfg = StubFinishConfig()  # band search y∈[0.28·1920, 0.58·1920] = [537,1113]
    band_y0, band_y1 = 760, 790

    def fake_extract(base, cfg):
        return _make_gray_with_band(1080, 1920, band_y0, band_y1)

    dy = captions.compute_divider_y("unused.mp4", cfg, extract_frame=fake_extract)
    assert band_y0 <= dy <= band_y1  # center of the dark bar


def test_compute_divider_y_falls_back_on_uniform_frame():
    from PIL import Image

    cfg = StubFinishConfig(divider_y=772)

    def flat_extract(base, cfg):
        return Image.new("L", (1080, 1920), 100)  # no distinct dark bar

    dy = captions.compute_divider_y("unused.mp4", cfg, extract_frame=flat_extract)
    assert dy == 772  # detection fails → configured fallback


def test_compute_divider_y_falls_back_when_extraction_raises():
    cfg = StubFinishConfig(divider_y=772)

    def boom(base, cfg):
        raise RuntimeError("ffmpeg missing")

    assert captions.compute_divider_y("x.mp4", cfg, extract_frame=boom) == 772


def test_compute_divider_y_respects_configured_fallback_value():
    cfg = StubFinishConfig(divider_y=650)

    def boom(base, cfg):
        raise RuntimeError("nope")

    assert captions.compute_divider_y("x.mp4", cfg, extract_frame=boom) == 650


@requires_ffmpeg
def test_compute_divider_y_real_ffmpeg_frame(tmp_path):
    reel = tmp_path / "bar.mp4"
    # gray reel with a full-width black bar at y=760..790 (center 775).
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=4:r=30",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-vf", "drawbox=x=0:y=760:w=1080:h=30:color=black:t=fill",
            "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(reel),
        ],
        check=True, capture_output=True,
    )
    dy = captions.compute_divider_y(reel, StubFinishConfig())
    assert 745 <= dy <= 805, f"expected divider near the black bar, got {dy}"


# ───── banner font-fit ───────────────────────────────────────────────


def test_long_hook_gets_smaller_fontsize_than_short_hook():
    cfg = StubFinishConfig()
    short = captions.compute_banner_fontsize("HI", cfg)
    long = captions.compute_banner_fontsize("A" * 60, cfg)
    assert long < short


def test_fontsize_capped_at_max_for_short_hooks():
    cfg = StubFinishConfig(banner_fit_max_fs=58)
    assert captions.compute_banner_fontsize("HI", cfg) == 58


def test_fontsize_floored_at_min_for_very_long_hooks():
    cfg = StubFinishConfig(banner_fit_min_fs=30)
    assert captions.compute_banner_fontsize("A" * 200, cfg) == 30


def test_banner_event_embeds_computed_fs_override():
    cfg = StubFinishConfig()
    hook = "stop telling ai what to do and start"
    ass = captions.build_banner_ass(hook, 10.0, cfg)
    banner_line = next(ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",Banner," in ln)
    m = _FS_RE.search(banner_line)
    assert m, "banner dialogue must carry a computed \\fs override"
    expected = captions.compute_banner_fontsize(hook.upper(), cfg)
    assert int(m.group(1)) == expected
    # \pos still present and parseable alongside \fs
    assert parse_dialogues(ass)[0].y == cfg.divider_y


# ───── styles against the REAL config ────────────────────────────────


def _style_fields(ass_text, style_name):
    line = next(ln for ln in ass_text.splitlines() if ln.startswith(f"Style: {style_name},"))
    return line.split(",")


def test_banner_style_is_purple_on_white_box():
    from reel_af.render.finish_config import ReelFinishConfig

    cfg = ReelFinishConfig()
    ass = captions.build_banner_ass("hook", 5.0, cfg)
    f = _style_fields(ass, captions.BANNER_STYLE_NAME)
    # fields (Name at [0]): Primary[3], Back[6], BorderStyle[15], Outline[16]
    assert f[3] == "&H00CE227E"   # purple #7E22CE
    assert f[6] == "&H00FFFFFF"   # opaque white box
    assert f[15].strip() == "3"   # BorderStyle = opaque box
    assert int(f[16]) >= 6        # thick outline


def test_caption_style_is_white_on_dark_box_at_70pct():
    from reel_af.render.finish_config import ReelFinishConfig

    cfg = ReelFinishConfig()
    ass = captions.build_caption_ass([(0.0, 0.4, "hi"), (0.4, 0.8, "there")], cfg)
    f = _style_fields(ass, captions.CAPTION_STYLE_NAME)
    assert f[3] == "&H00FFFFFF"   # white fill
    assert f[6] == "&HB0000000"   # semi-opaque dark box
    assert f[15].strip() == "3"   # BorderStyle = opaque box
    assert cfg.caption_safe_y == int(0.70 * cfg.canvas_h)
    caps = [d for d in parse_dialogues(ass) if d.style == captions.CAPTION_STYLE_NAME]
    assert caps and all(d.y == cfg.caption_safe_y for d in caps)
