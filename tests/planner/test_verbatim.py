from __future__ import annotations

import pytest

from reel_af.dsl.models import MATCH_QUALITY_FLOOR, DslWord, WordsSidecar
from reel_af.planner.models import CandidateSpan
from reel_af.planner.pipeline import plan
from reel_af.planner.verbatim import enforce_verbatim

SRC = "https://www.youtube.com/watch?v=abc123"


def _words() -> WordsSidecar:
    return WordsSidecar(
        words=[
            DslWord(w="they", start=0.0, end=0.2),
            DslWord(w="don't", start=0.2, end=0.5),
            DslWord(w="reason", start=0.5, end=0.9),
        ]
    )


def _cand(quote: str) -> CandidateSpan:
    return CandidateSpan(
        quote=quote,
        approx_start_s=0.0,
        approx_end_s=1.0,
        value_score=0.8,
        emotion="skepticism",
        is_claim=True,
        payoff_worthy=True,
    )


def test_enforce_verbatim_keeps_real_aligner_matches_and_drops_paraphrases():
    accepted, dropped = enforce_verbatim(
        [_cand("they don't reason"), _cand("they do not think")],
        _words(),
        floor=0.85,
    )

    assert [candidate.quote for candidate in accepted] == ["they don't reason"]
    assert accepted[0].candidate_id == "c001"
    assert accepted[0].word_range == [0, 2]
    assert accepted[0].quality == 1.0

    assert len(dropped) == 1
    assert dropped[0].candidate_id == "c002"
    assert dropped[0].reason == "below_floor"
    assert dropped[0].alignment.kind == "unmatched"
    assert dropped[0].alignment.best_quality < 0.85


def test_enforce_verbatim_rejects_floor_below_dsl_match_quality_floor():
    with pytest.raises(ValueError, match="MATCH_QUALITY_FLOOR"):
        enforce_verbatim([_cand("they don't reason")], _words(), floor=MATCH_QUALITY_FLOOR - 0.01)


class _ParaphraseOnlyLLM:
    def __init__(self) -> None:
        self.mine_calls = 0
        self.strategize_calls = 0

    async def mine(self, transcript, register):
        self.mine_calls += 1
        return [_cand("they do not think")]

    async def strategize(self, transcript, candidates, bounds):
        self.strategize_calls += 1
        raise AssertionError("pipeline should not strategize after verbatim rejects every candidate")

    async def arrange(self, candidates, strategy):
        raise AssertionError("pipeline should not arrange after verbatim rejects every candidate")


async def test_plan_stops_before_strategy_when_verbatim_gate_empties_candidates(tmp_path):
    llm = _ParaphraseOnlyLLM()

    result = await plan(SRC, words=_words(), llm=llm, out_dir=tmp_path)

    assert result["error"] == "planner_empty_candidate_set"
    assert llm.mine_calls == 1
    assert llm.strategize_calls == 0
    assert not (tmp_path / "composite.ts.md").exists()
    assert result["diagnostics"][0]["code"] == "CANDIDATE_NOT_FOUND"
