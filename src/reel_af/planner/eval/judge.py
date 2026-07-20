"""OpenRouter-backed LLM judge for subjective retention quality."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from reel_af.planner.config import load_planner_config

from .models import (
    DIMENSION_LABELS,
    RETENTION_DIMENSIONS,
    BlueprintEvidence,
    DimensionScore,
    JudgeResult,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class JudgeError(RuntimeError):
    """Base judge failure."""


class JudgeResponseError(JudgeError, ValueError):
    """The judge returned malformed JSON or missing dimensions."""


class OpenRouterJudge:
    """Rubric judge using OpenRouter chat completions.

    The prompt uses G-Eval-style form filling: explicit criteria, evaluation
    steps, and a fixed JSON form. The evidence bundle is compact and neutral to
    reduce position, verbosity, and self-enhancement bias.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        timeout_s: float | None = None,
    ) -> None:
        cfg = load_planner_config()
        _load_env()
        self.model = model or os.environ.get("REEL_AF_EVAL_JUDGE_MODEL") or cfg.model
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s or cfg.llm_request_timeout_s

    def score(self, evidence: BlueprintEvidence) -> JudgeResult:
        """Score all named retention dimensions for one gate-passing reel."""

        if not self.api_key:
            raise JudgeError("OPENROUTER_API_KEY is required for eval judge scoring")

        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": build_judge_messages(evidence),
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_judge_response(content, model=self.model)


def build_judge_messages(evidence: BlueprintEvidence) -> list[dict[str, str]]:
    """Build a bias-controlled G-Eval form-filling prompt."""

    rubric = {
        dimension: {
            "label": DIMENSION_LABELS[dimension],
            "score_scale": {
                "1": "misses the criterion or actively weakens retention",
                "2": "partially present but vague, delayed, or inconsistent",
                "3": "adequate and understandable, with clear room to tighten",
                "4": "strong, specific, and mostly production-ready",
                "5": "excellent, sharply optimized, and directly supported by evidence",
            },
        }
        for dimension in RETENTION_DIMENSIONS
    }
    form = {
        "dimensions": {
            dimension: {"score": "integer 1-5", "rationale": "concise evidence-based reason"}
            for dimension in RETENTION_DIMENSIONS
        }
    }
    user_payload = {
        "task": "Score one vertical reel plan against named retention dimensions.",
        "bias_controls": [
            "Judge only the evidence fields. Do not infer quality from model/provider/source identity.",
            "Do not reward verbosity, polish, or amount of text. Reward only retention evidence.",
            "This is not pairwise ranking. Score each dimension independently.",
            "Use concise rationales under 240 characters.",
        ],
        "evidence_contract": [
            "For R9, inspect engagement_lines and beats[*].engagement for one specific send/share cue.",
            "For R11, inspect hook text, CTA, engagement_lines, and beats[*].engagement for bait.",
            "For R12, inspect cta.hardness and cta.placements plus any CTA-role beats.",
            "For cut-in discipline, inspect cut_ins and beats[*].cutin.",
            "planner_rationale is the planner's stated intent. Use it to understand choices, "
            "then verify the claim against the evidence fields before scoring.",
        ],
        "evaluation_steps": [
            "For each dimension, identify the exact evidence that supports or weakens it.",
            "Assign an integer score from 1 to 5 using the rubric scale.",
            "Fill every dimension in the JSON form. Do not add extra keys.",
        ],
        "rubric": rubric,
        "evidence": evidence.model_dump(mode="json", exclude_none=True),
        "required_json_form": form,
    }
    return [
        {
            "role": "system",
            "content": (
                "You are a strict reel-retention evaluator. Return only valid JSON. "
                "Do not include markdown, commentary, or hidden criteria."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=True, sort_keys=True),
        },
    ]


def parse_judge_response(text: str, *, model: str) -> JudgeResult:
    """Parse and validate the judge JSON form."""

    data = _extract_json_object(text)
    raw_dimensions = data.get("dimensions", data)
    if not isinstance(raw_dimensions, dict):
        raise JudgeResponseError("judge response missing dimensions object")

    dimensions: dict[str, DimensionScore] = {}
    for dimension in RETENTION_DIMENSIONS:
        raw_score = raw_dimensions.get(dimension)
        if not isinstance(raw_score, dict):
            raise JudgeResponseError(f"judge response missing dimension {dimension}")
        score = int(round(float(raw_score.get("score"))))
        score = max(1, min(5, score))
        rationale = str(raw_score.get("rationale") or "").strip()
        if not rationale:
            raise JudgeResponseError(f"judge response missing rationale for {dimension}")
        dimensions[dimension] = DimensionScore(score=score, rationale=rationale)

    aggregate = sum(item.score for item in dimensions.values()) / len(dimensions)
    return JudgeResult(
        model=model,
        dimensions=dimensions,
        aggregate_score=round(aggregate, 3),
        raw_response=text,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            raise JudgeResponseError("judge response did not contain a JSON object") from None
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise JudgeResponseError("judge response JSON root must be an object")
    return data


def _load_env() -> None:
    root = Path(__file__).resolve().parents[4]
    load_dotenv(root / ".env")
