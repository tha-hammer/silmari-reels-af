"""Closure adapter proposal for reordered reel-af DSL source intervals.

This file is a research artifact, not production code. It sketches the production readers the
TDD closure should use so the implementation plan tests observe the real compiler and stitcher
contracts instead of a parser-only surrogate.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from reel_af.dsl.models import DownloadedSegment, FootageReel, SourceSegment, validate_renderable
from reel_af.render.footage_stitch import plan_pairwise_stitch


def source_interval_triples(reel: FootageReel) -> list[tuple[str, str, float, float]]:
    """Return source-url, segment-id, start, end for all source segments in a reel."""
    triples: list[tuple[str, str, float, float]] = []
    for segment in reel.segments:
        if isinstance(segment, SourceSegment):
            triples.append((
                segment.source_url,
                segment.segment_id,
                segment.start_s,
                segment.end_s,
            ))
    return triples


def assert_no_source_time_overlap(reel: FootageReel, *, epsilon: float = 1e-6) -> None:
    """Fail if any source moment is covered by more than one emitted source segment."""
    by_source: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for source_url, segment_id, start_s, end_s in source_interval_triples(reel):
        by_source[source_url].append((segment_id, start_s, end_s))

    for source_url, intervals in by_source.items():
        intervals.sort(key=lambda item: (item[1], item[2], item[0]))
        for left, right in zip(intervals, intervals[1:]):
            left_id, _left_start, left_end = left
            right_id, right_start, _right_end = right
            assert left_end <= right_start + epsilon, (
                f"source overlap for {source_url}: {left_id} ends at {left_end}, "
                f"{right_id} starts at {right_start}"
            )


def asset_map_for_reel(reel: FootageReel, media_path: Path) -> dict[str, DownloadedSegment]:
    """Build a minimal asset map for pure stitch planning/filtergraph tests."""
    assets: dict[str, DownloadedSegment] = {}
    for segment in reel.segments:
        if not isinstance(segment, SourceSegment):
            continue
        assets[segment.segment_id] = DownloadedSegment(
            segment_id=segment.segment_id,
            path=media_path,
            source_start_s=segment.start_s,
            source_end_s=segment.end_s,
        )
    return assets


def assert_reordered_reel_closes_through_stitch_plan(
    reel: FootageReel,
    *,
    media_path: Path,
) -> None:
    """Validate the real renderability contract and derive the real pairwise stitch plan."""
    validate_renderable(reel)
    assert_no_source_time_overlap(reel)
    plan = plan_pairwise_stitch(reel, asset_map_for_reel(reel, media_path))
    assert plan.total_duration_s > 0
