"""Transcript ingest helpers for the A1 planner producer.

The public functions in this module return the same sidecar shape consumed by
``reel_af.dsl.models.WordsSidecar``. Validation is intentionally delegated to
that DSL model so planner ingest does not carry a second copy of the invariant.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from reel_af.dsl.models import WordsSidecar

WHISPER_MODEL = "medium.en"
_YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be")

SidecarPayload = Mapping[str, Any] | WordsSidecar
CaptionRunner = Callable[[str], str | SidecarPayload | None]
WhisperRunner = Callable[[str], SidecarPayload]


def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def youtube_id(source: str) -> str | None:
    """Return a YouTube video id for supported YouTube URLs, else ``None``."""
    try:
        parsed = urlparse(source)
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None
    if host in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/").split("/")[0] or None
    if parsed.path.startswith(("/shorts/", "/embed/")):
        parts = parsed.path.split("/")
        return parts[2] if len(parts) > 2 else None
    return (parse_qs(parsed.query).get("v") or [None])[0]


def _fmt_ts(seconds: float) -> str:
    hours, rem = divmod(max(seconds, 0.0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"


def _parse_vtt_ts(raw: str) -> float:
    parts = raw.replace(",", ".").split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid VTT timestamp: {raw!r}")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def segments_to_vtt(segments: list[dict[str, Any]]) -> str:
    """Convert YouTube transcript-api segments into WEBVTT text."""
    lines = ["WEBVTT", ""]
    for segment in segments:
        start = float(segment["start"])
        end = start + float(segment.get("duration", 0.0))
        text = re.sub(r"\s+", " ", str(segment["text"])).strip()
        if text:
            lines.extend([f"{_fmt_ts(start)} --> {_fmt_ts(end)}", text, ""])
    return "\n".join(lines)


def vtt_to_segments(vtt: str) -> list[dict[str, float | str]]:
    """Convert cue-level WEBVTT text into fallback sidecar segments."""
    segments: list[dict[str, float | str]] = []
    lines = vtt.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        start_raw, end_raw = line.split("-->", 1)
        start_s = _parse_vtt_ts(start_raw.strip())
        end_s = _parse_vtt_ts(end_raw.strip().split()[0])
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = re.sub(r"\s+", " ", " ".join(text_lines)).strip()
        if text:
            segments.append({"text": text, "start_s": start_s, "end_s": end_s})
    return segments


def vtt_to_text(vtt: str) -> str:
    """Flatten WEBVTT cue text into transcript text."""
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def sidecar_from_vtt(vtt: str) -> dict[str, Any]:
    """Build a segments-only ``WordsSidecar`` payload from WEBVTT captions."""
    return {"schema_version": "1", "words": [], "segments": vtt_to_segments(vtt)}


def sidecar_from_whisper_json(path: Path | str, vtt: str) -> dict[str, Any]:
    """Build a ``WordsSidecar`` payload from whisper-ctranslate2 JSON."""
    data = json.loads(Path(path).read_text())
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for segment in data.get("segments", []):
        text = re.sub(r"\s+", " ", str(segment.get("text", ""))).strip()
        start = segment.get("start")
        end = segment.get("end")
        if text and start is not None and end is not None:
            segments.append({"text": text, "start_s": float(start), "end_s": float(end)})

        for raw_word in segment.get("words", []) or []:
            token = str(raw_word.get("word", raw_word.get("text", raw_word.get("w", "")))).strip()
            if not token:
                continue
            item: dict[str, Any] = {
                "w": token,
                "start": float(raw_word["start"]),
                "end": float(raw_word["end"]),
            }
            conf = raw_word.get("conf", raw_word.get("probability"))
            if conf is not None:
                item["conf"] = float(conf)
            words.append(item)

    if not segments:
        segments = sidecar_from_vtt(vtt)["segments"]
    return {"schema_version": "1", "words": words, "segments": segments}


def fetch_captions_vtt(video_id: str) -> str | None:
    """Fetch YouTube captions as WEBVTT text, returning ``None`` when unavailable."""
    try:
        proc = _run(
            [
                "uvx",
                "--from",
                "youtube-transcript-api",
                "youtube_transcript_api",
                video_id,
                "--format",
                "json",
            ],
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    segments = data[0] if data and isinstance(data[0], list) else data
    return segments_to_vtt(segments) if segments else None


def whisper_vtt(media: Path | str, run_dir: Path | str, *, model: str = WHISPER_MODEL) -> str:
    """Extract mono WAV and run local whisper-ctranslate2 with word timestamps."""
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    wav = run_path / "audio.wav"
    _run(
        ["ffmpeg", "-y", "-i", str(media), "-ac", "1", "-ar", "16000", "-vn", str(wav)],
        capture_output=True,
    )
    _run(
        [
            "uvx",
            "--from",
            "whisper-ctranslate2",
            "whisper-ctranslate2",
            str(wav),
            "--model",
            model,
            "--device",
            "cpu",
            "--compute_type",
            "int8",
            "--output_dir",
            str(run_path),
            "--output_format",
            "all",
            "--word_timestamps",
            "True",
            "--verbose",
            "False",
        ],
        capture_output=True,
    )
    return (run_path / "audio.vtt").read_text()


def _coerce_caption_payload(result: str | SidecarPayload) -> dict[str, Any]:
    if isinstance(result, str):
        return sidecar_from_vtt(result)
    if isinstance(result, WordsSidecar):
        return result.model_dump()
    return dict(result)


def _coerce_whisper_payload(result: SidecarPayload) -> dict[str, Any]:
    if isinstance(result, WordsSidecar):
        return result.model_dump()
    return dict(result)


def _has_word_timing(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("words"))


def _default_whisper(source: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="reel_af_planner_whisper_") as tmp:
        workdir = Path(tmp)
        media: Path | str = source
        if youtube_id(source) is not None:
            media = workdir / "source.m4a"
            _run(["yt-dlp", "-f", "bestaudio", "-o", str(media), source], capture_output=True)
        vtt = whisper_vtt(media, workdir)
        return sidecar_from_whisper_json(workdir / "audio.json", vtt)


def transcribe(
    source: str,
    *,
    run_caption: CaptionRunner = fetch_captions_vtt,
    run_whisper: WhisperRunner | None = None,
    allow_coarse: bool = False,
) -> WordsSidecar:
    """Return word-level transcript timing for a YouTube URL or local media path.

    YouTube captions are only final when they already carry word timing or when
    the caller explicitly opts into coarse cue timing. Plain VTT captions fall
    through to whisper so downstream quote alignment can meet the quality floor.
    """
    video_id = youtube_id(source)
    if video_id is not None:
        caption_result = run_caption(video_id)
        if caption_result is not None:
            caption_payload = _coerce_caption_payload(caption_result)
            if _has_word_timing(caption_payload) or allow_coarse:
                return WordsSidecar.model_validate(caption_payload)

    whisper = run_whisper or _default_whisper
    return WordsSidecar.model_validate(_coerce_whisper_payload(whisper(source)))

