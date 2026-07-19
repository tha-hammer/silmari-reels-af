"""Planner config schema and loader for the A1 producer."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reel_af.dsl.models import MATCH_QUALITY_FLOOR
from reel_af.planner.models import DurationBounds

_CONFIG_PATH = Path(__file__).parents[1] / "render" / "config" / "planner.json"


@lru_cache(maxsize=1)
def load_planner_defaults() -> dict[str, Any]:
    """The planner config dictionary, loaded once from ``render/config/planner.json``."""
    return json.loads(_CONFIG_PATH.read_text())


_D = load_planner_defaults()


def _v(key: str, **kwargs: Any) -> Any:
    """A pydantic field default sourced from the JSON config."""
    return Field(default_factory=lambda: deepcopy(_D[key]), **kwargs)


class AsrEntry(BaseModel):
    """Optional remote-ASR entry; local whisper remains the default producer path."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: str
    word_ts: Literal["native", "verify", "forced"]
    response_format: str = Field(min_length=1)
    request_word_timestamps: bool
    retry_policy: str | None = None
    capability_notes: str | None = None


class PlannerConfig(BaseModel):
    """Typed planner tunables. Values come from ``render/config/planner.json``."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: str = _v("model")
    llm_temperature: float = _v("llm_temperature", ge=0, le=2)
    llm_connect_timeout_s: float = _v("llm_connect_timeout_s", gt=0)
    llm_request_timeout_s: float = _v("llm_request_timeout_s", gt=0)
    llm_total_timeout_s: float = _v("llm_total_timeout_s", gt=0)
    default_register: Literal["entertainment", "educational", "b2b"] = _v("default_register")
    bounds_default: DurationBounds = _v("bounds_default", validate_default=True)
    max_repair_passes: int = _v("max_repair_passes", ge=0)
    verbatim_floor: float = _v("verbatim_floor", ge=0, le=1)
    max_transcript_chars: int = _v("max_transcript_chars", gt=0)
    max_candidates: int = _v("max_candidates", gt=0)
    max_beats: int = _v("max_beats", gt=0)
    max_repair_hint_chars: int = _v("max_repair_hint_chars", gt=0)
    max_audio_bytes: int = _v("max_audio_bytes", gt=0)
    max_audio_duration_s: float = _v("max_audio_duration_s", gt=0)
    asr_chunk_duration_s: float = _v("asr_chunk_duration_s", gt=0)
    asr_connect_timeout_s: float = _v("asr_connect_timeout_s", gt=0)
    asr_request_timeout_s: float = _v("asr_request_timeout_s", gt=0)
    asr_total_timeout_s: float = _v("asr_total_timeout_s", gt=0)
    self_verify: bool = _v("self_verify")
    remote_asr_chain: list[AsrEntry] = _v("remote_asr_chain")
    allow_local_only_asr: bool = False

    r1_hook_window_s: float = _v("r1_hook_window_s", gt=0)
    r2_cadence_s: dict[str, float] = _v("r2_cadence_s")
    r4_max_gap_s: float = _v("r4_max_gap_s", gt=0)
    r8_min_token_overlap: float = _v("r8_min_token_overlap", ge=0, le=1)
    r11_bait_patterns: list[str] = _v("r11_bait_patterns", min_length=1)

    @field_validator("bounds_default", mode="before")
    @classmethod
    def _coerce_bounds_default(cls, value: Any) -> DurationBounds:
        if isinstance(value, DurationBounds):
            return value
        if isinstance(value, dict):
            if "min_s" in value and "max_s" in value:
                return DurationBounds(min_s=float(value["min_s"]), max_s=float(value["max_s"]))
            if "min" in value and "max" in value:
                return DurationBounds(min_s=float(value["min"]), max_s=float(value["max"]))
        return DurationBounds.model_validate(value)

    @model_validator(mode="after")
    def _validate_runtime_contracts(self) -> "PlannerConfig":
        if self.verbatim_floor < MATCH_QUALITY_FLOOR:
            raise ValueError(
                f"verbatim_floor must be >= MATCH_QUALITY_FLOOR ({MATCH_QUALITY_FLOOR})"
            )
        bounds_default = self._coerce_bounds_default(self.bounds_default)
        if bounds_default is not self.bounds_default:
            self.bounds_default = bounds_default
        if (
            not math.isfinite(bounds_default.min_s)
            or not math.isfinite(bounds_default.max_s)
            or bounds_default.min_s < 0
            or bounds_default.max_s <= bounds_default.min_s
        ):
            raise ValueError("bounds_default must contain finite ordered min_s/max_s values")
        if not self.allow_local_only_asr and not self.remote_asr_chain:
            raise ValueError("remote_asr_chain must not be empty unless allow_local_only_asr=true")
        return self


@lru_cache(maxsize=1)
def load_planner_config() -> PlannerConfig:
    """Load and validate the planner config once."""
    return PlannerConfig.model_validate(load_planner_defaults())


__all__ = [
    "AsrEntry",
    "DurationBounds",
    "PlannerConfig",
    "load_planner_config",
    "load_planner_defaults",
]
