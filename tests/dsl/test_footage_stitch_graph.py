from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.models import (
    BlackSegment,
    DownloadedSegment,
    FootageReel,
    SourceSegment,
    Transition,
)
from reel_af.render.footage_stitch import (
    FootageFilterGraph,
    SegmentAssetValidationError,
    _ffmpeg_cmd,
    _fold_cmd,
    _normalize_cmd,
    build_footage_filtergraph,
    plan_pairwise_stitch,
)


def _three_segment_reel() -> FootageReel:
    return FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(segment_id="seg-1", source_url="fixture", start_s=0.0, end_s=1.0, text="one"),
            BlackSegment(duration_s=0.5),
            SourceSegment(segment_id="seg-2", source_url="fixture", start_s=1.0, end_s=2.0, text="two"),
        ],
        transitions=[
            Transition(before_index=0, after_index=1, effect="none", duration_s=0.0),
            Transition(before_index=1, after_index=2, effect="dissolve", duration_s=0.2, audio_fade=True),
        ],
        duration_s=2.3,
    )


def _three_segment_assets() -> dict:
    return {
        "seg-1": _asset("seg-1", Path(__file__), 0.0, 1.0),
        "seg-2": _asset("seg-2", Path(__file__), 1.0, 2.0),
    }


def test_pairwise_plan_structure_and_math():
    plan = plan_pairwise_stitch(_three_segment_reel(), _three_segment_assets())
    assert [s.kind for s in plan.norm_steps] == ["source", "black", "source"]
    assert len(plan.fold_steps) == 2
    assert plan.total_duration_s == pytest.approx(2.3)
    # the dissolve folds against the accumulated (seg-1 + black = 1.5s) reel
    assert plan.fold_steps[1].effect == "dissolve"
    assert plan.fold_steps[1].current_duration_s == pytest.approx(1.5)


def test_pairwise_bounds_every_pass_to_at_most_two_inputs():
    """The fix: no ffmpeg pass opens more than 2 decoders (the single-graph OOM
    opened all N at once). Normalize = ≤1 input, each fold = exactly 2."""
    plan = plan_pairwise_stitch(_three_segment_reel(), _three_segment_assets())
    for step in plan.norm_steps:
        assert _normalize_cmd(step, Path("/tmp/n.mp4")).count("-i") <= 1
    for fold in plan.fold_steps:
        cmd = _fold_cmd(Path("/tmp/c.mp4"), Path("/tmp/x.mp4"), fold, Path("/tmp/o.mp4"), duration_clamp=None)
        assert cmd.count("-i") == 2


def test_pairwise_fold_filters_carry_transition():
    plan = plan_pairwise_stitch(_three_segment_reel(), _three_segment_assets())
    none_cmd = " ".join(
        _fold_cmd(Path("/tmp/c.mp4"), Path("/tmp/x.mp4"), plan.fold_steps[0], Path("/tmp/o.mp4"), duration_clamp=None)
    )
    assert "concat=n=2:v=1:a=0[v]" in none_cmd
    dissolve_cmd = " ".join(
        _fold_cmd(Path("/tmp/c.mp4"), Path("/tmp/x.mp4"), plan.fold_steps[1], Path("/tmp/o.mp4"), duration_clamp=None)
    )
    assert "xfade=transition=dissolve:duration=0.200:offset=1.300" in dissolve_cmd
    assert "acrossfade=d=0.200" in dissolve_cmd


def test_ffmpeg_cmd_is_memory_bounded():
    """The stitch command single-threads and trims x264 buffers so a multi-input
    filtergraph can't OOM-kill the agent (regression for the 9-input exit -9)."""
    graph = FootageFilterGraph(
        input_paths=(Path("/tmp/a.mp4"), Path("/tmp/b.mp4")),
        filter_complex="[0:v]null[v0];[1:v]null[v1];[v0][v1]concat=n=2:v=1:a=0[v]",
        video_label="[v]",
        audio_label="[a]",
        duration_s=3.0,
    )
    cmd = _ffmpeg_cmd(graph, Path("/tmp/out.mp4"))
    # single-threading kills the per-thread buffer pools (dominant on a many-core box)
    assert cmd[cmd.index("-threads") + 1] == "1"
    assert cmd[cmd.index("-filter_threads") + 1] == "1"
    assert cmd[cmd.index("-filter_complex_threads") + 1] == "1"
    # short lookahead / no B-frames / single ref removes the largest encoder buffers
    assert "rc-lookahead=5:bframes=0:ref=1" in cmd
    # still a real stitch: both inputs and the graph are present
    assert cmd.count("-i") == 2
    assert graph.filter_complex in cmd


def _asset(segment_id: str, path: Path, start_s: float, end_s: float) -> DownloadedSegment:
    return DownloadedSegment(
        segment_id=segment_id,
        path=path,
        source_start_s=start_s,
        source_end_s=end_s,
    )


