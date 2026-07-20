from __future__ import annotations

from pathlib import Path

BAML_SOURCES = [
    Path("baml_src/retention.baml"),
    Path("baml_src/mine.baml"),
    Path("baml_src/strategize.baml"),
    Path("baml_src/arrange.baml"),
    Path("baml_src/script_coherence.baml"),
]


def _source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in BAML_SOURCES)


def test_retention_frame_contains_load_bearing_rules():
    src = _source()

    for clause in (
        "VERBATIM",
        "candidate_id",
        "occurrence_index",
        "NEVER provide absolute timecodes",
        "Cut-ins are relative",
        "NEVER emit engagement bait",
        "{{ ctx.output_format }}",
        "Emit enum values exactly as",
        "wire values shown in the output format",
    ):
        assert clause in src

    assert "Emit PascalCase enum values" not in src


def test_retention_frame_uses_content_driven_r7_not_short_targeting():
    src = _source()

    assert "one short vertical reel" not in src
    assert "R7 Content-driven length" in src
    assert "not fit to a" in src
    assert "default 180s soft cap" in src
    assert "Hook/payoff pair" not in src
    assert "strict global monotone shortening" in src


def test_mine_prompt_keeps_offsets_non_authoritative():
    src = _source()

    assert "function MineCandidates" in src
    assert "{{ RetentionRules() }}" in src
    assert "Quote VERBATIM" in src
    assert "approx_start_s/approx_end_s are optional hints" in src
    assert "never authoritative timecodes" in src
    assert "whole-source" in src
    assert "source window" in src
    assert "do not cluster" in src
    assert "Never pad candidate output" in src
    assert "rationale must explain why this exact span was selected" in src
    assert "value_score, emotion" in src


def test_strategize_prompt_preserves_candidate_identity():
    src = _source()

    assert "function StrategizeReel" in src
    assert "candidate_id/occurrence_index" in src
    assert "ONE primary engagement lever" in src
    assert "Preserve candidate identity" in src
    assert "rationale MUST state why the selected template_" in src
    assert "CONTENT-DRIVEN LENGTH LATITUDE" in src
    assert "duration_range_s" in src
    assert "Do NOT fit to a requested timecode" in src
    assert "Do NOT pad" in src
    assert "Do NOT truncate" in src
    assert "must not be compressed into the" in src
    assert "compact five-beat strategy is valid only when" in src
    assert "target length is tight enough" not in src
    assert "TIGHT TARGET LENGTH BAND" not in src


def test_arrange_prompt_carries_repair_hint_contract():
    src = _source()

    assert "function ArrangeReel" in src
    assert "candidate_contexts: CandidateTranscriptContext[]" in src
    assert "repair_hint: string?" in src
    assert "You are cutting this transcript into a coherent short script" in src
    assert "Never leave the listener without the why" in src
    assert "value_score gates eligibility" in src
    assert "Candidate transcript context" in src
    assert "If repair_hint is present, repair those exact failed quotes" in src
    assert "candidate_id, occurrence_index, VERBATIM span_quote" in src
    assert "Repair hint: {{ repair_hint }}" in src
    assert "rationale MUST explain why this beat order resolves the strategy" in src
    assert "completion_rationale MUST explain" in src
    assert "CONTENT-DRIVEN LENGTH AND CAP" in src
    assert "First select every beat required to complete strategy.arc" in src
    assert "Never pad" in src
    assert "Scale interrupt density to beat count" in src
    assert "Do not default to the old five-beat" in src
    assert "Bad compressed long-source arc" in src
    assert "Use interrupt_out on 2 or 3 beats total" not in src
    assert "Name the loop candidate_id/occurrence_index" in src


def test_arrange_prompt_requires_distinct_loop_source_span():
    src = _source()

    assert "Every reel segment must resolve to a unique source span" in src
    assert (
        "The final beat MUST echo strategy.hook.span_quote while using a source span DISTINCT"
        in src
    )
    assert "different candidate_id/occurrence_index" in src
    assert "start_s/end_s" in src
    assert "choose the OTHER occurrence for the final loop" in src
    assert "Never reuse the identical hook source clip as the final loop" in src


def test_arrange_prompt_forbids_join_into_earlier_source_span():
    src = _source()

    assert "Never Join into an earlier source span" in src
    assert "Use Trans, not Join" in src
    assert "candidate start_s is earlier than the prior beat's candidate start_s" in src


def test_script_coherence_prompt_reviews_actual_assembled_script():
    src = _source()

    assert "function CheckScriptCoherence" in src
    assert "actual concatenated span text in order" in src
    assert "does beat N+1 follow from beat N" in src
    assert "is the why present or dropped" in src
    assert "non-sequitur" in src
    assert "bridge / drop / reorder" in src
    assert "Do NOT hardcode bridge-vs-drop" in src
    assert "per-transition verdict" in src
