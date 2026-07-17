"""Hook/banner text and image moment helpers for finished real-footage reels."""

from __future__ import annotations

import base64
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

CRISP_YTDLP_FORMAT = "137+140/137+bestaudio[ext=m4a]"
GENERIC_YTDLP_FORMAT = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
YTDLP_MERGE_OUTPUT_FORMAT = "mp4"

# yt-dlp format ladders and the JS runtime are protocol selectors, not user
# preferences — named module constants beside CRISP_YTDLP_FORMAT, never JSON.
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
VIMEO_HOSTS = ("vimeo.com",)
_SCHEMELESS_HOSTS = YOUTUBE_HOSTS + VIMEO_HOSTS
_FORMAT_BY_HOST = {
    "youtube": CRISP_YTDLP_FORMAT,
    "vimeo": GENERIC_YTDLP_FORMAT,
}
YOUTUBE_JS_RUNTIME = "deno"

# Environment/filesystem resolution lives in the wrapper, not the builder.
YTDLP_COOKIES_FILE_ENV = "YTDLP_COOKIES_FILE"
# Base64 of a Netscape cookies.txt, materialized to a temp file at download time.
# The secret-friendly alternative to YTDLP_COOKIES_FILE for volume-less containers
# (Path B): store the export as a Railway secret, no on-disk/volume file to manage.
YTDLP_COOKIES_B64_ENV = "YTDLP_COOKIES_B64"
# Full proxy URL (e.g. http://user:pass@brd.superproxy.io:33335) routing the fetch
# through a residential/ISP egress IP — the datacenter-IP 403/429 fix (Path C).
YTDLP_PROXY_ENV = "YTDLP_PROXY_URL"
_MATERIALIZED_COOKIES_PATH = "/tmp/reel-af-ytdlp-cookies.txt"
# A source that is already a direct media file (e.g. a presigned bucket object A1
# pre-downloaded on a residential machine) is fetched with a plain GET — no
# extractor, no bot-check (Path A).
_DIRECT_MEDIA_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
YTDLP_DOWNLOAD_TIMEOUT_S = 600.0
YTDLP_ERROR_TAIL_CHARS = 1200
_BOT_MARKERS = ("sign in to confirm", "not a bot", "--cookies")
_JS_RUNTIME_MARKERS = ("no supported javascript runtime", "--js-runtimes")
# googlevideo intermittently 403s media fetches from datacenter IPs (~1 in 5); these
# are transient and clear on a fresh attempt, unlike bot-check / js-runtime failures.
DOWNLOAD_MAX_ATTEMPTS = 4
DOWNLOAD_RETRY_BACKOFF_S = 2.0
_TRANSIENT_DOWNLOAD_MARKERS = (
    "http error 403",
    "403: forbidden",
    "unable to download video data",
    "unable to download webpage",
    "connection reset",
    "connection aborted",
    "read timed out",
    "temporary failure",
    "unable to connect",
    "giving up after",
)
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
    model_config = ConfigDict(extra="forbid")
    hook: str


class ImageMomentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t_start: float
    t_end: float
    image_prompt: str


class ImageMomentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    moments: list[ImageMomentDraft]


class ImageMoment(NamedTuple):
    t_start: float
    t_end: float
    image_prompt: str


def _host_matches(host: str, candidates: tuple[str, ...]) -> bool:
    """Exact-or-dot-boundary host match; never a bare ``endswith(candidate)``."""
    return any(host == candidate or host.endswith("." + candidate) for candidate in candidates)


def _normalize_source_url(source_url: str) -> str:
    """Return a schemeful URL, upgrading scheme-less known hosts to ``https://``."""
    raw = str(source_url).strip()
    if not raw:
        raise ValueError("source_url is required")

    parsed = urlparse(raw)
    if parsed.scheme:
        if not parsed.hostname:
            raise ValueError("source_url must include a host")
        return raw

    first_segment = raw.split("/", 1)[0].lower()
    if _host_matches(first_segment, _SCHEMELESS_HOSTS):
        return f"https://{raw}"

    raise ValueError("source_url must include a scheme and host")


