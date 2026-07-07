"""B0 — ReelFinishConfig: the single, no-literal home for finish-stage tunables.

Every mechanic downstream (caption safe zone, banner divider, grouping
thresholds, styles, image cut-in count/region) reads its numbers from this
config so there are no magic literals in the render code. These tests pin the
proven defaults (from the validated ppWtqV0auok renders) and prove that
overriding a field actually moves the emitted output.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reel_af.render.finish_config import (
    AssStyle,
    ImageRegion,
    ReelFinishConfig,
    banner_pos_tag,
    caption_pos_tag,
)


def test_geometry_defaults_present() -> None:
    cfg = ReelFinishConfig()
    assert cfg.canvas_w == 1080
    assert cfg.canvas_h == 1920
    assert cfg.center_x == 540
    # int(0.70·H) — clears IG/Meta + YT UI; divider_y is the banner fallback.
    assert cfg.caption_safe_y == 1344
    assert cfg.divider_y == 772


def test_caption_grouping_defaults_present() -> None:
    cfg = ReelFinishConfig()
    assert cfg.caption_max_words == 4
    assert cfg.caption_max_dur_s == pytest.approx(1.8)
    assert cfg.caption_gap_s == pytest.approx(0.35)
    assert cfg.caption_uppercase is True
    assert cfg.banner_uppercase is True


def test_style_defaults_match_proven_values() -> None:
    cfg = ReelFinishConfig()
    # Caption "Cap" style — high contrast: white text in a semi-opaque dark box.
    assert cfg.caption_style.fontname == "Arial"
    assert cfg.caption_style.fontsize == 62
    assert cfg.caption_style.primary == "&H00FFFFFF"
    assert cfg.caption_style.back == "&HB0000000"
    assert cfg.caption_style.border_style == 3
    assert cfg.caption_style.outline == 4
    assert cfg.caption_style.shadow == 0
    assert cfg.caption_style.bold is True
    # Banner "Banner" style — PURPLE text on an opaque WHITE box (TASK 2).
    assert cfg.banner_style.fontname == "Arial"
    assert cfg.banner_style.fontsize == 58
    assert cfg.banner_style.primary == "&H00CE227E"
    assert cfg.banner_style.back == "&H00FFFFFF"
    assert cfg.banner_style.border_style == 3
    assert cfg.banner_style.outline == 6
    assert cfg.banner_style.shadow == 0
    assert cfg.banner_style.bold is True


def test_image_cutin_defaults_present() -> None:
    cfg = ReelFinishConfig()
    # 2-3 image cut-ins per reel over the screenshare pane (config-tunable).
    assert 2 <= cfg.image_count <= 3
    # Screenshare region: below the divider, y≈800..1920.
    assert cfg.image_region.x == 0
    assert cfg.image_region.y == 800
    assert cfg.image_region.w == 1080
    assert cfg.image_region.h == 1120
    # Cut-in duration window and edge guard (no image in first/last 2s).
    assert cfg.image_min_dur_s == pytest.approx(2.0)
    assert cfg.image_max_dur_s == pytest.approx(3.0)
    assert cfg.image_edge_guard_s == pytest.approx(2.0)


def test_caption_pos_tag_uses_config() -> None:
    cfg = ReelFinishConfig()
    assert caption_pos_tag(cfg) == r"{\pos(540,1344)}"


def test_overriding_caption_safe_y_moves_the_pos() -> None:
    cfg = ReelFinishConfig(caption_safe_y=1400)
    assert caption_pos_tag(cfg) == r"{\pos(540,1400)}"


def test_banner_pos_tag_uses_config() -> None:
    cfg = ReelFinishConfig()
    assert banner_pos_tag(cfg) == r"{\pos(540,772)}"


def test_overriding_divider_y_moves_the_banner_pos() -> None:
    cfg = ReelFinishConfig(divider_y=900)
    assert banner_pos_tag(cfg) == r"{\pos(540,900)}"


def test_overriding_center_x_moves_both_tags() -> None:
    cfg = ReelFinishConfig(center_x=600)
    assert caption_pos_tag(cfg) == r"{\pos(600,1344)}"
    assert banner_pos_tag(cfg) == r"{\pos(600,772)}"


def test_config_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReelFinishConfig(nonsense_field=1)


def test_nested_models_are_independent_instances() -> None:
    a = ReelFinishConfig()
    b = ReelFinishConfig()
    a.caption_style.fontsize = 99
    assert b.caption_style.fontsize == 62  # no shared-default aliasing


def test_can_override_nested_style_and_region() -> None:
    cfg = ReelFinishConfig(
        caption_style=AssStyle(fontname="Montserrat", fontsize=64),
        image_region=ImageRegion(x=10, y=810, w=1060, h=1100),
    )
    assert cfg.caption_style.fontname == "Montserrat"
    assert cfg.image_region.y == 810
