"""Canonical generated-output paths for planner resources and eval artifacts."""

from __future__ import annotations

import os
from pathlib import Path

from reel_af.planner.config import PlannerConfig, load_planner_config

REEL_AF_OUTPUT_ROOT_ENV = "REEL_AF_OUTPUT_ROOT"
DEFAULT_OUTPUT_ROOT = "resources"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_output_root(
    out_dir: str | Path | None = None,
    *,
    cfg: PlannerConfig | None = None,
) -> Path:
    """Resolve the family output root containing both runs/ and evals/."""

    if out_dir is not None:
        return Path(out_dir)

    env_root = os.getenv(REEL_AF_OUTPUT_ROOT_ENV)
    if env_root:
        return _project_relative_path(env_root)

    selected = cfg or load_planner_config()
    return _project_relative_path(selected.output_root or DEFAULT_OUTPUT_ROOT)


def runs_dir(
    workflow: str,
    run_id: str,
    *,
    out_dir: str | Path | None = None,
    cfg: PlannerConfig | None = None,
) -> Path:
    """Return the concrete run artifact directory for a workflow/run id pair."""

    if out_dir is not None:
        return Path(out_dir)

    selected = cfg or load_planner_config()
    return (
        resolve_output_root(cfg=selected)
        / selected.artifacts_dir
        / f"{_component(workflow)}-{_component(run_id)}"
    )


def evals_dir(
    *,
    out_dir: str | Path | None = None,
    cfg: PlannerConfig | None = None,
) -> Path:
    """Return the concrete eval output directory."""

    if out_dir is not None:
        return Path(out_dir)

    selected = cfg or load_planner_config()
    return resolve_output_root(cfg=selected) / selected.evals_dir


def _project_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _PROJECT_ROOT / path


def _component(value: str) -> str:
    component = str(value).strip().replace("/", "-").replace("\\", "-")
    return component or "run"


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "REEL_AF_OUTPUT_ROOT_ENV",
    "evals_dir",
    "resolve_output_root",
    "runs_dir",
]
