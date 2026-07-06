from __future__ import annotations

import pytest
from pydantic import ValidationError

from reel_af.render.overlays import (
    CutInOverlay,
    build_overlay_filtergraph,
    visual_prompts,
    zoom_crop_xy,
)


def test_overlay_graph_builds_zoom_and_visual_cut_ins():
    graph = build_overlay_filtergraph(
        [
            {"type": "zoom", "at_s": 10.0, "until_s": 11.0, "zoom_focus": "upper"},
            {
                "type": "visual",
                "at_s": 11.0,
                "until_s": 12.0,
                "image_prompt": "abstract architecture diagram",
            },
        ],
        segment_start_s=9.5,
        segment_duration_s=3.0,
    )

    assert graph.visual_input_count == 1
    assert "[base_src]split=2[base][z0]" in graph.filter_complex
    assert "crop=1080:1920:270:115" in graph.filter_complex
    assert "[1:v]scale=1080:1920" in graph.filter_complex
    assert "overlay=enable='between(t,0.500,1.500)'" in graph.filter_complex
    assert "overlay=enable='between(t,1.500,2.500)'" in graph.filter_complex


def test_visual_prompts_follow_sorted_visual_cut_in_order():
    prompts = visual_prompts(
        [
            {"type": "visual", "at_s": 20.0, "until_s": 21.0, "image_prompt": "second"},
            {"type": "zoom", "at_s": 5.0, "until_s": 6.0},
            {"type": "visual", "at_s": 10.0, "until_s": 11.0, "image_prompt": "first"},
        ]
    )

    assert prompts == ["first", "second"]


def test_visual_cut_in_requires_prompt():
    with pytest.raises(ValidationError, match="image_prompt"):
        CutInOverlay(type="visual", at_s=1.0, until_s=2.0)


def test_zoom_crop_focus_falls_back_to_center():
    assert zoom_crop_xy("left")[0] == 0
    assert zoom_crop_xy("unknown") == zoom_crop_xy("center")
