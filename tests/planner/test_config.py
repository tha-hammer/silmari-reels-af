from __future__ import annotations

import pytest
from pydantic import ValidationError

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
