from __future__ import annotations

from pathlib import Path

BAML_SOURCES = [
    Path("baml_src/retention.baml"),
    Path("baml_src/mine.baml"),
    Path("baml_src/strategize.baml"),
    Path("baml_src/arrange.baml"),
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
    ):
        assert clause in src


def test_mine_prompt_keeps_offsets_non_authoritative():
    src = _source()

    assert "function MineCandidates" in src
    assert "{{ RetentionRules() }}" in src
    assert "Quote VERBATIM" in src
    assert "approx_start_s/approx_end_s are optional hints" in src
    assert "never authoritative timecodes" in src


def test_strategize_prompt_preserves_candidate_identity():
    src = _source()

    assert "function StrategizeReel" in src
    assert "candidate_id/occurrence_index" in src
    assert "ONE primary engagement lever" in src
    assert "Preserve candidate identity" in src


def test_arrange_prompt_carries_repair_hint_contract():
    src = _source()

    assert "function ArrangeReel" in src
    assert "repair_hint: string?" in src
    assert "If repair_hint is present, repair those exact failed quotes" in src
    assert "candidate_id, occurrence_index, VERBATIM span_quote" in src
    assert "Repair hint: {{ repair_hint }}" in src
