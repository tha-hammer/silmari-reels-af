"""Hook/banner text and image moment helpers for finished real-footage reels."""

from __future__ import annotations

import inspect
import json
import math
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

CRISP_YTDLP_FORMAT = "137+140/137+bestaudio[ext=m4a]"
YTDLP_MERGE_OUTPUT_FORMAT = "mp4"
DEFAULT_HOOK_MAX_WORDS = 8
DEFAULT_IMAGE_COUNT = 3
DEFAULT_IMAGE_MOMENT_EDGE_S = 2.0
DEFAULT_IMAGE_MOMENT_DURATION_S = 2.5
DEFAULT_TEXT_MODEL = os.getenv(
    "REEL_AF_HOOK_MODEL",
    os.getenv("REEL_AF_TEXT_MODEL", "openrouter/google/gemini-2.5-flash"),
)

_TIME_EPSILON_S = 0.001


class HookDraft(BaseModel):
    hook: str


class ImageMomentDraft(BaseModel):
    t_start: float
    t_end: float
    image_prompt: str


class ImageMomentResponse(BaseModel):
    moments: list[ImageMomentDraft] = Field(default_factory=list)


class ImageMoment(NamedTuple):
    t_start: float
    t_end: float
    image_prompt: str


def build_crisp_ytdlp_command(
    source_url: str,
    output_path: str | Path,
    *,
    format_selector: str = CRISP_YTDLP_FORMAT,
    merge_output_format: str = YTDLP_MERGE_OUTPUT_FORMAT,
) -> list[str]:
    """Build the vertical-safe yt-dlp command used by the real-footage path."""

    source_url = str(source_url).strip()
    if not source_url:
        raise ValueError("source_url is required")
    output_path = Path(output_path)
    return [
        "yt-dlp",
        "-f",
        format_selector,
        "--merge-output-format",
        merge_output_format,
        "-o",
        str(output_path),
        source_url,
    ]


