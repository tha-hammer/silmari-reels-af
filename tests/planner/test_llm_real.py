from __future__ import annotations

from pathlib import Path

import pytest

from baml_client.async_client import b
from reel_af.dsl.aligner import align
from reel_af.dsl.compile import load_words
from reel_af.dsl.models import MATCH_QUALITY_FLOOR

pytestmark = pytest.mark.openrouter_required(reason="real BAML mine")

FIXTURES = Path(__file__).resolve().parents[1] / "dsl" / "fixtures"


async def test_real_mine_returns_verbatim_alignable_spans():
    words = load_words(FIXTURES / "source.words.json")
    transcript_text = " ".join(word.w for word in words.words)

    candidates = await b.MineCandidates(transcript_text, "educational")

    assert candidates
    assert any(
        getattr(align(candidate.quote, words), "quality", 0.0) >= MATCH_QUALITY_FLOOR
        for candidate in candidates
    )
