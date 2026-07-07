"""Empirical banner regression test — render the ASS, MEASURE the pixels.

This is the test that would have caught the "text floating in a tall white bar"
bug: it renders the real ``build_banner_ass`` output through ffmpeg/libass onto a
frame, detects the white box and purple ink extents, and asserts the ink FILLS
the box on both axes and is CENTRED and IN-FRAME. Unit tests on ASS strings can't
see this — only rendered pixels can. Fail-closed if the render stack is absent.

Thresholds are set below the measured margin of the shipped design and ABOVE the
old broken design (fill_h ≈ 56%), so a regression to the line-box banner turns
this red.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from reel_af.render.captions import _banner_font_file, build_banner_ass
from reel_af.render.finish_config import ReelFinishConfig


def _stack_ready() -> bool:
    try:
        import PIL  # noqa: F401
    except Exception:
        return False
    from shutil import which

    if not which("ffmpeg"):
        return False
    return _banner_font_file(ReelFinishConfig()) is not None


requires_render = pytest.mark.skipif(
    not _stack_ready(), reason="needs ffmpeg + Pillow + a resolvable banner font"
)

FILL_MIN = 0.70          # ink fills ≥70% of the box on each axis (old design ≈0.56)
CENTER_TOL = 22          # ink centre within 22px of box centre on each axis


def _render_banner(hook: str, cfg: ReelFinishConfig) -> Path:
    ass = build_banner_ass(hook, 3.0, cfg)
    td = Path(tempfile.mkdtemp(prefix="bfill_"))
    a = td / "b.ass"
    a.write_text(ass)
    out = td / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c=0x2b2b2b:s={cfg.canvas_w}x{cfg.canvas_h}:d=1",
         "-vf", f"ass={a}", "-frames:v", "1", str(out)],
        check=True, capture_output=True,
    )
    return out


def _extents(png: Path):
    """Return (box_bbox, ink_bbox) as (x0,y0,x1,y1) from the rendered frame."""
    from PIL import Image

    im = Image.open(png).convert("RGB")
    px = im.load()
    w, h = im.size
    box = ink = None
    for y in range(h):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            if r > 235 and g > 235 and b > 235:
                box = _grow(box, x, y)
            elif r > 70 and b > 70 and g < 110 and r > g + 25 and b > g + 15:
                ink = _grow(ink, x, y)
    return box, ink


def _grow(bb, x, y):
    if bb is None:
        return [x, y, x, y]
    return [min(bb[0], x), min(bb[1], y), max(bb[2], x), max(bb[3], y)]


HOOKS = [
    "COLLABORATE WITH AI, DONT DELEGATE.",
    "MOST DOJOS WONT TEACH REAL COMBAT.",
    "LLMS GODLIKE MYTH VERSUS STUPID REALITY.",
    "AI IS STUPID.",
]


@requires_render
@pytest.mark.parametrize("hook", HOOKS)
def test_banner_ink_fills_box_and_is_centred(hook):
    cfg = ReelFinishConfig(divider_y=785)
    png = _render_banner(hook, cfg)
    box, ink = _extents(png)
    assert box is not None, "no white box rendered"
    assert ink is not None, "no purple ink rendered"

    bw, bh = box[2] - box[0], box[3] - box[1]
    iw, ih = ink[2] - ink[0], ink[3] - ink[1]
    fill_h = ih / bh
    # Height still hugs the ink (the old bug was 56%); width belongs to the
    # full-width box now, checked in test_full_width_box_spans_the_frame.
    assert fill_h >= FILL_MIN, f"{hook!r}: ink fills only {fill_h:.0%} of box height"

    bcx, bcy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    icx, icy = (ink[0] + ink[2]) / 2, (ink[1] + ink[3]) / 2
    assert abs(icx - bcx) <= CENTER_TOL, f"{hook!r}: not h-centred ({abs(icx-bcx):.0f}px)"
    assert abs(icy - bcy) <= CENTER_TOL, f"{hook!r}: not v-centred ({abs(icy-bcy):.0f}px)"

    assert ink[0] >= 0 and ink[2] <= cfg.canvas_w, f"{hook!r}: ink overflows frame width"


@requires_render
def test_long_hook_text_is_substantial_width():
    """A long hook must still grow to a big two-line block (not shrink to tiny)."""
    cfg = ReelFinishConfig(divider_y=785)
    png = _render_banner("COLLABORATE WITH AI, DONT DELEGATE.", cfg)
    _, ink = _extents(png)
    avail_w = cfg.canvas_w - 2 * cfg.banner_side_margin_px - 2 * cfg.banner_pad_x
    assert (ink[2] - ink[0]) >= 0.85 * avail_w   # widest line ~fills the text area


@requires_render
def test_full_width_box_spans_the_frame():
    """Default banner_full_width → box spans (nearly) edge-to-edge, so no footage
    bleeds beside it in the divider band."""
    cfg = ReelFinishConfig(divider_y=785)
    assert cfg.banner_full_width is True
    png = _render_banner("COLLABORATE WITH AI, DONT DELEGATE.", cfg)
    box, _ = _extents(png)
    inset = cfg.banner_box_margin_x
    assert box[0] <= inset + 6
    assert box[2] >= cfg.canvas_w - inset - 6


@requires_render
def test_hugging_box_respects_side_margins_when_not_full_width():
    cfg = ReelFinishConfig(divider_y=785, banner_full_width=False)
    png = _render_banner("COLLABORATE WITH AI, DONT DELEGATE.", cfg)
    box, _ = _extents(png)
    assert box[0] >= cfg.banner_side_margin_px - 6
    assert box[2] <= cfg.canvas_w - cfg.banner_side_margin_px + 6
