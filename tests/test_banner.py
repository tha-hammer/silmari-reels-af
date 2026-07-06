"""B4 banner ASS from hook — one full-duration boxed lime line at the divider.

Copies the proven ``Banner`` style + ``\\pos(540, divider_y)`` placement from
the delivered ``enhance_reel.py`` driver, made config-driven.
"""

from __future__ import annotations

from util_captions import StubFinishConfig, parse_dialogues

from reel_af.render import captions


def _banner_dialogues(ass_text: str):
    return [d for d in parse_dialogues(ass_text) if d.style == captions.BANNER_STYLE_NAME]


def test_build_banner_ass_spans_full_duration():
    cfg = StubFinishConfig()
    ass = captions.build_banner_ass("the hook", dur=30.0, cfg=cfg)
    dials = _banner_dialogues(ass)
    assert len(dials) == 1
    assert dials[0].start == 0.0
    assert abs(dials[0].end - 30.0) < 0.05


def test_build_banner_ass_positions_at_divider():
    cfg = StubFinishConfig(divider_y=772, center_x=540)
    dials = _banner_dialogues(captions.build_banner_ass("hook", dur=10.0, cfg=cfg))
    assert dials[0].x == 540
    assert dials[0].y == 772


def test_build_banner_ass_divider_override_moves_pos():
    low = _banner_dialogues(captions.build_banner_ass("h", 10.0, StubFinishConfig(divider_y=772)))
    high = _banner_dialogues(captions.build_banner_ass("h", 10.0, StubFinishConfig(divider_y=600)))
    assert low[0].y == 772
    assert high[0].y == 600


def test_build_banner_ass_uppercases_by_default():
    cfg = StubFinishConfig(banner_uppercase=True)
    dials = _banner_dialogues(captions.build_banner_ass("stop telling ai what to do", 5.0, cfg))
    assert dials[0].text.strip() == "STOP TELLING AI WHAT TO DO"


def test_build_banner_ass_respects_lowercase_opt_out():
    cfg = StubFinishConfig(banner_uppercase=False)
    dials = _banner_dialogues(captions.build_banner_ass("Keep Case", 5.0, cfg))
    assert dials[0].text.strip() == "Keep Case"


def test_build_banner_ass_declares_boxed_banner_style():
    cfg = StubFinishConfig()
    ass = captions.build_banner_ass("hook", 5.0, cfg)
    assert f"Style: {captions.BANNER_STYLE_NAME}," in ass
    # BorderStyle 3 == opaque box behind the text (proven banner look)
    style_line = next(
        ln for ln in ass.splitlines() if ln.startswith(f"Style: {captions.BANNER_STYLE_NAME},")
    )
    fields = style_line.split(",")
    # fields[0] is "Style: Banner" (Name), so the columns shift left by one vs
    # the Format header: Fontname[1] Fontsize[2] ... Angle[14] BorderStyle[15].
    assert fields[15].strip() == "3"


def test_build_banner_ass_escapes_braces():
    cfg = StubFinishConfig(banner_uppercase=False)
    dials = _banner_dialogues(captions.build_banner_ass("a{x}b", 5.0, cfg))
    assert "{" not in dials[0].text and "}" not in dials[0].text


def test_build_finish_ass_combines_banner_and_captions():
    """The single ASS that finish_reel (B9) burns — one banner + N captions."""
    cfg = StubFinishConfig()
    words = [(0.0, 0.3, "hello"), (0.3, 0.6, "world"), (0.9, 1.2, "again")]
    ass = captions.build_finish_ass(words, hook="my hook", dur=12.0, cfg=cfg)
    banners = [d for d in parse_dialogues(ass) if d.style == captions.BANNER_STYLE_NAME]
    caps = [d for d in parse_dialogues(ass) if d.style == captions.CAPTION_STYLE_NAME]
    assert len(banners) == 1
    assert banners[0].y == cfg.divider_y
    assert caps and all(d.y == cfg.caption_safe_y for d in caps)
    # both styles declared exactly once
    assert ass.count(f"Style: {captions.BANNER_STYLE_NAME},") == 1
    assert ass.count(f"Style: {captions.CAPTION_STYLE_NAME},") == 1
