from __future__ import annotations

from reel_af.dsl.models import DslWord, WordsSidecar
from reel_af.planner.models import Beat, BeatRole, PlannerCandidate
from reel_af.planner.script_coherence import (
    build_candidate_contexts,
    build_script_beats,
    build_script_transitions,
    coherence_repair_hint,
    contextual_candidate_pool,
)
from reel_af.planner.serialize import ResolvedBeat
from reel_af.planner.models import (
    ScriptCoherenceFixAction,
    ScriptCoherenceReport,
    ScriptTransitionReview,
    ScriptTransitionVerdict,
)


def _words() -> WordsSidecar:
    tokens = "before why pay now because bridge payoff lands after echo".split()
    return WordsSidecar(
        words=[
            DslWord(w=token, start=float(index), end=float(index) + 0.5)
            for index, token in enumerate(tokens)
        ]
    )


def _candidate(candidate_id: str, word_range: list[int], start_s: float, end_s: float):
    return PlannerCandidate(
        candidate_id=candidate_id,
        quote=" ".join(word.w for word in _words().words[word_range[0] : word_range[1] + 1]),
        occurrence_index=0,
        word_range=word_range,
        start_s=start_s,
        end_s=end_s,
        quality=1.0,
        value_score=0.9,
        emotion="plain",
        is_claim=False,
        payoff_worthy=True,
        rationale="accepted source quote for one local thread",
    )


def _beat(candidate_id: str, quote: str, role: BeatRole = BeatRole.Value) -> Beat:
    return Beat(
        role=role,
        span_quote=quote,
        candidate_id=candidate_id,
        occurrence_index=0,
        max_len_s=3.0,
        rationale="this beat advances the local thread",
    )


def test_contextual_candidate_pool_adds_bounded_bridge_spans():
    candidates = [_candidate("c001", [2, 3], 2.0, 3.5), _candidate("c002", [6, 7], 6.0, 7.5)]

    pool = contextual_candidate_pool(candidates, _words(), selected_candidate_ids={"c001"})

    ids = {candidate.candidate_id for candidate in pool}
    assert {"c001", "c002", "ctx_c001_before", "ctx_c001_after"} <= ids
    ctx_after = next(candidate for candidate in pool if candidate.candidate_id == "ctx_c001_after")
    assert ctx_after.word_range == [4, 9]
    assert ctx_after.word_range[1] - ctx_after.word_range[0] <= 5
    assert ctx_after.rationale and "preserve the why" in ctx_after.rationale


def test_build_candidate_contexts_exposes_source_neighborhood():
    candidates = [_candidate("c001", [2, 3], 2.0, 3.5), _candidate("c002", [6, 7], 6.0, 7.5)]

    contexts = build_candidate_contexts(candidates, _words())

    first = contexts[0]
    assert first.before_text == "before why"
    assert first.after_text == "because bridge payoff lands after echo"
    assert first.source_neighborhood == "before why pay now because bridge payoff lands after echo"
    assert first.next_candidate_id == "c002"
    assert first.gap_to_next_s == 2.5


def test_build_script_beats_and_transitions_use_actual_resolved_text():
    beats = [
        _beat("c001", "pay now", BeatRole.Hook),
        _beat("c002", "payoff lands", BeatRole.Payoff),
    ]
    resolved = [
        ResolvedBeat(
            index=0,
            beat=beats[0],
            span_quote="pay now",
            resolved=True,
            start_s=2.0,
            end_s=3.5,
            word_range=(2, 3),
        ),
        ResolvedBeat(
            index=1,
            beat=beats[1],
            span_quote="payoff lands",
            resolved=True,
            start_s=6.0,
            end_s=7.5,
            word_range=(6, 7),
        ),
    ]

    script_beats = build_script_beats(beats, resolved)
    transitions = build_script_transitions(script_beats, resolved, _words())

    assert script_beats[0].span_quote == "pay now"
    assert script_beats[0].rationale == "this beat advances the local thread"
    assert transitions[0].from_text == "pay now"
    assert transitions[0].to_text == "payoff lands"
    assert transitions[0].source_gap_s == 2.5
    assert transitions[0].connective_text == "because bridge"


def test_coherence_repair_hint_names_transition_and_fix():
    report = ScriptCoherenceReport(
        coherent=False,
        transitions=[
            ScriptTransitionReview(
                transition_index=0,
                from_beat_index=0,
                to_beat_index=1,
                verdict=ScriptTransitionVerdict.UnbridgedJump,
                fix_action=ScriptCoherenceFixAction.Bridge,
                why_present=False,
                rationale="the second beat jumps to payoff without the cause",
                missing_why="why the payoff follows",
                suggested_bridge_candidate_ids=["ctx_c002_before"],
                suggested_repair="include the local bridge before c002",
            )
        ],
        overall_rationale="one transition drops the why",
        repair_hint="bridge ctx_c002_before",
    )

    hint = coherence_repair_hint(report, max_chars=400)

    assert "SCRIPT-COHERENCE" in hint
    assert "UnbridgedJump" in hint
    assert "Bridge" in hint
    assert "ctx_c002_before" in hint
