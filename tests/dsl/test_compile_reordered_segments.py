from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from reel_af.dsl.compile import (
    SOURCE_INTERVAL_EPSILON_S,
    _verify_no_source_interval_overlap,
    compile_composite,
    load_words,
)
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import (
    Diagnostic,
    DownloadedSegment,
    FootageReel,
    SourceRef,
    SourceSegment,
    WordsSidecar,
    validate_renderable,
)
from reel_af.render.footage_stitch import _fold_cmd, build_footage_filtergraph, plan_pairwise_stitch

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/reordered.mp4"


def _source_segments(reel: FootageReel) -> list[SourceSegment]:
    return [segment for segment in reel.segments if isinstance(segment, SourceSegment)]


def _assert_transition_indexes_adjacent(reel: FootageReel) -> None:
    assert [(t.before_index, t.after_index) for t in reel.transitions] == [
        (i, i + 1) for i in range(len(reel.segments) - 1)
    ]


def _assert_no_source_time_overlap(reel: FootageReel) -> None:
    by_source: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for segment in _source_segments(reel):
        by_source[segment.source_url].append((segment.segment_id, segment.start_s, segment.end_s))

    for intervals in by_source.values():
        intervals.sort(key=lambda item: (item[1], item[2], item[0]))
        for (_left_id, _left_start, left_end), (_right_id, right_start, _right_end) in zip(
            intervals,
            intervals[1:],
        ):
            assert left_end <= right_start + SOURCE_INTERVAL_EPSILON_S


def _asset_map_for_reel(reel: FootageReel, path: Path) -> dict[str, DownloadedSegment]:
    return {
        segment.segment_id: DownloadedSegment(
            segment_id=segment.segment_id,
            path=path,
            source_start_s=0.0,
            source_end_s=max(segment.end_s, 60.0),
        )
        for segment in _source_segments(reel)
    }


def _words_sidecar(spans: list[tuple[float, float, str]]) -> WordsSidecar:
    return WordsSidecar.model_validate({
        "schema_version": "1",
        "words": [],
        "segments": [
            {"start_s": start_s, "end_s": end_s, "text": text}
            for start_s, end_s, text in spans
        ],
    })


def _compile_text(text: str, words: WordsSidecar) -> FootageReel:
    result = compile_composite(read_composite(text), words, SourceRef(source_url=SOURCE_URL))
    assert result.status in ("ok", "warning"), [d.model_dump() for d in result.diagnostics]
    assert result.plan is not None
    return result.plan


def test_final_source_overlap_is_rejected_order_independently():
    diagnostics: list[Diagnostic] = []
    segments = [
        SourceSegment(segment_id="seg-late", source_url=SOURCE_URL, start_s=30.0, end_s=35.0, text="late"),
        SourceSegment(segment_id="seg-early", source_url=SOURCE_URL, start_s=10.0, end_s=20.0, text="early"),
        SourceSegment(segment_id="seg-mid", source_url=SOURCE_URL, start_s=18.0, end_s=25.0, text="middle"),
    ]

    assert _verify_no_source_interval_overlap(segments, diagnostics) is True

    diag = diagnostics[0]
    assert diag.code == "SOURCE_TIME_OVERLAP"
    assert diag.context["source_url"] == SOURCE_URL
    assert diag.context["left_segment_id"] == "seg-early"
    assert diag.context["right_segment_id"] == "seg-mid"
    assert diag.context["left_index"] == 1
    assert diag.context["right_index"] == 2
    assert diag.context["overlap_s"] == pytest.approx(2.0)


def test_extend_uses_source_time_neighbor_bounds():
    words = _words_sidecar([
        (10.0, 14.0, "earlier clip before the middle beat"),
        (20.0, 24.0, "middle clip that appears first"),
        (30.0, 34.0, "later clip after the middle beat"),
    ])
    text = (
        "00:00:20.000  middle clip that appears first [extend tail 10]\n"
        "00:00:10.000  earlier clip before the middle beat\n"
        "00:00:30.000  later clip after the middle beat\n"
    )

    reel = _compile_text(text, words)

    source_segments = _source_segments(reel)
    middle = source_segments[0]
    later = source_segments[2]
    assert middle.end_s == pytest.approx(later.start_s)
    _assert_no_source_time_overlap(reel)


