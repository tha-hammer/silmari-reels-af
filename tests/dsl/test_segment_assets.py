from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.models import (
    BlackSegment,
    DownloadedSegment,
    FootageReel,
    SegmentFetchRequest,
    SourceSegment,
    Transition,
)
from reel_af.render.footage_stitch import (
    MissingSegmentAssetError,
    download_segments,
    validate_segment_assets,
)


def test_download_segments_fetches_source_segments_only(tmp_path):
    reel = FootageReel(
        source_url="https://example.test/source.mp4",
        segments=[
            SourceSegment(
                segment_id="seg-0001",
                source_url="https://example.test/source.mp4",
                start_s=1.0,
                end_s=2.5,
                text="one",
            ),
            BlackSegment(duration_s=0.5),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="none", duration_s=0.0)],
        duration_s=2.0,
    )
    requests: list[SegmentFetchRequest] = []

    def fetch(request: SegmentFetchRequest) -> DownloadedSegment:
        requests.append(request)
        request.target_path.write_bytes(b"fake mp4")
        return DownloadedSegment(
            segment_id=request.segment_id,
            path=request.target_path,
            source_start_s=request.start_s,
            source_end_s=request.end_s,
        )

    assets = download_segments(reel, tmp_path, fetch)

    assert list(assets) == ["seg-0001"]
    assert len(requests) == 1
    assert requests[0].source_url == "https://example.test/source.mp4"
    assert requests[0].start_s == 1.0
    assert requests[0].end_s == 2.5
    assert requests[0].target_path == tmp_path / "seg-0001.mp4"


def test_validate_segment_assets_rejects_missing_source_asset(tmp_path):
    reel = FootageReel(
        source_url="fixture",
        segments=[
            SourceSegment(
                segment_id="seg-0001",
                source_url="fixture",
                start_s=0.0,
                end_s=1.0,
                text="one",
            ),
            BlackSegment(duration_s=0.5),
        ],
        transitions=[Transition(before_index=0, after_index=1, effect="none", duration_s=0.0)],
        duration_s=1.5,
    )

    with pytest.raises(MissingSegmentAssetError, match="MISSING_SEGMENT_ASSET"):
        validate_segment_assets(reel, {})

    with pytest.raises(MissingSegmentAssetError, match="does not exist"):
        validate_segment_assets(
            reel,
            {
                "seg-0001": DownloadedSegment(
                    segment_id="seg-0001",
                    path=tmp_path / "missing.mp4",
                    source_start_s=0.0,
                    source_end_s=1.0,
                )
            },
        )


def test_black_segments_do_not_require_assets(tmp_path):
    reel = FootageReel(
        source_url="fixture",
        segments=[BlackSegment(duration_s=0.5)],
        transitions=[],
        duration_s=0.5,
    )

    validate_segment_assets(
        reel,
        {
            "unused": DownloadedSegment(
                segment_id="unused",
                path=Path(__file__),
                source_start_s=0.0,
                source_end_s=1.0,
            )
        },
    )
