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


# ───── banner font-fit (MEASURED, two-line) ──────────────────────────


def test_long_hook_gets_smaller_fontsize_than_short_hook():
    cfg = StubFinishConfig()
    short = captions.compute_banner_fontsize("HI", cfg)
    long = captions.compute_banner_fontsize("SUPERCALIFRAGILISTIC EXPIALIDOCIOUS ANTIDISESTABLISHMENT", cfg)
    assert long < short


def test_fontsize_capped_at_max_for_short_hooks():
    cfg = StubFinishConfig(banner_max_fs=90)
    assert captions.compute_banner_fontsize("HI", cfg) == 90


def test_fontsize_shrinks_for_a_very_long_single_word():
    # One un-splittable word can't be balanced-wrapped, so it must shrink to fit.
    cfg = StubFinishConfig()
    fs = captions.compute_banner_fontsize("A" * 60, cfg)
    assert 8 <= fs < captions.compute_banner_fontsize("HI", cfg)


def test_fit_is_measured_not_char_ratio():
    """Two strings of equal length but different real widths fit differently."""
    cfg = StubFinishConfig(banner_max_fs=200, banner_max_lines=1)
    wide = captions.compute_banner_fontsize("W" * 20, cfg)   # W is a wide glyph
    narrow = captions.compute_banner_fontsize("i" * 20, cfg)  # i is a narrow glyph
    assert narrow > wide  # a char-count·ratio guess would make these equal


def test_balanced_wrap_splits_to_minimise_widest_line():
    cfg = StubFinishConfig()
    lines = captions.balanced_wrap("ALPHA BETA GAMMA DELTA", cfg, font_file=None)
    assert len(lines) == 2
    assert lines == ["ALPHA BETA", "GAMMA DELTA"]


def test_banner_event_embeds_computed_fs_override():
    cfg = StubFinishConfig()
    hook = "stop telling ai what to do and start"
    ass = captions.build_banner_ass(hook, 10.0, cfg)
    text_line = next(
        ln for ln in ass.splitlines()
        if ln.startswith("Dialogue:") and ",Banner," in ln and ",BannerBox," not in ln
    )
    m = _FS_RE.search(text_line)
    assert m, "banner text dialogue must carry a computed \\fs override"
    expected = captions.compute_banner_fontsize(hook.upper(), cfg)
    assert int(m.group(1)) == expected


# ───── styles against the REAL config ────────────────────────────────


def _style_fields(ass_text, style_name):
    line = next(ln for ln in ass_text.splitlines() if ln.startswith(f"Style: {style_name},"))
    return line.split(",")


def test_banner_is_purple_text_on_white_box():
    from reel_af.render.finish_config import ReelFinishConfig

    cfg = ReelFinishConfig()
    ass = captions.build_banner_ass("hook", 5.0, cfg)
    # The white box is its own BannerBox drawing; the text is clean purple on it.
    box = _style_fields(ass, captions.BANNER_BOX_STYLE_NAME)
    text = _style_fields(ass, captions.BANNER_STYLE_NAME)
    # fields (Name at [0]): Primary[3], Back[6], BorderStyle[15], Outline[16]
    assert box[3] == "&H00FFFFFF"    # box fill = opaque white
    assert text[3] == "&H00CE227E"   # purple #7E22CE text
    assert text[15].strip() == "1"   # text has no opaque box of its own
    assert int(text[16]) == 0        # clean text, no outline over the box


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
