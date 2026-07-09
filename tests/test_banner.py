"""B4 banner ASS — the two-line boxed hook at the divider (V3, user-chosen).

The hook is balanced-wrapped to ≤2 lines and drawn as a white ``BannerBox``
rectangle with the purple ``Banner`` text ink-centred on the divider. These
tests assert structure/placement; ``test_banner_fill.py`` asserts the rendered
pixels actually fill + centre in the box.
"""

from __future__ import annotations

from util_captions import StubFinishConfig, parse_dialogues

from reel_af.render import captions


def _banner_text(ass_text: str):
    return [d for d in parse_dialogues(ass_text) if d.style == captions.BANNER_STYLE_NAME]


def _banner_box(ass_text: str):
    return [d for d in parse_dialogues(ass_text) if d.style == captions.BANNER_BOX_STYLE_NAME]


def _flat(text: str) -> str:
    """Join wrapped lines (the ``\\N`` separator) back into one string."""
    return text.replace("\\N", " ").strip()


def test_build_banner_ass_spans_full_duration():
    cfg = StubFinishConfig()
    ass = captions.build_banner_ass("the hook", dur=30.0, cfg=cfg)
    text = _banner_text(ass)
    box = _banner_box(ass)
    assert len(text) == 1 and len(box) == 1
    for d in (text[0], box[0]):
        assert d.start == 0.0
        assert abs(d.end - 30.0) < 0.05


def test_build_banner_ass_centres_box_on_divider():
    cfg = StubFinishConfig(divider_y=772, center_x=540)
    ass = captions.build_banner_ass("hook", dur=10.0, cfg=cfg)
    text = _banner_text(ass)[0]
    # text is horizontally centred; ink is vertically centred ON the divider,
    # so the text \pos.y sits within a line-height of divider_y.
    assert text.x == 540
    assert abs(text.y - 772) <= 60


def test_build_banner_ass_divider_override_moves_pos_by_delta():
    # Same hook → same centring offset, so the y-delta equals the divider delta.
    low = _banner_text(captions.build_banner_ass("h", 10.0, StubFinishConfig(divider_y=772)))[0]
    high = _banner_text(captions.build_banner_ass("h", 10.0, StubFinishConfig(divider_y=600)))[0]
    assert low.y - high.y == 772 - 600


def test_build_banner_ass_uppercases_by_default():
    cfg = StubFinishConfig(banner_uppercase=True)
    d = _banner_text(captions.build_banner_ass("stop telling ai what to do", 5.0, cfg))[0]
    assert _flat(d.text) == "STOP TELLING AI WHAT TO DO"


def test_build_banner_ass_respects_lowercase_opt_out():
    cfg = StubFinishConfig(banner_uppercase=False)
    d = _banner_text(captions.build_banner_ass("Keep Case", 5.0, cfg))[0]
    assert _flat(d.text) == "Keep Case"


def test_build_banner_ass_wraps_multiword_hook_to_two_lines():
    cfg = StubFinishConfig()
    d = _banner_text(captions.build_banner_ass("collaborate with ai do not delegate", 5.0, cfg))[0]
    assert "\\N" in d.text                       # wrapped
    assert d.text.count("\\N") == 1              # exactly two lines


def test_build_banner_ass_single_word_stays_one_line():
    cfg = StubFinishConfig()
    d = _banner_text(captions.build_banner_ass("stupid", 5.0, cfg))[0]
    assert "\\N" not in d.text


def test_build_banner_ass_declares_box_and_text_styles():
    cfg = StubFinishConfig()
    ass = captions.build_banner_ass("hook", 5.0, cfg)
    assert ass.count(f"Style: {captions.BANNER_BOX_STYLE_NAME},") == 1
    assert ass.count(f"Style: {captions.BANNER_STYLE_NAME},") == 1
    # box style fill is white (opaque); text style fill is purple.
    box_line = next(ln for ln in ass.splitlines() if ln.startswith(f"Style: {captions.BANNER_BOX_STYLE_NAME},"))
    text_line = next(
        ln for ln in ass.splitlines()
        if ln.startswith(f"Style: {captions.BANNER_STYLE_NAME},")
        and not ln.startswith(f"Style: {captions.BANNER_BOX_STYLE_NAME},")
    )
    assert box_line.split(",")[3] == "&H00FFFFFF"     # PrimaryColour (box fill)
    assert text_line.split(",")[3] == "&H00CE227E"    # purple text


def test_build_banner_ass_box_is_a_drawing():
    cfg = StubFinishConfig()
    ass = captions.build_banner_ass("hook", 5.0, cfg)
    box_dialogue = next(ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",BannerBox," in ln)
    assert "\\p1" in box_dialogue and "\\p0" in box_dialogue   # ASS vector drawing
    assert "m 0 0 l " in box_dialogue                           # rectangle path


def test_build_banner_ass_escapes_braces():
    cfg = StubFinishConfig(banner_uppercase=False)
    d = _banner_text(captions.build_banner_ass("a{x}b", 5.0, cfg))[0]
    assert "{" not in d.text and "}" not in d.text


def test_build_finish_ass_combines_banner_and_captions():
    """The single ASS that finish_reel (B9) burns — banner (box+text) + N captions."""
    cfg = StubFinishConfig()
    words = [(0.0, 0.3, "hello"), (0.3, 0.6, "world"), (0.9, 1.2, "again")]
    ass = captions.build_finish_ass(words, hook="my hook", dur=12.0, cfg=cfg)
    banners = _banner_text(ass)
    boxes = _banner_box(ass)
    caps = [d for d in parse_dialogues(ass) if d.style == captions.CAPTION_STYLE_NAME]
    assert len(banners) == 1 and len(boxes) == 1
    assert caps and all(d.y == cfg.caption_safe_y for d in caps)
    # each style declared exactly once
    assert ass.count(f"Style: {captions.BANNER_STYLE_NAME},") == 1
    assert ass.count(f"Style: {captions.BANNER_BOX_STYLE_NAME},") == 1
    assert ass.count(f"Style: {captions.CAPTION_STYLE_NAME},") == 1
