"""Remotion effect-prop emission from a merged cfg (plan Behavior 3).

Both overlays turn the (snake_case) tuned cfg into the camelCase Remotion props
the compositions read. ``render_overlay`` writes a ``props.json`` sidecar;
``render_lower_third`` passes ``--props`` inline. Both go through an injected
``runner`` so we capture the emitted payload with no Node/Chromium subprocess.
"""

from __future__ import annotations

import json

from reel_af.render import lower_third, middle_third


class _Capture:
    """A ``subprocess.run`` stand-in that records argv without executing."""

    def __init__(self):
        self.cmd = None

    def __call__(self, cmd, **kwargs):
        self.cmd = cmd
        return None


def _inline_props(cmd) -> dict:
    prop_arg = next(a for a in cmd if a.startswith("--props="))
    return json.loads(prop_arg[len("--props=") :])


def test_middle_third_props_json_carries_camelcase_effect_props(tmp_path):
    cfg = {
        "overlay_accent": "#00E5FF",
        "remotion_composition": "MiddleThird",
        "overlay_vertical_anchor": 0.4,
        "font_scale": 1.5,
        "card_opacity": 0.7,
        "accent_bar_px": 12,
        "corner_radius": 30,
        "anim_style": "fade",
        "anim_damping": 120,
        "anim_mass": 1.2,
        "phrase_uppercase": True,
    }
    seq = tmp_path / "reel01" / "seq"
    middle_third.render_overlay(
        [{"text": "hi", "from": 0, "durationInFrames": 30}],
        100, seq, cfg, runner=_Capture(),
    )
    props = json.loads((tmp_path / "reel01" / "props.json").read_text())

    assert props["accent"] == "#00E5FF"
    assert props["verticalAnchor"] == 0.4
    assert props["fontScale"] == 1.5
    assert props["cardOpacity"] == 0.7
    assert props["accentBarPx"] == 12
    assert props["cornerRadius"] == 30
    assert props["anim"] == "fade"
    assert props["animDamping"] == 120.0
    assert props["animMass"] == 1.2
    assert props["textTransform"] == "uppercase"


def test_middle_third_untuned_cfg_omits_effect_props(tmp_path):
    """An un-tuned preset emits only the base props — the composition defaults fill
    the rest, keeping the render pixel-identical to before."""
    cfg = {
        "overlay_accent": "#7E22CE",
        "remotion_composition": "MiddleThird",
        "overlay_vertical_anchor": 0.32,
        "phrase_uppercase": False,
    }
    seq = tmp_path / "reel01" / "seq"
    middle_third.render_overlay([], 60, seq, cfg, runner=_Capture())
    props = json.loads((tmp_path / "reel01" / "props.json").read_text())

    assert set(props) == {"accent", "segments", "totalFrames", "verticalAnchor", "textTransform"}
    assert props["textTransform"] == "none"


def test_lower_third_inline_props_carry_effect_props(tmp_path):
    cfg = {
        "overlay_accent": "#00E5FF",
        "font_scale": 1.2,
        "box_opacity": 0.8,
        "accent_bar_px": 10,
        "corner_radius": 16,
        "anim_style": "slide",
        "anim_damping": 150,
        "anim_mass": 0.9,
    }
    cap = _Capture()
    lower_third.render_lower_third(
        "My Title", tmp_path / "lt" / "seq", cfg=cfg, runner=cap,
    )
    props = _inline_props(cap.cmd)

    assert props["title"] == "My Title"
    assert props["accent"] == "#00E5FF"
    assert props["fontScale"] == 1.2
    assert props["boxOpacity"] == 0.8
    assert props["accentBarPx"] == 10
    assert props["cornerRadius"] == 16
    assert props["anim"] == "slide"
    assert props["animDamping"] == 150.0
    assert props["animMass"] == 0.9


def test_lower_third_untuned_cfg_emits_only_title_and_accent(tmp_path):
    cap = _Capture()
    lower_third.render_lower_third(
        "Plain", tmp_path / "lt" / "seq", accent="#7E22CE", cfg={}, runner=cap,
    )
    props = _inline_props(cap.cmd)
    assert props == {"title": "Plain", "accent": "#7E22CE"}