def _classify_host(source_url: str) -> str:
    """Classify a source URL as ``"youtube"``, ``"vimeo"``, or ``"generic"``."""
    normalized = _normalize_source_url(source_url)
    host = (urlparse(normalized).hostname or "").lower()
    if _host_matches(host, YOUTUBE_HOSTS):
        return "youtube"
    if _host_matches(host, VIMEO_HOSTS):
        return "vimeo"
    return "generic"


def _host_flags(host_kind: str, cookies_file: str | Path | None) -> list[str]:
    """Assemble host-specific yt-dlp flags (JS runtime, cookies) in one place."""
    flags: list[str] = []
    if host_kind == "youtube":
        flags.extend(["--js-runtimes", YOUTUBE_JS_RUNTIME])
    if cookies_file is not None:
        flags.extend(["--cookies", str(cookies_file)])
    return flags


def build_crisp_ytdlp_command(
    source_url: str,
    output_path: str | Path,
    *,
    format_selector: str | None = None,
    merge_output_format: str = YTDLP_MERGE_OUTPUT_FORMAT,
    cookies_file: str | Path | None = None,
    proxy: str | None = None,
) -> list[str]:
    """Build the vertical-safe yt-dlp command used by the real-footage path."""

    normalized_url = _normalize_source_url(source_url)
    host_kind = _classify_host(normalized_url)
    selected_format = (
        format_selector
        if format_selector is not None
        else _FORMAT_BY_HOST.get(host_kind, GENERIC_YTDLP_FORMAT)
    )
    target = Path(output_path)
    proxy_flags = ["--proxy", proxy] if proxy else []
    return [
        "yt-dlp",
        "-f",
        selected_format,
        "--merge-output-format",
        merge_output_format,
        *proxy_flags,
        *_host_flags(host_kind, cookies_file),
        "-o",
        str(target),
        normalized_url,
    ]


def _resolve_proxy_from_env() -> str | None:
    """The residential/ISP proxy URL to egress through, or ``None`` (direct)."""
    proxy = (os.getenv(YTDLP_PROXY_ENV) or "").strip()
    return proxy or None


def _resolve_cookies_file_from_env() -> Path | None:
    """Resolve cookies for yt-dlp, or ``None`` (no cookies).

    ``YTDLP_COOKIES_FILE`` (a path) takes precedence and raises if set-but-missing.
    Otherwise ``YTDLP_COOKIES_B64`` (base64 of a Netscape cookies.txt) is decoded and
    materialized to a temp file (Path B) — the secret-friendly source for volume-less
    containers, where the export lives in a Railway secret, not on disk."""
    configured = (os.getenv(YTDLP_COOKIES_FILE_ENV) or "").strip()
    if configured:
        cookies_path = Path(configured)
        if not cookies_path.is_file():
            raise RuntimeError(f"{YTDLP_COOKIES_FILE_ENV} is set but not a file: {configured!r}")
        return cookies_path

    encoded = (os.getenv(YTDLP_COOKIES_B64_ENV) or "").strip()
    if not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as exc:  # binascii.Error subclasses ValueError
        raise RuntimeError(f"{YTDLP_COOKIES_B64_ENV} is not valid base64") from exc
    materialized = Path(_MATERIALIZED_COOKIES_PATH)
    materialized.write_bytes(decoded)
    return materialized


def _download_failure_hint(stderr: str) -> str:
    """Map a yt-dlp stderr tail to an actionable operator hint (or empty string)."""
    lower = stderr.lower()
    hints: list[str] = []
    if any(marker in lower for marker in _BOT_MARKERS):
        hints.append(
            f"Set {YTDLP_COOKIES_FILE_ENV} to a valid Netscape-format cookies export."
        )
    if any(marker in lower for marker in _JS_RUNTIME_MARKERS):
        hints.append("Install deno in the image and keep --js-runtimes deno enabled.")
    if not hints:
        return ""
    return " " + " ".join(hints)


