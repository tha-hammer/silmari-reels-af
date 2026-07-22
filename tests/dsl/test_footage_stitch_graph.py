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
    ThreePhaseUnsupportedError,
    _body_extract_cmd,
    _concat_cmd,
    _concat_list_text,
    _ffmpeg_cmd,
    _fold_cmd,
    _normalize_cmd,
    _transition_clip_cmd,
    _video_encode_opts,
    build_footage_filtergraph,
    plan_pairwise_stitch,
    plan_three_phase_stitch,
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


# ── AF-77e · three-phase normalize → transition-only → concat-copy ─


def _two_segment_reel(effect: str, duration_s: float, *, audio_fade: bool = True,
                      total: float) -> FootageReel:
    return FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(segment_id="seg-1", source_url="fixture", start_s=0.0, end_s=1.0, text="one"),
            SourceSegment(segment_id="seg-2", source_url="fixture", start_s=1.0, end_s=2.0, text="two"),
        ],
        transitions=[
            Transition(before_index=0, after_index=1, effect=effect,
                       duration_s=duration_s, audio_fade=audio_fade),
        ],
        duration_s=total,
    )


def test_three_phase_plan_geometry():
    """B1: bodies + isolated transition windows tile the reel exactly."""
    plan = plan_three_phase_stitch(_three_segment_reel(), _three_segment_assets())
    assert [s.kind for s in plan.norm_steps] == ["source", "black", "source"]
    assert len(plan.transition_steps) == 1
    step = plan.transition_steps[0]
    assert (step.position, step.left_idx, step.right_idx) == (2, 1, 2)
    assert step.effect == "dissolve"
    assert step.transition_duration_s == pytest.approx(0.2)
    # dissolve window comes from the black segment's own tail (0.5 - 0.2)
    assert step.left_tail_offset_s == pytest.approx(0.3)
    assert step.result_duration_s == pytest.approx(0.2)
    got = [(e.kind, e.index, e.inpoint_s, e.duration_s) for e in plan.concat_entries]
    assert got == [
        ("body", 0, pytest.approx(0.0), pytest.approx(1.0)),
        ("body", 1, pytest.approx(0.0), pytest.approx(0.3)),
        ("transition", 2, pytest.approx(0.0), pytest.approx(0.2)),
        ("body", 2, pytest.approx(0.2), pytest.approx(0.8)),
    ]
    assert plan.total_duration_s == pytest.approx(2.3)
    assert sum(e.duration_s for e in plan.concat_entries) == pytest.approx(2.3)


def test_three_phase_keyframes_only_at_nonzero_body_inpoints():
    """B2: stream-copy body cuts need a keyframe exactly at the inpoint."""
    plan = plan_three_phase_stitch(_three_segment_reel(), _three_segment_assets())
    assert plan.keyframe_times == ((), (), (pytest.approx(0.2),))


def test_three_phase_all_none_is_pure_concat():
    """B3: no transition clips — whole normalized segments concat-copied."""
    reel = _two_segment_reel("none", 0.0, total=2.0)
    plan = plan_three_phase_stitch(reel, _three_segment_assets())
    assert plan.transition_steps == ()
    got = [(e.kind, e.index, e.inpoint_s, e.duration_s) for e in plan.concat_entries]
    assert got == [
        ("body", 0, pytest.approx(0.0), pytest.approx(1.0)),
        ("body", 1, pytest.approx(0.0), pytest.approx(1.0)),
    ]


def test_three_phase_fade_to_color_window_is_two_d():
    """B4: fade-to-color renders fade-out(D) + fade-in(D) → a 2D clip."""
    reel = _two_segment_reel("fadeblack", 0.3, total=2.0)
    plan = plan_three_phase_stitch(reel, _three_segment_assets())
    step = plan.transition_steps[0]
    assert step.result_duration_s == pytest.approx(0.6)
    got = [(e.kind, e.index, e.inpoint_s, e.duration_s) for e in plan.concat_entries]
    assert got == [
        ("body", 0, pytest.approx(0.0), pytest.approx(0.7)),
        ("transition", 1, pytest.approx(0.0), pytest.approx(0.6)),
        ("body", 1, pytest.approx(0.3), pytest.approx(0.7)),
    ]
    assert sum(e.duration_s for e in plan.concat_entries) == pytest.approx(2.0)


