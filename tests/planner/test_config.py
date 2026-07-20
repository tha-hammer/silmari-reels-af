from __future__ import annotations

import pytest
from pydantic import ValidationError

from reel_af.dsl.models import MATCH_QUALITY_FLOOR
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.pipeline import _duration_policy


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


def test_config_contains_content_driven_length_defaults():
    cfg = load_planner_config()

    assert cfg.r7_soft_cap_s == 180.0
    assert cfg.r7_cap_tolerance_s == 3.0
    assert cfg.max_candidates >= 160
    assert cfg.max_beats >= 48
    assert cfg.mine_window_duration_s > cfg.mine_window_overlap_s > 0
    assert cfg.mine_candidates_per_window > 0
    assert cfg.mine_max_windows > 0


def test_duration_policy_treats_bounds_as_advisory_until_explicit_override():
    cfg = load_planner_config()

    default = _duration_policy(None, cfg)
    requested_long = _duration_policy({"min_s": 120, "max_s": 180}, cfg)
    override = _duration_policy({"min_s": 180, "max_s": 240}, cfg)

    assert default.effective_cap_s == 180.0
    assert default.cap_overridden is False
    assert requested_long.advisory_min_s == 120.0
    assert requested_long.advisory_max_s == 180.0
    assert requested_long.effective_cap_s == 180.0
    assert requested_long.cap_overridden is False
    assert override.effective_cap_s == 240.0
    assert override.cap_overridden is True


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
