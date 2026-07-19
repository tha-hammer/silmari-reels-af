from __future__ import annotations

import pytest

from reel_af.dsl.models import MATCH_QUALITY_FLOOR, DslWord, WordsSidecar
from reel_af.planner.models import CandidateSpan, PlannerCandidate
from reel_af.planner.pipeline import plan
from reel_af.planner.serialize import resolve_timecodes
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


def _timed_words(*tokens: str) -> WordsSidecar:
    return WordsSidecar(
        words=[
            DslWord(w=token, start=index * 0.5, end=(index + 1) * 0.5)
            for index, token in enumerate(tokens)
        ]
    )


def _planner_candidate(
    candidate_id: str,
    quote: str,
    start: int,
    end: int,
    *,
    occurrence_index: int = 0,
) -> PlannerCandidate:
    return PlannerCandidate(
        candidate_id=candidate_id,
        quote=quote,
        occurrence_index=occurrence_index,
        word_range=[start, end],
        start_s=start * 0.5,
        end_s=(end + 1) * 0.5,
        quality=1.0,
        value_score=0.9,
        emotion="practical",
        is_claim=True,
        payoff_worthy=True,
    )


def _beat(quote: str, candidate_id: str = "c001") -> dict[str, object]:
    return {
        "span_quote": quote,
        "candidate_id": candidate_id,
        "occurrence_index": 0,
        "max_len_s": 10.0,
    }


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


def test_resolve_timecodes_joins_adjacent_candidate_spans_with_first_identity():
    words = _timed_words("um", "alpha", "beta", "gamma", "delta")
    candidates = [
        _planner_candidate("c001", "alpha beta", 1, 2),
        _planner_candidate("c002", "gamma delta", 3, 4),
    ]

    resolved = resolve_timecodes(
        [_beat("alpha beta gamma delta", "c001")],
        words,
        candidates=candidates,
    )

    assert resolved[0].resolved
    assert resolved[0].word_range == (1, 4)
    assert (resolved[0].start_s, resolved[0].end_s) == (0.5, 2.5)

    wrong_identity = resolve_timecodes(
        [_beat("alpha beta gamma delta", "c002")],
        words,
        candidates=candidates,
    )
    assert not wrong_identity[0].resolved


def test_resolve_timecodes_allows_trimmed_leading_filler():
    words = _timed_words("um", "alpha", "beta")
    candidates = [_planner_candidate("c001", "um alpha beta", 0, 2)]

    resolved = resolve_timecodes([_beat("alpha beta")], words, candidates=candidates)

    assert resolved[0].resolved
    assert resolved[0].word_range == (1, 2)
    assert (resolved[0].start_s, resolved[0].end_s) == (0.5, 1.5)


def test_resolve_timecodes_allows_trimmed_trailing_filler():
    words = _timed_words("alpha", "beta", "you", "know")
    candidates = [_planner_candidate("c001", "alpha beta you know", 0, 3)]

    resolved = resolve_timecodes([_beat("alpha beta")], words, candidates=candidates)

    assert resolved[0].resolved
    assert resolved[0].word_range == (0, 1)
    assert (resolved[0].start_s, resolved[0].end_s) == (0.0, 1.0)


def test_resolve_timecodes_rejects_added_word_even_when_source_aligns():
    words = _timed_words("alpha", "beta", "inserted", "gamma")
    candidates = [
        _planner_candidate("c001", "alpha beta", 0, 1),
        _planner_candidate("c002", "gamma", 3, 3),
    ]

    resolved = resolve_timecodes(
        [_beat("alpha beta inserted gamma")],
        words,
        candidates=candidates,
    )

    assert not resolved[0].resolved
    assert resolved[0].quality == 1.0


def test_resolve_timecodes_rejects_reworded_word():
    words = _timed_words("alpha", "beta")
    candidates = [_planner_candidate("c001", "alpha beta", 0, 1)]

    resolved = resolve_timecodes([_beat("alpha theta")], words, candidates=candidates)

    assert not resolved[0].resolved


def test_resolve_timecodes_rejects_alignment_below_floor():
    words = _timed_words("alpha", "beta")
    candidates = [_planner_candidate("c001", "alpha beta", 0, 1)]

    resolved = resolve_timecodes(
        [_beat("totally unrelated phrase")],
        words,
        candidates=candidates,
        floor=0.85,
    )

    assert not resolved[0].resolved
    assert resolved[0].quality < 0.85


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