def test_three_phase_single_segment():
    """B5: one segment → one body entry, no transitions."""
    plan = plan_three_phase_stitch(
        _single_source_reel(), {"seg-1": _pre_normalized_asset(False)}
    )
    assert plan.transition_steps == ()
    assert [(e.kind, e.index) for e in plan.concat_entries] == [("body", 0)]


def _overlap_window_reel() -> FootageReel:
    """Pairwise-valid reel whose middle segment (0.4s) cannot host both its
    incoming and outgoing 0.3s dissolve windows (0.6s > 0.4s)."""
    return FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(segment_id="seg-1", source_url="fixture", start_s=0.0, end_s=1.0, text="one"),
            BlackSegment(duration_s=0.4),
            SourceSegment(segment_id="seg-2", source_url="fixture", start_s=1.0, end_s=2.0, text="two"),
        ],
        transitions=[
            Transition(before_index=0, after_index=1, effect="dissolve", duration_s=0.3),
            Transition(before_index=1, after_index=2, effect="dissolve", duration_s=0.3),
        ],
        duration_s=1.8,
    )


def test_three_phase_rejects_overlapping_windows():
    """B6: middle segment shorter than head+tail windows → unsupported."""
    with pytest.raises(ThreePhaseUnsupportedError):
        plan_three_phase_stitch(_overlap_window_reel(), _three_segment_assets())


def test_overlap_reel_stays_pairwise_valid():
    """B7: the overlap reel is fully valid for the pairwise fold — three-phase
    unsupportedness must trigger a fallback, never a rejection."""
    plan = plan_pairwise_stitch(_overlap_window_reel(), _three_segment_assets())
    assert len(plan.fold_steps) == 2
    assert plan.total_duration_s == pytest.approx(1.8)


def test_encode_opts_carry_closed_gop():
    """B9: deterministic keyframe cadence — closed GOP for exact copy cuts."""
    opts = _video_encode_opts()
    joined = " ".join(opts)
    assert "-g 60" in joined
    assert "-keyint_min 60" in joined
    assert "-sc_threshold 0" in joined


def test_normalize_cmd_forces_keyframes_at_body_inpoints():
    """B10: forced keyframe where the body stream-copy cut begins."""
    plan = plan_three_phase_stitch(_three_segment_reel(), _three_segment_assets())
    step = plan.norm_steps[2]
    cmd = _normalize_cmd(step, Path("/tmp/n.mp4"), keyframe_times=(0.2,))
    assert cmd[cmd.index("-force_key_frames") + 1] == "0.200"
    assert cmd.count("-i") <= 1
    # default stays keyframe-free (pairwise callers unchanged)
    assert "-force_key_frames" not in _normalize_cmd(step, Path("/tmp/n.mp4"))


def test_transition_clip_cmd_decodes_windows_only():
    """B11: 2 inputs, left tail via input seek, xfade at offset 0, acrossfade."""
    plan = plan_three_phase_stitch(_three_segment_reel(), _three_segment_assets())
    step = plan.transition_steps[0]
    cmd = _transition_clip_cmd(Path("/tmp/l.mp4"), Path("/tmp/r.mp4"), step, Path("/tmp/t.mp4"))
    assert cmd.count("-i") == 2
    assert cmd[cmd.index("-ss") + 1] == "0.300"
    joined = " ".join(cmd)
    assert "xfade=transition=dissolve:duration=0.200:offset=0.000" in joined
    assert "acrossfade=d=0.200" in joined


def test_transition_clip_cmd_hard_audio_cut_uses_right_head():
    """B12: audio_fade=False keeps the pairwise hard cut — the window's audio
    is the right head only (left body already carries audio to the cut)."""
    reel = _two_segment_reel("dissolve", 0.2, audio_fade=False, total=1.8)
    plan = plan_three_phase_stitch(reel, _three_segment_assets())
    step = plan.transition_steps[0]
    cmd = _transition_clip_cmd(Path("/tmp/l.mp4"), Path("/tmp/r.mp4"), step, Path("/tmp/t.mp4"))
    joined = " ".join(cmd)
    assert "acrossfade" not in joined
    assert "[1:a]atrim=end=0.200" in joined


