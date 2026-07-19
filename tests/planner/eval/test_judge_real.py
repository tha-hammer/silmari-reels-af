from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from reel_af.planner.eval.models import RETENTION_DIMENSIONS
from reel_af.planner.eval.runner import score_blueprint

from .test_gates_runner import SRC, _blueprint, _write_words

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

pytestmark = pytest.mark.requires_openrouter(reason="real eval judge")


def test_live_openrouter_judge_scores_gate_passing_blueprint(tmp_path):
    result = score_blueprint(
        _blueprint(),
        _write_words(tmp_path),
        source_url=SRC,
        case_id="live-judge-smoke",
        out_dir=tmp_path,
    )

    assert result.gates.passed
    assert not result.judge_skipped
    assert result.judge_model
    assert set(result.dimensions) == set(RETENTION_DIMENSIONS)
    assert all(1 <= item.score <= 5 for item in result.dimensions.values())
    assert list(tmp_path.glob("*live-judge-smoke.json"))