def test_graph_emits_trim_black_xfade_and_acrossfade():
    reel = FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(
                segment_id="seg-1",
                source_url="fixture",
                start_s=0.0,
                end_s=1.0,
                text="one",
            ),
            BlackSegment(duration_s=0.5),
            SourceSegment(
                segment_id="seg-2",
                source_url="fixture",
                start_s=1.0,
                end_s=2.0,
                text="two",
            ),
        ],
        transitions=[
            Transition(before_index=0, after_index=1, effect="none", duration_s=0.0),
            Transition(
                before_index=1,
                after_index=2,
                effect="dissolve",
                duration_s=0.2,
                audio_fade=True,
            ),
        ],
        duration_s=2.3,
    )
    graph = build_footage_filtergraph(
        reel,
        {
            "seg-1": _asset("seg-1", Path(__file__), 0.0, 1.0),
            "seg-2": _asset("seg-2", Path(__file__), 1.0, 2.0),
        },
    )

    assert "trim=start=0.000:end=1.000,setpts=PTS-STARTPTS" in graph.filter_complex
    assert "color=c=black" in graph.filter_complex
    assert "concat=n=2:v=1:a=0" in graph.filter_complex
    assert "xfade=transition=dissolve:duration=0.200:offset=1.300" in graph.filter_complex
    assert "acrossfade=d=0.200" in graph.filter_complex
    assert graph.duration_s == pytest.approx(2.3)


def test_graph_uses_hard_audio_cut_when_audio_fade_is_false():
    reel = FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(
                segment_id="seg-1",
                source_url="fixture",
                start_s=0.0,
                end_s=1.0,
                text="one",
            ),
            SourceSegment(
                segment_id="seg-2",
                source_url="fixture",
                start_s=1.0,
                end_s=2.0,
                text="two",
            ),
        ],
        transitions=[
            Transition(
                before_index=0,
                after_index=1,
                effect="dissolve",
                duration_s=0.2,
                audio_fade=False,
            )
        ],
        duration_s=1.8,
    )
    graph = build_footage_filtergraph(
        reel,
        {
            "seg-1": _asset("seg-1", Path(__file__), 0.0, 1.0),
            "seg-2": _asset("seg-2", Path(__file__), 1.0, 2.0),
        },
    )

    assert "acrossfade" not in graph.filter_complex
    assert "atrim=duration=0.800" in graph.filter_complex
    assert "[ax1cut][a1]concat=n=2:v=0:a=1[ax1]" in graph.filter_complex


def test_graph_rejects_transition_duration_longer_than_adjacent_clip():
    reel = FootageReel.model_construct(
        source_url="fixture",
        segments=[
            SourceSegment(
                segment_id="seg-1",
                source_url="fixture",
                start_s=0.0,
                end_s=1.0,
                text="one",
            ),
            SourceSegment(
                segment_id="seg-2",
                source_url="fixture",
                start_s=1.0,
                end_s=2.0,
                text="two",
            ),
        ],
        transitions=[
            Transition.model_construct(
                before_index=0,
                after_index=1,
                effect="dissolve",
                duration_s=1.2,
                audio_fade=True,
            )
        ],
        duration_s=0.8,
    )

    with pytest.raises(SegmentAssetValidationError, match="must be >0"):
        build_footage_filtergraph(
            reel,
            {
                "seg-1": _asset("seg-1", Path(__file__), 0.0, 1.0),
                "seg-2": _asset("seg-2", Path(__file__), 1.0, 2.0),
            },
        )


# ── B4 · pre_normalized single-spatial-normalization guard ────────

def _single_source_reel() -> FootageReel:
    return FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(
                segment_id="seg-1",
                source_url="fixture",
                start_s=0.0,
                end_s=1.0,
                text="one",
            ),
        ],
        transitions=[],
        duration_s=1.0,
    )


def _pre_normalized_asset(pre_normalized: bool) -> DownloadedSegment:
    return DownloadedSegment(
        segment_id="seg-1",
        path=Path(__file__),
        source_start_s=0.0,
        source_end_s=1.0,
        pre_normalized=pre_normalized,
    )


def test_pre_normalized_input_skips_spatial_scale_and_crop():
    graph = build_footage_filtergraph(
        _single_source_reel(),
        {"seg-1": _pre_normalized_asset(True)},
    )
    fc = graph.filter_complex
    # timing / SAR / fps / pixel-format are still applied
    assert "trim=start=0.000:end=1.000,setpts=PTS-STARTPTS" in fc
    assert "setsar=1,fps=30,format=yuv420p[v0]" in fc
    # the second spatial normalization is gone
    assert "scale=1080:1920:force_original_aspect_ratio=increase" not in fc
    assert "crop=1080:1920" not in fc
    # audio normalization is unchanged
    assert "[0:a]atrim=start=0.000:end=1.000" in fc
    assert "aresample=48000" in fc
    assert "aformat=sample_rates=48000:channel_layouts=stereo[a0]" in fc


def test_non_pre_normalized_input_keeps_spatial_normalization():
    graph = build_footage_filtergraph(
        _single_source_reel(),
        {"seg-1": _pre_normalized_asset(False)},
    )
    fc = graph.filter_complex
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in fc
    assert "crop=1080:1920,setsar=1,fps=30,format=yuv420p[v0]" in fc
