from __future__ import annotations

import json

import pytest

from reel_af.planner.eval.judge import build_judge_messages, parse_judge_response
from reel_af.planner.eval.models import (
    RETENTION_DIMENSIONS,
    BlueprintEvidence,
    DimensionScore,
)


def _scores() -> dict[str, dict[str, object]]:
    return {
        dimension: {"score": 4, "rationale": f"{dimension} is supported by the evidence."}
        for dimension in RETENTION_DIMENSIONS
    }


def test_parse_judge_response_requires_every_dimension():
    result = parse_judge_response(json.dumps({"dimensions": _scores()}), model="judge-model")

    assert result.model == "judge-model"
    assert result.aggregate_score == 4
    assert set(result.dimensions) == set(RETENTION_DIMENSIONS)
    assert all(isinstance(item, DimensionScore) for item in result.dimensions.values())


def test_parse_judge_response_rejects_missing_dimension():
    scores = _scores()
    scores.pop("single_cta_r12")

    with pytest.raises(ValueError, match="single_cta_r12"):
        parse_judge_response(json.dumps({"dimensions": scores}), model="judge-model")


def test_judge_prompt_is_form_filling_and_bias_controlled():
    evidence = BlueprintEvidence(
        case_id="case",
        artifact_kind="blueprint",
        source_url="https://youtu.be/eval123",
        beats=[],
        engagement_lines=["Send this to a founder before launch."],
        cta={"hardness": "soft", "placements": ["end"]},
        cut_ins=[{"beat_index": 1, "type": "zoom"}],
        planner_rationale={"arrange": {"rationale": "ordered payoff before loop"}},
    )

    messages = build_judge_messages(evidence)
    payload = json.loads(messages[1]["content"])

    assert "evaluation_steps" in payload
    assert "bias_controls" in payload
    assert "evidence_contract" in payload
    assert "engagement_lines" in payload["evidence"]
    assert "planner_rationale" in payload["evidence"]
    assert set(payload["required_json_form"]["dimensions"]) == set(RETENTION_DIMENSIONS)
