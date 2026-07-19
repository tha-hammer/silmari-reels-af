from __future__ import annotations

import pytest
from pydantic import ValidationError

from reel_af.dsl.models import MATCH_QUALITY_FLOOR
from reel_af.planner.config import PlannerConfig, load_planner_config


def test_planner_config_loads_and_forbids_extra():
    cfg = load_planner_config()

    assert isinstance(cfg, PlannerConfig)
    assert cfg.model
    assert cfg.r1_hook_window_s == 3.5
    assert cfg.r2_cadence_s["entertainment"] == 3.0
    assert cfg.r11_bait_patterns

    with pytest.raises(ValidationError):
        PlannerConfig.model_validate({**cfg.model_dump(), "surprise": 1})


def test_config_contains_runtime_llm_and_bounds():
    cfg = load_planner_config()

    assert cfg.model == "anthropic/claude-sonnet-5"
    assert cfg.llm_temperature == 0.4
    assert cfg.bounds_default.min_s < cfg.bounds_default.max_s
    assert cfg.verbatim_floor >= MATCH_QUALITY_FLOOR


def test_config_populates_remote_asr_chain():
    cfg = load_planner_config()

    assert [entry.word_ts for entry in cfg.remote_asr_chain] == [
        "native",
        "verify",
        "forced",
        "forced",
    ]
    assert cfg.remote_asr_chain[0].response_format == "verbose_json"
    assert cfg.remote_asr_chain[0].request_word_timestamps is True


def test_config_rejects_verbatim_floor_below_dsl_floor():
    cfg = load_planner_config()

    with pytest.raises(ValidationError, match="verbatim_floor"):
        PlannerConfig.model_validate(
            {**cfg.model_dump(), "verbatim_floor": MATCH_QUALITY_FLOOR - 0.01}
        )