def _remove_partial_outputs(target: Path) -> None:
    """Delete the target and the known yt-dlp ``.part`` sibling if present."""
    for candidate in (target, target.with_name(target.name + ".part")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _is_transient_download_error(stderr: str) -> bool:
    """A failure worth re-attempting: datacenter-IP 403s and transient network
    errors clear on a fresh yt-dlp invocation. Bot-check and js-runtime failures
    do NOT — retrying them just wastes the render budget (they need cookies/deno)."""
    lower = stderr.lower()
    if any(marker in lower for marker in _BOT_MARKERS):
        return False
    if any(marker in lower for marker in _JS_RUNTIME_MARKERS):
        return False
    return any(marker in lower for marker in _TRANSIENT_DOWNLOAD_MARKERS)


def download_crisp_source(
    source_url: str,
    output_path: str | Path,
    *,
    timeout_s: float | None = YTDLP_DOWNLOAD_TIMEOUT_S,
    runner: Any = subprocess.run,
) -> Path:
    """Download a source video with the crisp vertical-safe selector.

    Owns environment/file resolution (``YTDLP_COOKIES_FILE``), the bounded
    default timeout, partial-output cleanup, and actionable error messages. The
    subprocess timeout is the hard execution bound; thread-backed callers get
    only best-effort cancellation.
    """

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cookies_file = _resolve_cookies_file_from_env()
    proxy = _resolve_proxy_from_env()
    cmd = build_crisp_ytdlp_command(source_url, target, cookies_file=cookies_file, proxy=proxy)

    last_stderr = ""
    last_returncode: Any = "unknown"
    for attempt in range(1, DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            _remove_partial_outputs(target)
            timeout_label = "the configured timeout" if timeout_s is None else f"{timeout_s:g}s"
            raise RuntimeError(
                f"yt-dlp crisp download timed out after {timeout_label}"
            ) from exc

        if getattr(proc, "returncode", 0) == 0:
            return target

        _remove_partial_outputs(target)
        last_stderr = str(getattr(proc, "stderr", ""))
        last_returncode = getattr(proc, "returncode", "unknown")
        if attempt < DOWNLOAD_MAX_ATTEMPTS and _is_transient_download_error(last_stderr):
            time.sleep(DOWNLOAD_RETRY_BACKOFF_S)  # let the transient 403/throttle clear
            continue
        break

    tail = last_stderr[-YTDLP_ERROR_TAIL_CHARS:]
    hint = _download_failure_hint(last_stderr)
    raise RuntimeError(
        f"yt-dlp crisp download failed after {attempt} attempt(s) "
        f"(exit {last_returncode}): {tail}{hint}"
    )


def _is_direct_media_url(source_url: str) -> bool:
    """True when the source is already a direct media file (generic host + media
    extension) — a pre-fetched clip to GET, not a page to run an extractor on."""
    normalized = _normalize_source_url(source_url)
    path = urlparse(normalized).path.lower()
    return _classify_host(normalized) == "generic" and path.endswith(_DIRECT_MEDIA_EXTENSIONS)


def download_direct_source(
    source_url: str,
    output_path: str | Path,
    *,
    timeout_s: float | None = YTDLP_DOWNLOAD_TIMEOUT_S,
    opener: Any = urllib.request.urlopen,
) -> Path:
    """Stream a direct media URL to disk (Path A). No yt-dlp / extractor / cookies /
    bot-check — the bytes were already fetched residentially and uploaded."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_source_url(source_url)
    try:
        with opener(normalized, timeout=timeout_s) as response, open(target, "wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:  # noqa: BLE001 - any network/HTTP error is a bounded failure
        _remove_partial_outputs(target)
        raise RuntimeError(f"direct source download failed: {exc}") from exc
    return target


def download_source(source_url: str, output_path: str | Path, **kwargs: Any) -> Path:
    """Acquire the source video by the right strategy: a direct media URL (Path A)
    is streamed with a plain GET; anything else goes through the crisp yt-dlp path
    (with its env-driven cookies (Path B) and proxy (Path C))."""
    if _is_direct_media_url(source_url):
        return download_direct_source(source_url, output_path)
    return download_crisp_source(source_url, output_path, **kwargs)


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
    "GENERIC_YTDLP_FORMAT",
    "YTDLP_COOKIES_FILE_ENV",
    "YTDLP_DOWNLOAD_TIMEOUT_S",
    "YTDLP_MERGE_OUTPUT_FORMAT",
    "ImageMoment",
    "build_crisp_ytdlp_command",
    "download_crisp_source",
    "download_direct_source",
    "download_source",
    "generate_hook",
    "pick_image_moments",
]
