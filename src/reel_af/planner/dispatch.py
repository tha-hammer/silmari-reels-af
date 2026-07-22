"""Dispatch helpers for A1 hook-plan clips."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

from reel_af.planner.serialize import HOOKS_TARGET


class DslHookCpInput(TypedDict):
    source_url: str
    composite_ref: str
    words_ref: str
    hook_ref: str
    clip_idx: int


class DslHookDispatch(TypedDict):
    idx: int
    idempotency_key: str
    target: Literal["reel-af.reel_dsl_hooks_to_reels"]
    cp_input: DslHookCpInput


DispatchAsync = Callable[[str, dict[str, DslHookCpInput], dict[str, Any]], str]
FetchBytes = Callable[[str], bytes]


def load_hook_plan_for_dispatch(
    hook_ref: Mapping[str, Any] | str | Path,
    *,
    fetch_bytes: FetchBytes | None = None,
) -> Mapping[str, Any]:
    """Load a hook-plan from an already parsed dict, local path, or HTTPS ref."""

    if isinstance(hook_ref, Mapping):
        return hook_ref

    ref = str(hook_ref)
    parsed = urlparse(ref)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if fetch_bytes is None:
            raise ValueError("fetch_bytes is required to load HTTPS hook_ref")
        return _decode_hook_plan(fetch_bytes(ref), source=ref)

    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"unsupported hook_ref scheme for dispatch loading: {parsed.scheme}")

    path = Path(parsed.path if parsed.scheme == "file" else ref)
    return _decode_hook_plan(path.read_bytes(), source=str(path))


def build_dsl_hook_dispatches(
    *,
    source_url: str,
    words_ref: str,
    hook_ref: str,
    hook_plan: Mapping[str, Any],
) -> list[DslHookDispatch]:
    """Build one renderer dispatch payload per hook-plan clip."""

    clips = hook_plan.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("hook plan must contain at least one clip")

    dispatches: list[DslHookDispatch] = []
    seen: set[int] = set()
    for position, raw_clip in enumerate(clips, start=1):
        if not isinstance(raw_clip, Mapping):
            raise ValueError(f"clip {position} must be an object")
        clip = _validate_clip(raw_clip, position=position)
        idx = clip["idx"]
        if idx in seen:
            raise ValueError(f"duplicate clip idx: {idx}")
        seen.add(idx)
        dispatches.append(
            {
                "idx": idx,
                "idempotency_key": clip["idempotency_key"],
                "target": HOOKS_TARGET,
                "cp_input": {
                    "source_url": str(source_url),
                    "composite_ref": clip["composite_ref"],
                    "words_ref": str(words_ref),
                    "hook_ref": str(hook_ref),
                    "clip_idx": idx,
                },
            }
        )

    return sorted(dispatches, key=lambda dispatch: dispatch["idx"])


def dispatch_dsl_hook_clips(
    *,
    source_url: str,
    words_ref: str,
    hook_ref: str,
    hook_plan: Mapping[str, Any],
    dispatch_async: DispatchAsync,
) -> dict[str, list[dict[str, Any]]]:
    """Dispatch each hook-plan clip to the single-clip renderer target."""

    clip_dispatches: list[dict[str, Any]] = []
    for item in build_dsl_hook_dispatches(
        source_url=source_url,
        words_ref=words_ref,
        hook_ref=hook_ref,
        hook_plan=hook_plan,
    ):
        metadata = {
            "idx": item["idx"],
            "idempotency_key": item["idempotency_key"],
        }
        execution_id = dispatch_async(
            item["target"],
            {"input": item["cp_input"]},
            metadata,
        )
        clip_dispatches.append({**metadata, "execution_id": execution_id})
    return {"clip_dispatches": clip_dispatches}


def _decode_hook_plan(payload: bytes, *, source: str) -> Mapping[str, Any]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid hook plan JSON: {source}") from exc
    if not isinstance(data, Mapping):
        raise ValueError(f"hook plan must be an object: {source}")
    return data


def _validate_clip(clip: Mapping[str, Any], *, position: int) -> dict[str, Any]:
    idx = clip.get("idx")
    if isinstance(idx, bool) or not isinstance(idx, int):
        raise ValueError(f"clip {position} idx must be an integer")
    if idx < 1:
        raise ValueError(f"clip {idx} idx must be >= 1")

    target = clip.get("target")
    if target != HOOKS_TARGET:
        raise ValueError(f"clip {idx} target must be {HOOKS_TARGET}")

    composite_ref = _required_str(clip.get("composite_ref"), f"clip {idx} composite_ref")
    _validate_hosted_or_a1_ref(composite_ref, label=f"clip {idx} composite_ref")

    idempotency_key = _required_str(
        clip.get("idempotency_key"),
        f"clip {idx} idempotency_key",
    )
    return {
        "idx": idx,
        "target": target,
        "composite_ref": composite_ref,
        "idempotency_key": idempotency_key,
    }


def _required_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _validate_hosted_or_a1_ref(ref: str, *, label: str) -> None:
    if ref.startswith("a1://"):
        if len(ref) == len("a1://"):
            raise ValueError(f"{label} must include an a1:// path")
        return

    parsed = urlparse(ref)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return

    raise ValueError(f"{label} must be a hosted URL or a1:// ref")


__all__ = [
    "HOOKS_TARGET",
    "DslHookDispatch",
    "build_dsl_hook_dispatches",
    "dispatch_dsl_hook_clips",
    "load_hook_plan_for_dispatch",
]