def test_join_refuses_reordered_source_time_pair():
    words = _words_sidecar([
        (10.0, 15.0, "earlier source clip"),
        (30.0, 35.0, "later source clip"),
    ])
    text = (
        "00:00:30.000  later source clip\n"
        "\n"
        "[join]\n"
        "\n"
        "00:00:10.000  earlier source clip\n"
    )

    result = compile_composite(read_composite(text), words, SourceRef(source_url=SOURCE_URL))

    assert result.status == "error"
    assert result.plan is None
    assert any(d.code == "JOIN_REFUSED" for d in result.diagnostics)


def test_join_targets_marked_boundary_only():
    words = _words_sidecar([
        (10.0, 12.0, "first mergeable clip"),
        (12.0, 14.0, "second mergeable clip"),
        (14.0, 16.0, "third mergeable clip"),
    ])
    text = (
        "00:00:10.000  first mergeable clip\n"
        "00:00:12.000  second mergeable clip\n"
        "\n"
        "[join]\n"
        "\n"
        "00:00:14.000  third mergeable clip\n"
    )

    reel = _compile_text(text, words)

    source_segments = _source_segments(reel)
    assert len(source_segments) == 2
    assert source_segments[0].text == "first mergeable clip"
    assert source_segments[1].text == "second mergeable clip third mergeable clip"


def test_transition_marker_remaps_after_successful_join():
    words = _words_sidecar([
        (10.0, 12.0, "first joined clip"),
        (12.0, 14.0, "second joined clip"),
        (14.0, 16.0, "third surviving clip"),
    ])
    text = (
        "00:00:10.000  first joined clip\n"
        "\n"
        "[join]\n"
        "\n"
        "00:00:12.000  second joined clip\n"
        "\n"
        "[trans dissolve 0.2]\n"
        "\n"
        "00:00:14.000  third surviving clip\n"
    )

    reel = _compile_text(text, words)

    assert len(reel.segments) == 2
    assert [(t.before_index, t.after_index, t.effect, t.duration_s) for t in reel.transitions] == [
        (0, 1, "dissolve", 0.2)
    ]


def test_reordered_composite_with_crossfades_compiles():
    doc_path = FIXTURES / "reordered_segments.ts.md"
    doc = read_composite(doc_path.read_text(encoding="utf-8"), source_path=doc_path)
    words = load_words(FIXTURES / "reordered_segments.words.json")

    result = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert result.status in ("ok", "warning"), [d.model_dump() for d in result.diagnostics]
    assert result.plan is not None
    reel = result.plan
    _assert_transition_indexes_adjacent(reel)
    assert [(t.before_index, t.after_index) for t in reel.transitions] == [(0, 1), (1, 2)]
    assert [t.effect for t in reel.transitions] == ["dissolve", "smoothleft"]
    for transition in reel.transitions:
        left = reel.segments[transition.before_index]
        right = reel.segments[transition.after_index]
        left_duration = left.end_s - left.start_s if isinstance(left, SourceSegment) else left.duration_s
        right_duration = right.end_s - right.start_s if isinstance(right, SourceSegment) else right.duration_s
        assert transition.duration_s < min(left_duration, right_duration)
    validate_renderable(reel)
    _assert_no_source_time_overlap(reel)


def test_reordered_composite_closes_through_real_stitch_plan():
    doc_path = FIXTURES / "reordered_segments.ts.md"
    doc = read_composite(doc_path.read_text(encoding="utf-8"), source_path=doc_path)
    words = load_words(FIXTURES / "reordered_segments.words.json")
    result = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))
    assert result.status in ("ok", "warning"), [d.model_dump() for d in result.diagnostics]
    assert result.plan is not None
    reel = result.plan
    media_path = Path(__file__)
    assets = _asset_map_for_reel(reel, media_path)

    validate_renderable(reel)
    _assert_no_source_time_overlap(reel)
    plan = plan_pairwise_stitch(reel, assets)
    graph = build_footage_filtergraph(reel, assets)

    assert plan.total_duration_s == pytest.approx(reel.duration_s)
    assert [step.trim_start_s for step in plan.norm_steps] == pytest.approx([20.0, 10.0, 18.0])
    assert [step.idx for step in plan.norm_steps] == [0, 1, 2]
    assert any(step.effect == "dissolve" for step in plan.fold_steps)
    fold_cmd = " ".join(
        _fold_cmd(
            Path("/tmp/current.mp4"),
            Path("/tmp/next.mp4"),
            plan.fold_steps[0],
            Path("/tmp/out.mp4"),
            duration_clamp=None,
        )
    )
    assert "xfade=transition=dissolve" in fold_cmd
    assert "acrossfade" in fold_cmd
    assert "xfade=transition=dissolve" in graph.filter_complex
    assert "acrossfade" in graph.filter_complex