def test_transition_clip_cmd_fade_to_color():
    """B13: fade-out + fade-in + concat, white for fadewhite."""
    reel = _two_segment_reel("fadewhite", 0.3, total=2.0)
    plan = plan_three_phase_stitch(reel, _three_segment_assets())
    step = plan.transition_steps[0]
    cmd = _transition_clip_cmd(Path("/tmp/l.mp4"), Path("/tmp/r.mp4"), step, Path("/tmp/t.mp4"))
    joined = " ".join(cmd)
    assert "fade=t=out:st=0.000:d=0.300:color=white" in joined
    assert "fade=t=in:st=0:d=0.300:color=white" in joined
    assert "concat=n=2:v=1:a=0[v]" in joined
    assert "afade=t=out" in joined and "afade=t=in" in joined


def test_body_extract_cmd_stream_copies():
    """B14: bodies are remuxed (-c copy), never re-encoded."""
    plan = plan_three_phase_stitch(_three_segment_reel(), _three_segment_assets())
    entry = plan.concat_entries[-1]  # body 2: inpoint 0.2, dur 0.8
    cmd = _body_extract_cmd(Path("/tmp/n.mp4"), entry, Path("/tmp/b.mp4"))
    assert cmd[cmd.index("-ss") + 1] == "0.200"
    assert cmd[cmd.index("-t") + 1] == "0.800"
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "libx264" not in cmd


def test_concat_list_text_quotes_paths():
    """B15: concat demuxer list format with single-quote escaping."""
    text = _concat_list_text([Path("/tmp/a.mp4"), Path("/tmp/it's.mp4")])
    assert text.splitlines() == [
        "file '/tmp/a.mp4'",
        "file '/tmp/it'\\''s.mp4'",
    ]


def test_concat_cmd_copies_without_reencode():
    """B16: the assemble pass is -f concat -c copy — zero re-encode."""
    cmd = _concat_cmd(Path("/tmp/list.txt"), Path("/tmp/out.mp4"))
    assert cmd[cmd.index("-f") + 1] == "concat"
    assert cmd[cmd.index("-safe") + 1] == "0"
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "libx264" not in cmd


def _install_fake_ffmpeg(monkeypatch) -> list[list[str]]:
    """Capture every ffmpeg invocation and materialize its output file."""
    import reel_af.render.footage_stitch as fs

    captured: list[list[str]] = []

    async def fake_run(cmd, *, timeout_s):
        captured.append([str(part) for part in cmd])
        Path(cmd[-1]).write_bytes(b"stub")

    monkeypatch.setattr(fs, "_run_ffmpeg", fake_run)
    return captured


@pytest.mark.asyncio
async def test_stitch_uses_three_phase_when_supported(monkeypatch, tmp_path):
    """B17: supported reel → normalize/transition/body/concat, no pairwise folds."""
    from reel_af.render.footage_stitch import stitch_footage_reel

    captured = _install_fake_ffmpeg(monkeypatch)
    out = await stitch_footage_reel(
        _three_segment_reel(), _three_segment_assets(), tmp_path / "out", "run-3p"
    )
    assert out.exists()
    outputs = [cmd[-1] for cmd in captured]
    assert not any("fold-" in path for path in outputs)
    assert sum("norm-" in path for path in outputs) == 3
    assert sum("trans-" in path for path in outputs) == 1
    assert any("-f" in cmd and "concat" in cmd for cmd in captured)


@pytest.mark.asyncio
async def test_stitch_falls_back_to_pairwise_when_unsupported(monkeypatch, tmp_path):
    """B18: overlapping windows → the pairwise fold still renders."""
    from reel_af.render.footage_stitch import stitch_footage_reel

    captured = _install_fake_ffmpeg(monkeypatch)
    out = await stitch_footage_reel(
        _overlap_window_reel(), _three_segment_assets(), tmp_path / "out", "run-fb"
    )
    assert out.exists()
    outputs = [cmd[-1] for cmd in captured]
    assert sum("fold-" in path for path in outputs) == 2
    assert not any("-f" in cmd and "concat" in cmd for cmd in captured)
