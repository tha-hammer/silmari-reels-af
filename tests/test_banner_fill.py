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
            elif r > 40 and b > 40 and r > g and b > g and (r > g + 15 or b > g + 15):
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

FILL_MIN = 0.68          # text fills ≥68% of the box on its binding axis
CENTER_TOL = 22          # ink centre within 22px of box centre on each axis


@requires_render
@pytest.mark.parametrize("hook", HOOKS)
def test_text_fills_box_and_is_centred(hook):
    """The text fills the fixed box (on whichever axis binds) and is centred.

    The old bug was a tiny hook floating in the box; here the fit maximises the
    font, so at least one axis is well filled and the ink is centred.
    """
    cfg = ReelFinishConfig(divider_y=785)
    png = _render_banner(hook, cfg)
    box, ink = _extents(png)
    assert box is not None and ink is not None

    bw, bh = box[2] - box[0], box[3] - box[1]
    iw, ih = ink[2] - ink[0], ink[3] - ink[1]
    fill = max(iw / bw, ih / bh)
    assert fill >= FILL_MIN, f"{hook!r}: text fills only {fill:.0%} of the box"

    bcx, bcy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    icx, icy = (ink[0] + ink[2]) / 2, (ink[1] + ink[3]) / 2
    assert abs(icx - bcx) <= CENTER_TOL, f"{hook!r}: not h-centred ({abs(icx-bcx):.0f}px)"
    assert abs(icy - bcy) <= CENTER_TOL, f"{hook!r}: not v-centred ({abs(icy-bcy):.0f}px)"
    assert ink[0] >= 0 and ink[2] <= cfg.canvas_w, f"{hook!r}: ink overflows frame width"


@requires_render
def test_box_is_full_width_for_every_hook():
    """The box spans the full frame width for every hook (no footage bleed)."""
    cfg = ReelFinishConfig(divider_y=785)
    for hook in HOOKS:
        box, _ = _extents(_render_banner(hook, cfg))
        assert abs((box[2] - box[0]) - cfg.canvas_w) <= 6, f"{hook!r}: box not full width"


@requires_render
@pytest.mark.parametrize("hook", HOOKS)
def test_text_fills_box_to_target_padding(hook):
    """THE invariant: the text fills the box down to ~banner_pad on every side.

    The box height hugs the text, so padding is the configured pad (±slack for
    antialiasing / the pixel detector), not a big floating gap.
    """
    cfg = ReelFinishConfig(divider_y=785)
    box, ink = _extents(_render_banner(hook, cfg))
    assert box is not None and ink is not None
    pad_t = ink[1] - box[1]
    pad_b = box[3] - ink[3]
    pad_l = ink[0] - box[0]
    pad_r = box[2] - ink[2]
    lo, hi = cfg.banner_pad_y - 12, cfg.banner_pad_y + 18
    for name, p in [("top", pad_t), ("bottom", pad_b), ("left", pad_l), ("right", pad_r)]:
        assert lo <= p <= hi, f"{hook!r}: {name} padding {p}px not ≈{cfg.banner_pad_y}px"
