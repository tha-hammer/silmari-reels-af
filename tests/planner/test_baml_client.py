from __future__ import annotations

import subprocess
from pathlib import Path

BAML_FILES = {
    "generators": Path("baml_src/generators.baml"),
    "clients": Path("baml_src/clients.baml"),
    "types": Path("baml_src/types.baml"),
    "retention": Path("baml_src/retention.baml"),
    "mine": Path("baml_src/mine.baml"),
    "strategize": Path("baml_src/strategize.baml"),
    "arrange": Path("baml_src/arrange.baml"),
    "script_coherence": Path("baml_src/script_coherence.baml"),
}


def test_baml_client_exposes_planner_functions():
    from baml_client.async_client import b
    from baml_client.types import (
        Beat,
        CandidateSpan,
        CandidateTranscriptContext,
        PlannerCandidate,
        ReelBlueprint,
        ReelStrategy,
        ScriptCoherenceFixAction,
        ScriptCoherenceReport,
        ScriptTransitionVerdict,
    )

    for fn in ("MineCandidates", "StrategizeReel", "ArrangeReel", "CheckScriptCoherence"):
        assert hasattr(b, fn)
    assert "template_" in ReelStrategy.model_fields
    assert "template_" in ReelBlueprint.model_fields
    assert "template" not in ReelStrategy.model_fields
    assert "template" not in ReelBlueprint.model_fields
    assert "target_duration_s" not in ReelStrategy.model_fields
    assert "target_duration_s" not in ReelBlueprint.model_fields
    assert "duration_range_s" in ReelStrategy.model_fields
    assert "duration_policy" in ReelStrategy.model_fields
    assert "arc" in ReelStrategy.model_fields
    assert "duration_range_s" in ReelBlueprint.model_fields
    assert "duration_policy" in ReelBlueprint.model_fields
    assert "arc" in ReelBlueprint.model_fields
    assert "completion_rationale" in ReelBlueprint.model_fields
    assert "rationale" in CandidateSpan.model_fields
    assert "rationale" in PlannerCandidate.model_fields
    assert "rationale" in Beat.model_fields
    assert "source_window_index" in CandidateSpan.model_fields
    assert "source_window_index" in PlannerCandidate.model_fields
    assert "rationale" in ReelStrategy.model_fields
    assert "rationale" in ReelBlueprint.model_fields
    assert "source_neighborhood" in CandidateTranscriptContext.model_fields
    assert "transitions" in ScriptCoherenceReport.model_fields
    assert ScriptTransitionVerdict.UnbridgedJump.value == "unbridged_jump"
    assert ScriptCoherenceFixAction.Bridge.value == "bridge"


def test_authored_baml_source_is_split_by_concern():
    assert not Path("baml_src/reel_planner.baml").exists()
    for path in BAML_FILES.values():
        assert path.exists(), path

    assert "generator target" in BAML_FILES["generators"].read_text()
    assert "client<llm> PlannerLLM" in BAML_FILES["clients"].read_text()
    assert "client<llm> PlannerFallback" in BAML_FILES["clients"].read_text()
    assert "retry_policy PlannerRetry" in BAML_FILES["clients"].read_text()
    assert "template_string RetentionRules" in BAML_FILES["retention"].read_text()
    assert "function MineCandidates" in BAML_FILES["mine"].read_text()
    assert "function StrategizeReel" in BAML_FILES["strategize"].read_text()
    assert "function ArrangeReel" in BAML_FILES["arrange"].read_text()
    assert "function CheckScriptCoherence" in BAML_FILES["script_coherence"].read_text()


def test_authored_baml_types_match_planner_contract():
    src = BAML_FILES["types"].read_text()
    expected = [
        "enum HookType",
        'CuriosityGap @alias("curiosity_gap")',
        'BoldClaim @alias("bold_claim")',
        'DirectCallout @alias("direct_callout")',
        'ResultFirst @alias("result_first")',
        'Question @alias("question")',
        'PainPoint @alias("pain_point")',
        'Number @alias("number")',
        'PatternInterrupt @alias("pattern_interrupt")',
        "class PlannerCandidate",
        "candidate_id string",
        "occurrence_index int",
        "rationale string?",
        "class CandidateTranscriptContext",
        "source_neighborhood string",
        "class ReelStrategy",
        "template_ Template",
        "duration_range_s DurationRange",
        "duration_policy DurationPolicy",
        "arc ArcPlan",
        "class ScriptBeatText",
        "enum ScriptTransitionVerdict",
        "enum ScriptCoherenceFixAction",
        "class ScriptCoherenceReport",
        "class ReelBlueprint",
        "template_ Template",
        "completion_rationale string",
    ]
    for needle in expected:
        assert needle in src


def test_authored_baml_functions_match_planner_contract():
    src = "\n".join(
        BAML_FILES[name].read_text()
        for name in ("clients", "retention", "mine", "strategize", "arrange", "script_coherence")
    )
    expected = [
        "model \"anthropic/claude-sonnet-5\"",
        "strategy {",
        "type exponential_backoff",
        "delay_ms 200",
        "multiplier 2",
        "max_delay_ms 2000",
        "provider fallback",
        "strategy [",
        "function ArrangeReel(",
        "candidate_contexts: CandidateTranscriptContext[]",
        "function CheckScriptCoherence(",
        "repair_hint: string?",
        "If repair_hint is present, repair those exact failed quotes without changing unrelated strategy.",
        "rationale MUST explain",
        "value_score gates eligibility",
        "Never leave the listener without the why",
        "CONTENT-DRIVEN LENGTH LATITUDE",
        "CONTENT-DRIVEN LENGTH AND CAP",
        "{{ ctx.output_format }}",
    ]
    for needle in expected:
        assert needle in src


def test_baml_generator_emits_packaged_client():
    generator = BAML_FILES["generators"].read_text()
    assert 'output_dir "../src"' in generator
    assert 'version "0.222.0"' in generator

    ignored = subprocess.run(
        ["git", "check-ignore", "src/baml_client"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode != 0, ignored.stdout + ignored.stderr
