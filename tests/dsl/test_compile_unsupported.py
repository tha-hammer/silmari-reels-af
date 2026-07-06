"""B12: Unsupported Tier 4 markers fail with diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.compile import compile_composite, load_words
from reel_af.dsl.composite import read_composite
from reel_af.dsl.models import SourceRef

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE_URL = "https://example.com/source.mp4"


def test_insert_relevant_returns_unsupported():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert relevant 25]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "error"
    assert res.plan is None
    codes = [d.code for d in res.diagnostics]
    assert "UNSUPPORTED_INSERT" in codes


def test_insert_file_returns_unsupported():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[insert file rel_01]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "error"
    assert res.plan is None
    codes = [d.code for d in res.diagnostics]
    assert "UNSUPPORTED_INSERT" in codes


def test_find_returns_unsupported():
    text = (
        "00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning.\n"
        "\n"
        "[find relevant 30 x5]\n"
        "\n"
        "00:00:21.740  And the moment you trust the feeling, you ship the bug.\n"
    )
    doc = read_composite(text)
    words = load_words(FIXTURES / "source.words.json")
    res = compile_composite(doc, words, SourceRef(source_url=SOURCE_URL))

    assert res.status == "error"
    assert res.plan is None
    codes = [d.code for d in res.diagnostics]
    assert "UNSUPPORTED_FIND" in codes
