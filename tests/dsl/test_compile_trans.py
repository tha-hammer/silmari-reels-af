"""B11: trans compiles to transitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import SourceRef

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def test_trans_fade_maps_to_transition():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[trans dissolve 0.8]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    assert len(res.plan.transitions) == 1
    t = res.plan.transitions[0]
    assert t.effect == "dissolve"
    assert t.duration_s == 0.8
    assert t.audio_fade is True


def test_trans_none_produces_hard_cut():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[trans none]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    t = res.plan.transitions[0]
    assert t.effect == "none"
    assert t.duration_s == 0.0


def test_trans_audio_cut():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[trans dissolve 0.8 audio=cut]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "ok"
    assert res.plan is not None
    t = res.plan.transitions[0]
    assert t.audio_fade is False


def test_unresolved_hole_returns_error():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[trans ?]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "error"
    assert res.plan is None
    codes = [d.code for d in res.diagnostics]
    assert "UNRESOLVED_HOLE" in codes
