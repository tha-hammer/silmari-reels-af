"""B10: join merges adjacent source segments with guards."""

from __future__ import annotations

from pathlib import Path

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import FallbackSegment, SourceRef, SourceSegment, WordsSidecar

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def _words_sidecar(spans: list[tuple[float, float, str]]) -> WordsSidecar:
    return WordsSidecar(
        words=[],
        segments=[
            FallbackSegment(start_s=start_s, end_s=end_s, text=text)
            for start_s, end_s, text in spans
        ],
    )


def test_join_merges_adjacent_same_source():
    text = (
        "00:01:12.300  So the fix isn't a smarter model. It's a tighter loop.\n"
        "\n"
        "[join]\n"
        "\n"
        "00:01:19.050  A loop you can actually see closing.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    assert len(res.plan.segments) == 1
    seg = res.plan.segments[0]
    assert isinstance(seg, SourceSegment)
    assert seg.start_s <= 72.3
    assert seg.end_s >= 81.16


def test_join_normal_within_gap_limit():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[join]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    assert len(res.plan.segments) == 1


def test_segment_trailing_join_targets_boundary_after_that_segment():
    text = (
        "00:00:10.000  first mergeable clip\n"
        "00:00:12.000  second mergeable clip\n"
        "[join]\n"
        "00:00:14.000  third mergeable clip\n"
    )
    doc = read_composite(text)
    words = _words_sidecar([
        (10.0, 12.0, "first mergeable clip"),
        (12.0, 14.0, "second mergeable clip"),
        (14.0, 16.0, "third mergeable clip"),
    ])
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    source_segments = [seg for seg in res.plan.segments if isinstance(seg, SourceSegment)]
    assert len(source_segments) == 2
    assert source_segments[0].text == "first mergeable clip"
    assert source_segments[1].text == "second mergeable clip third mergeable clip"
