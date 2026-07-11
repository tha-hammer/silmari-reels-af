"""B13: insert black creates BlackSegment."""

from __future__ import annotations

from pathlib import Path

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import BlackSegment, SourceRef, validate_renderable

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def test_insert_black_creates_black_segment():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert black 2.5]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None

    black_segs = [s for s in res.plan.segments if isinstance(s, BlackSegment)]
    assert len(black_segs) == 1
    assert black_segs[0].duration_s == 2.5


def test_black_insert_affects_duration():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert black 2.5]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.plan is not None
    seg_0_dur = res.plan.segments[0].end_s - res.plan.segments[0].start_s
    seg_2_dur = res.plan.segments[2].end_s - res.plan.segments[2].start_s
    expected_total = seg_0_dur + 2.5 + seg_2_dur
    assert abs(res.plan.duration_s - expected_total) < 0.2


def test_black_insert_validates_renderable():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert black 2.5]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.plan is not None
    validate_renderable(res.plan)
