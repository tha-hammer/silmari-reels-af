"""Planner config schema and loader for the A1 producer."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG_PATH = Path(__file__).parents[1] / "render" / "config" / "planner.json"


@lru_cache(maxsize=1)
def load_planner_defaults() -> dict[str, Any]:
    """The planner config dictionary, loaded once from ``render/config/planner.json``."""
    return json.loads(_CONFIG_PATH.read_text())


_D = load_planner_defaults()


def _v(key: str, **kwargs: Any) -> Any:
    """A pydantic field default sourced from the JSON config."""
    return Field(default_factory=lambda: _D[key], **kwargs)


class AsrEntry(BaseModel):
    """Optional remote-ASR entry; local whisper remains the default producer path."""

    model_config = ConfigDict(extra="forbid")

    model: str
    word_ts: Literal["native", "verify", "forced"]


class PlannerConfig(BaseModel):
    """Typed planner tunables. Values come from ``render/config/planner.json``."""

    model_config = ConfigDict(extra="forbid")

    model: str = _v("model")
    default_register: Literal["entertainment", "educational", "b2b"] = _v("default_register")
    max_repair_passes: int = _v("max_repair_passes", ge=0)
    self_verify: bool = _v("self_verify")
    remote_asr_chain: list[AsrEntry] = _v("remote_asr_chain")

    r1_hook_window_s: float = _v("r1_hook_window_s", gt=0)
    r2_cadence_s: dict[str, float] = _v("r2_cadence_s")
    r4_max_gap_s: float = _v("r4_max_gap_s", gt=0)
    r8_min_token_overlap: float = _v("r8_min_token_overlap", ge=0, le=1)
    r11_bait_patterns: list[str] = _v("r11_bait_patterns", min_length=1)


@lru_cache(maxsize=1)
def load_planner_config() -> PlannerConfig:
    """Load and validate the planner config once."""
    return PlannerConfig.model_validate(load_planner_defaults())


__all__ = [
    "AsrEntry",
    "PlannerConfig",
    "load_planner_config",
    "load_planner_defaults",
]
