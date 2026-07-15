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


# ── active_cut_ins_for_segment (shared active-window helper) ───────

def test_active_cut_ins_for_segment_filters_and_sorts():
    from reel_af.render.overlays import active_cut_ins_for_segment
    # segment covers source time [1.0, 3.0]
    active = active_cut_ins_for_segment(
        [
            {"type": "visual", "at_s": 0.0, "until_s": 0.4, "image_prompt": "before"},
            {"type": "visual", "at_s": 2.2, "until_s": 2.8, "image_prompt": "later"},
            {"type": "zoom", "at_s": 1.5, "until_s": 2.0},
            {"type": "visual", "at_s": 5.0, "until_s": 6.0, "image_prompt": "after"},
        ],
        1.0,
        2.0,
    )
    assert [(c.type, c.at_s) for c in active] == [("zoom", 1.5), ("visual", 2.2)]


def test_active_cut_ins_for_segment_matches_graph_visual_count():
    from reel_af.render.overlays import (
        active_cut_ins_for_segment,
        build_overlay_filtergraph,
    )
    cut_ins = [
        {"type": "visual", "at_s": 0.0, "until_s": 0.4, "image_prompt": "before"},
        {"type": "visual", "at_s": 2.2, "until_s": 2.8, "image_prompt": "later"},
    ]
    active = active_cut_ins_for_segment(cut_ins, 1.0, 2.0)
    active_visual = [c for c in active if c.type == "visual"]
    graph = build_overlay_filtergraph(
        cut_ins, segment_start_s=1.0, segment_duration_s=2.0
    )
    assert graph.visual_input_count == len(active_visual)


# ── render_overlay_clip: audio, atomic publish, cleanup ───────────

def test_build_overlay_ffmpeg_cmd_preserves_source_audio():
    from pathlib import Path

    from reel_af.render.overlays import OverlayFilterGraph, build_overlay_ffmpeg_cmd
    graph = OverlayFilterGraph(filter_complex="[0:v]null[v]", visual_input_count=0)
    cmd = build_overlay_ffmpeg_cmd(
        Path("seg.mp4"), [], graph, Path("out.mp4"), duration_s=2.0, has_audio=True
    )
    assert "anullsrc" not in " ".join(cmd)
    assert "0:a" in cmd


def test_build_overlay_ffmpeg_cmd_synthesizes_silence_when_no_audio():
    from pathlib import Path

    from reel_af.render.overlays import OverlayFilterGraph, build_overlay_ffmpeg_cmd
    graph = OverlayFilterGraph(filter_complex="[0:v]null[v]", visual_input_count=2)
    cmd = build_overlay_ffmpeg_cmd(
        Path("seg.mp4"),
        [Path("a.png"), Path("b.png")],
        graph,
        Path("out.mp4"),
        duration_s=2.0,
        has_audio=False,
    )
    joined = " ".join(cmd)
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in joined
    # inputs: 0=segment, 1,2=images, 3=anullsrc
    assert "3:a" in cmd


async def _ok_ffmpeg(cmd, *, timeout_s):
    from pathlib import Path

    Path(cmd[-1]).write_bytes(b"stub-output")


async def test_render_overlay_clip_atomic_publish_on_success(tmp_path, monkeypatch):
    from reel_af.render import overlays
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"x")
    monkeypatch.setattr(overlays, "_has_audio_stream", lambda p: True)
    monkeypatch.setattr(overlays, "_run_ffmpeg", _ok_ffmpeg)
    out = tmp_path / "out" / "seg.mp4"
    result = await overlays.render_overlay_clip(
        seg,
        [{"type": "zoom", "at_s": 0.0, "until_s": 1.0}],
        [],
        out,
        segment_start_s=0.0,
        segment_duration_s=2.0,
    )
    assert result == out
    assert out.read_bytes() == b"stub-output"
    # no leftover temp file beside the published final
    assert [p.name for p in out.parent.iterdir()] == ["seg.mp4"]


async def test_render_overlay_clip_missing_segment_raises(tmp_path):
    from reel_af.render.overlays import OverlayError, render_overlay_clip
    with pytest.raises(OverlayError):
        await render_overlay_clip(
            tmp_path / "nope.mp4",
            [],
            [],
            tmp_path / "out.mp4",
            segment_start_s=0.0,
            segment_duration_s=1.0,
        )


async def test_render_overlay_clip_visual_count_mismatch_raises(tmp_path, monkeypatch):
    from reel_af.render import overlays
    from reel_af.render.overlays import OverlayError
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"x")
    monkeypatch.setattr(overlays, "_has_audio_stream", lambda p: True)
    with pytest.raises(OverlayError):
        await overlays.render_overlay_clip(
            seg,
            [{"type": "zoom", "at_s": 0.0, "until_s": 1.0}],
            [tmp_path / "img.png"],
            tmp_path / "o.mp4",
            segment_start_s=0.0,
            segment_duration_s=2.0,
        )


async def test_render_overlay_clip_cleans_temp_and_preserves_final_on_failure(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from reel_af.render import overlays
    from reel_af.render.overlays import OverlayError
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"x")
    out = tmp_path / "out" / "seg.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"SENTINEL")
    monkeypatch.setattr(overlays, "_has_audio_stream", lambda p: False)

    async def boom(cmd, *, timeout_s):
        Path(cmd[-1]).write_bytes(b"partial")
        raise OverlayError("boom")

    monkeypatch.setattr(overlays, "_run_ffmpeg", boom)
    with pytest.raises(OverlayError):
        await overlays.render_overlay_clip(
            seg,
            [{"type": "zoom", "at_s": 0.0, "until_s": 1.0}],
            [],
            out,
            segment_start_s=0.0,
            segment_duration_s=2.0,
        )
    assert out.read_bytes() == b"SENTINEL"
    assert [p.name for p in out.parent.iterdir()] == ["seg.mp4"]