def download_crisp_source(
    source_url: str,
    output_path: str | Path,
    *,
    timeout_s: float | None = None,
    runner: Any = subprocess.run,
) -> Path:
    """Download a source video with the crisp vertical-safe selector."""

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_crisp_ytdlp_command(source_url, target)
    proc = runner(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if getattr(proc, "returncode", 0) != 0:
        stderr = str(getattr(proc, "stderr", ""))
        raise RuntimeError(
            "yt-dlp crisp download failed "
            f"(exit {getattr(proc, 'returncode', 'unknown')}): {stderr[-1200:]}"
        )
    return target


async def generate_hook(
    transcript: str,
    provider: Any,
    *,
    max_words: int = DEFAULT_HOOK_MAX_WORDS,
) -> str:
    """Generate and normalize a punchy banner hook for the reel."""

    if max_words <= 0:
        raise ValueError("max_words must be positive")
    transcript = _collapse_ws(transcript)
    if not transcript:
        raise ValueError("transcript is required")

    raw = await _request_text(
        provider,
        system=(
            "You write short hook banners for vertical reels. Return one concrete, "
            f"punchy hook with no hashtags and no preamble. Maximum {max_words} words."
        ),
        user=(
            "Transcript:\n"
            f"{_limit_chars(transcript, 5000)}\n\n"
            "Return JSON with exactly this shape: {\"hook\":\"...\"}."
        ),
        schema=HookDraft,
    )
    hook = _normalize_hook(_extract_text(raw, preferred_keys=("hook",)), max_words)
    if hook:
        return hook
    fallback = _truncate_words(transcript, max_words)
    if fallback:
        return fallback
    raise ValueError("provider returned an empty hook")


async def pick_image_moments(
    transcript: str,
    provider: Any,
    config: Any | None = None,
    *,
    duration_s: float | None = None,
    image_count: int | None = None,
) -> list[ImageMoment]:
    """Choose safe, non-overlapping image cut-in moments for a reel."""

    config, duration_s = _resolve_config_and_duration(config, duration_s)
    count = _image_count(config, image_count)
    if count == 0:
        return []
    duration_s = float(duration_s)
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("duration_s must be positive")

    edge_s = _float_config(
        config,
        (
            "image_edge_guard_s",
            "image_moment_edge_s",
            "image_edge_pad_s",
            "image_exclusion_s",
        ),
        DEFAULT_IMAGE_MOMENT_EDGE_S,
    )
    min_duration_s = _float_config(
        config,
        ("image_min_dur_s", "image_min_duration_s"),
        DEFAULT_IMAGE_MOMENT_DURATION_S,
    )
    max_duration_s = _float_config(
        config,
        (
            "image_max_dur_s",
            "image_max_duration_s",
        ),
        DEFAULT_IMAGE_MOMENT_DURATION_S,
    )
    if max_duration_s < min_duration_s:
        raise ValueError("image_max_dur_s must be >= image_min_dur_s")
    clip_duration_s = _configured_clip_duration(config, min_duration_s, max_duration_s)
    if duration_s <= (2 * edge_s) + (2 * _TIME_EPSILON_S):
        raise ValueError("duration_s is too short for the configured image edge padding")

    transcript = _collapse_ws(transcript)
    raw = await _request_text(
        provider,
        system=(
            "You select image cut-in moments for a vertical real-footage reel. "
            "Return concrete visual prompts for generated still images. Avoid "
            f"the first and last {edge_s:g} seconds. Moments must not overlap."
        ),
        user=(
            f"Reel duration seconds: {duration_s:.3f}\n"
            f"Image count: {count}\n"
            f"Transcript:\n{_limit_chars(transcript, 6000)}\n\n"
            "Return JSON with this shape: "
            "{\"moments\":[{\"t_start\":3.0,\"t_end\":5.5,"
            "\"image_prompt\":\"specific generated-image prompt\"}]}."
        ),
        schema=ImageMomentResponse,
    )
    drafts = _coerce_moment_candidates(raw)
    selected = _valid_provider_moments(drafts, count, duration_s, edge_s)
    if selected is not None:
        return selected

    prompts = _moment_prompts(drafts, transcript, count)
    windows = _evenly_spaced_windows(count, duration_s, edge_s, clip_duration_s)
    return [
        ImageMoment(t_start=start, t_end=end, image_prompt=prompt)
        for (start, end), prompt in zip(windows, prompts, strict=True)
    ]


async def _request_text(
    provider: Any,
    *,
    system: str,
    user: str,
    schema: type[BaseModel] | None = None,
) -> Any:
    if provider is None:
        raise TypeError("provider is required")

    if hasattr(provider, "ai"):
        return await _maybe_await(provider.ai(system=system, user=user, schema=schema))

    prompt = f"{system}\n\n{user}"
    for method_name in ("generate_text", "generate_completion", "complete"):
        method = getattr(provider, method_name, None)
        if method is None:
            continue
        try:
            return await _maybe_await(
                method(
                    prompt=prompt,
                    system=system,
                    user=user,
                    model=DEFAULT_TEXT_MODEL,
                )
            )
        except TypeError:
            return await _maybe_await(method(prompt))

    if callable(provider):
        return await _maybe_await(provider(prompt))

    raise TypeError(
        "provider must expose ai(...), generate_text(...), generate_completion(...), "
        "complete(...), or be callable"
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _extract_text(value: Any, *, preferred_keys: Sequence[str] = ()) -> str:
    value = _model_dump(value)
    if isinstance(value, str):
        return _text_from_json_or_raw(value, preferred_keys)
    if isinstance(value, Mapping):
        for key in (*preferred_keys, "text", "content", "response", "message"):
            found = value.get(key)
            if found is not None:
                return _extract_text(found, preferred_keys=preferred_keys)
        choices = value.get("choices")
        if choices:
            return _extract_text(choices[0], preferred_keys=preferred_keys)
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return _extract_text(value[0], preferred_keys=preferred_keys) if value else ""

    for key in (*preferred_keys, "text", "content", "response", "message"):
        if hasattr(value, key):
            return _extract_text(getattr(value, key), preferred_keys=preferred_keys)
    choices = getattr(value, "choices", None)
    if choices:
        return _extract_text(choices[0], preferred_keys=preferred_keys)
    return str(value) if value is not None else ""


def _text_from_json_or_raw(value: str, preferred_keys: Sequence[str]) -> str:
    stripped = _strip_code_fence(value)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    return _extract_text(parsed, preferred_keys=preferred_keys)


def _coerce_moment_candidates(value: Any) -> list[ImageMoment]:
    value = _model_dump(value)
    if isinstance(value, str):
        stripped = _strip_code_fence(value)
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return []
    value = _model_dump(value)
    if isinstance(value, Mapping):
        for key in ("moments", "picks", "image_moments", "items"):
            if key in value:
                value = value[key]
                break
        else:
            value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []

    moments: list[ImageMoment] = []
    for item in value:
        coerced = _coerce_one_moment(item)
        if coerced is not None:
            moments.append(coerced)
    return moments


def _coerce_one_moment(item: Any) -> ImageMoment | None:
    item = _model_dump(item)
    if isinstance(item, Mapping):
        start = _first_present(item, ("t_start", "start", "start_s", "time_start"))
        end = _first_present(item, ("t_end", "end", "end_s", "time_end"))
        prompt = _first_present(item, ("image_prompt", "prompt", "visual_prompt"))
    elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
        if len(item) < 3:
            return None
        start, end, prompt = item[0], item[1], item[2]
    else:
        start = _first_attr(item, ("t_start", "start", "start_s", "time_start"))
        end = _first_attr(item, ("t_end", "end", "end_s", "time_end"))
        prompt = _first_attr(item, ("image_prompt", "prompt", "visual_prompt"))

    prompt_text = _normalize_prompt(str(prompt or ""))
    if not prompt_text:
        return None
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        return None
    return ImageMoment(t_start=start_f, t_end=end_f, image_prompt=prompt_text)


def _valid_provider_moments(
    drafts: list[ImageMoment],
    count: int,
    duration_s: float,
    edge_s: float,
) -> list[ImageMoment] | None:
    if len(drafts) < count:
        return None
    selected = sorted(drafts[:count], key=lambda moment: moment.t_start)
    previous_end = -math.inf
    safe_start = edge_s + _TIME_EPSILON_S
    safe_end = duration_s - edge_s - _TIME_EPSILON_S
    for moment in selected:
        if moment.t_start <= safe_start or moment.t_end >= safe_end:
            return None
        if moment.t_start >= moment.t_end:
            return None
        if moment.t_start < previous_end:
            return None
        previous_end = moment.t_end
    return selected


def _moment_prompts(
    drafts: list[ImageMoment],
    transcript: str,
    count: int,
) -> list[str]:
    prompts = [moment.image_prompt for moment in drafts if moment.image_prompt.strip()]
    fallback = _fallback_image_prompt(transcript)
    while len(prompts) < count:
        prompts.append(fallback)
    return prompts[:count]


def _evenly_spaced_windows(
    count: int,
    duration_s: float,
    edge_s: float,
    clip_duration_s: float,
) -> list[tuple[float, float]]:
    safe_start = edge_s + _TIME_EPSILON_S
    safe_end = duration_s - edge_s - _TIME_EPSILON_S
    safe_span = safe_end - safe_start
    if safe_span <= 0:
        raise ValueError("no safe image moment window is available")

    slot_s = safe_span / count
    window_duration_s = min(max(0.1, clip_duration_s), max(0.1, slot_s * 0.8))
    windows: list[tuple[float, float]] = []
    for idx in range(count):
        center = safe_start + (slot_s * (idx + 0.5))
        start = max(safe_start, center - (window_duration_s / 2))
        end = start + window_duration_s
        if end > safe_end:
            end = safe_end
            start = end - window_duration_s
        windows.append((round(start, 3), round(end, 3)))
    return windows


def _image_count(config: Any | None, override: int | None) -> int:
    raw = override if override is not None else _config_value(config, "image_count", DEFAULT_IMAGE_COUNT)
    count = int(raw)
    if count < 0:
        raise ValueError("image_count must be non-negative")
    return count


def _resolve_config_and_duration(
    config: Any | None,
    duration_s: float | None,
) -> tuple[Any | None, float]:
    if duration_s is None and _is_number(config):
        return None, float(config)
    if duration_s is None:
        for key in ("duration_s", "reel_duration_s"):
            value = _config_value(config, key, None)
            if value is not None:
                return config, float(value)
    if duration_s is None:
        raise ValueError(
            "duration_s is required; call pick_image_moments(transcript, provider, cfg, "
            "duration_s=reel_duration)"
        )
    return config, float(duration_s)


def _configured_clip_duration(
    config: Any | None,
    min_duration_s: float,
    max_duration_s: float,
) -> float:
    configured = _config_value(config, "image_moment_duration_s", None)
    if configured is None:
        configured = _config_value(config, "image_cutin_duration_s", None)
    if configured is None:
        configured = _config_value(config, "image_overlay_duration_s", None)
    if configured is None:
        return (min_duration_s + max_duration_s) / 2
    configured_f = float(configured)
    if not math.isfinite(configured_f) or configured_f < 0:
        raise ValueError("configured image moment duration must be non-negative and finite")
    return min(max(configured_f, min_duration_s), max_duration_s)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _float_config(config: Any | None, keys: Sequence[str], default: float) -> float:
    for key in keys:
        value = _config_value(config, key, None)
        if value is not None:
            value_f = float(value)
            if not math.isfinite(value_f) or value_f < 0:
                raise ValueError(f"{key} must be a non-negative finite number")
            return value_f
    return default


def _config_value(config: Any | None, key: str, default: Any) -> Any:
    if config is None:
        return default
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def _first_present(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _first_attr(item: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        if hasattr(item, key):
            return getattr(item, key)
    return None


def _model_dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def _normalize_hook(text: str, max_words: int) -> str:
    text = _strip_code_fence(text)
    text = re.sub(r"(?is)^\s*(hook|banner|headline)\s*[:\-]\s*", "", text)
    text = text.strip().strip("\"'`")
    text = re.sub(r"^\s*[-*•]\s*", "", text)
    text = _collapse_ws(text)
    return _truncate_words(text, max_words)


def _normalize_prompt(text: str) -> str:
    text = _strip_code_fence(text)
    text = text.strip().strip("\"'`")
    return _collapse_ws(text)


def _fallback_image_prompt(transcript: str) -> str:
    core = _truncate_words(_collapse_ws(transcript), 18)
    if core:
        return f"editorial still illustrating this reel beat: {core}"
    return "editorial still illustrating the central claim of the reel"


def _truncate_words(text: str, max_words: int) -> str:
    words = _collapse_ws(text).split()
    return " ".join(words[:max_words])


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _strip_code_fence(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _limit_chars(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


__all__ = [
    "CRISP_YTDLP_FORMAT",
    "YTDLP_MERGE_OUTPUT_FORMAT",
    "ImageMoment",
    "build_crisp_ytdlp_command",
    "download_crisp_source",
    "generate_hook",
    "pick_image_moments",
]
