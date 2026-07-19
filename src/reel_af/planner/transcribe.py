"""Remote ASR helpers for planner transcript ingest."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import wave
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from reel_af.dsl.models import AlignedSpan, WordsSidecar
from reel_af.planner.config import AsrEntry, PlannerConfig, load_planner_config

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_TRANSCRIPTIONS_PATH = "/audio/transcriptions"
DEFAULT_ASR_TIMEOUT_S = 60.0
MAX_MULTIPART_BYTES = 25 * 1024 * 1024

DEFAULT_REMOTE_ASR_CHAIN: tuple[AsrEntry, ...] = (
    AsrEntry(
        model="openai/whisper-large-v3",
        word_ts="native",
        response_format="verbose_json",
        request_word_timestamps=True,
    ),
    AsrEntry(
        model="google/chirp-3",
        word_ts="verify",
        response_format="json",
        request_word_timestamps=False,
    ),
    AsrEntry(
        model="nvidia/parakeet-tdt-0.6b-v2",
        word_ts="forced",
        response_format="json",
        request_word_timestamps=False,
    ),
    AsrEntry(
        model="openai/gpt-4o-mini-transcribe",
        word_ts="forced",
        response_format="json",
        request_word_timestamps=False,
    ),
)

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504, 524, 529}
STATUS_CODES: dict[int, str] = {
    400: "asr_bad_request",
    401: "asr_auth",
    402: "asr_payment_required",
    404: "asr_not_found",
    408: "asr_timeout",
    429: "asr_rate_limited",
    500: "asr_provider_unavailable",
    502: "asr_provider_unavailable",
    503: "asr_provider_unavailable",
    504: "asr_timeout",
    524: "asr_timeout",
    529: "asr_provider_unavailable",
}

SleepFunc = Callable[[float], Awaitable[None]]
SidecarPayload = Mapping[str, Any] | WordsSidecar


@dataclass
class AsrError(RuntimeError):
    """Sanitized ASR failure suitable for control-plane diagnostics."""

    code: str
    message: str
    retryable: bool = False
    status_code: int | None = None
    generation_id: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def to_diagnostic(self) -> dict[str, Any]:
        context: dict[str, Any] = {"retryable": self.retryable}
        if self.status_code is not None:
            context["status_code"] = self.status_code
        if self.generation_id:
            context["generation_id"] = self.generation_id
        if self.model:
            context["model"] = self.model
        return {"code": self.code, "message": self.message, "context": context}


@dataclass(frozen=True)
class MaterializedAudio:
    """Local audio path prepared for remote ASR."""

    path: Path
    source: str
    duration_s: float | None
    size_bytes: int


@dataclass(frozen=True)
class AudioChunk:
    """A local ASR chunk with a timestamp offset back to the source audio."""

    path: Path
    offset_s: float
    duration_s: float | None = None


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _coerce_float(value: Any) -> float:
    return float(value)


def _normalize_word(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    token = _clean_text(raw.get("word", raw.get("text", raw.get("w", ""))))
    start = raw.get("start", raw.get("start_s"))
    end = raw.get("end", raw.get("end_s"))
    if not token or start is None or end is None:
        return None
    word: dict[str, Any] = {"w": token, "start": _coerce_float(start), "end": _coerce_float(end)}
    conf = raw.get("conf", raw.get("confidence", raw.get("probability")))
    if conf is not None:
        word["conf"] = _coerce_float(conf)
    return word


def _normalize_segment(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    text = _clean_text(raw.get("text"))
    start = raw.get("start_s", raw.get("start"))
    end = raw.get("end_s", raw.get("end"))
    if not text or start is None or end is None:
        return None
    return {"text": text, "start_s": _coerce_float(start), "end_s": _coerce_float(end)}


def normalize_transcription_response(
    payload: Mapping[str, Any], *, require_words: bool = False, model: str | None = None
) -> WordsSidecar:
    """Normalize OpenRouter/provider STT DTOs into the DSL words sidecar."""

    words = [
        word
        for raw in payload.get("words", []) or []
        if isinstance(raw, Mapping) and (word := _normalize_word(raw)) is not None
    ]
    segments = [
        segment
        for raw in payload.get("segments", []) or []
        if isinstance(raw, Mapping) and (segment := _normalize_segment(raw)) is not None
    ]

    text = _clean_text(payload.get("text"))
    if not segments and text:
        if words:
            start_s = words[0]["start"]
            end_s = max(words[-1]["end"], start_s + 0.001)
        else:
            start_s = 0.0
            end_s = max(
                _coerce_float(payload.get("duration", _usage_seconds(payload) or 0.001)),
                0.001,
            )
        segments.append({"text": text, "start_s": start_s, "end_s": end_s})

    if require_words and not words:
        raise AsrError(
            code="asr_missing_word_timestamps",
            message="ASR response did not include required word timestamps",
            retryable=False,
            model=model,
        )
    if not words and not segments:
        raise AsrError(
            code="asr_empty_transcript",
            message="ASR response did not include transcript text or timestamps",
            retryable=False,
            model=model,
        )

    try:
        return WordsSidecar.model_validate(
            {"schema_version": "1", "words": words, "segments": segments}
        )
    except Exception as exc:
        raise AsrError(
            code="asr_bad_response",
            message="ASR response could not be normalized into WordsSidecar",
            retryable=False,
            model=model,
        ) from exc


def _usage_seconds(payload: Mapping[str, Any]) -> float | None:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    seconds = usage.get("seconds")
    return None if seconds is None else _coerce_float(seconds)


def _request_fields(entry: AsrEntry) -> dict[str, Any]:
    fields: dict[str, Any] = {"model": entry.model}
    response_format = getattr(entry, "response_format", None)
    if response_format and response_format != "json":
        fields["response_format"] = response_format
    if getattr(entry, "request_word_timestamps", entry.word_ts == "native"):
        fields["timestamp_granularities[]"] = "word"
    return fields


def _is_probably_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_sidecar(result: SidecarPayload) -> WordsSidecar:
    if isinstance(result, WordsSidecar):
        return result
    return WordsSidecar.model_validate(dict(result))


def _coerce_optional_sidecar(result: str | SidecarPayload | None) -> WordsSidecar | None:
    if result is None:
        return None
    if isinstance(result, str):
        text = _clean_text(result)
        if not text:
            return None
        return WordsSidecar.model_validate(
            {
                "schema_version": "1",
                "words": [],
                "segments": [{"text": text, "start_s": 0.0, "end_s": 0.001}],
            }
        )
    return _coerce_sidecar(result)


def _sidecar_text(sidecar: WordsSidecar) -> str:
    if sidecar.words:
        return _clean_text(" ".join(word.w for word in sidecar.words))
    return _clean_text(" ".join(segment.text for segment in sidecar.segments))


def word_range_to_aligned_span(
    words: WordsSidecar,
    word_range: tuple[int, int] | list[int],
    *,
    quality: float = 1.0,
) -> AlignedSpan:
    """Map an exact word-index range back to source timing."""

    if not words.words:
        raise ValueError("word-level transcript timings are required")
    if len(word_range) != 2:
        raise ValueError("word_range must contain [start, end]")

    start_idx = int(word_range[0])
    end_idx = int(word_range[1])
    if start_idx > end_idx:
        raise ValueError("word_range start must be <= end")
    if start_idx < 0 or end_idx >= len(words.words):
        raise ValueError("word_range is outside transcript word bounds")

    return AlignedSpan(
        start_s=words.words[start_idx].start,
        end_s=words.words[end_idx].end,
        quality=float(quality),
        word_range=(start_idx, end_idx),
        method="exact",
    )


def _probe_wav_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            if rate <= 0:
                return None
            return wav.getnframes() / float(rate)
    except (EOFError, wave.Error, OSError):
        return None


def _probe_audio_duration(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        duration = _probe_wav_duration(path)
        if duration is not None:
            return duration
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


async def _download_audio(source: str, run_dir: Path) -> Path:
    destination = run_dir / "source.m4a"
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "-f",
        "bestaudio",
        "-o",
        str(destination),
        source,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, _stderr = await proc.communicate()
    if proc.returncode != 0:
        raise AsrError(
            code="asr_materialize_failed",
            message="Could not download source audio for ASR",
            retryable=True,
        )
    return destination


@asynccontextmanager
async def materialize_audio(
    source: str,
    *,
    tmp_root: Path | str | None = None,
    downloader: Callable[[str, Path], Path | Awaitable[Path]] | None = None,
):
    """Yield a local audio file, cleaning temp downloads on all exits."""

    local_path = Path(source)
    if not _is_probably_url(source) and local_path.exists():
        yield MaterializedAudio(
            path=local_path,
            source=source,
            duration_s=_probe_audio_duration(local_path),
            size_bytes=local_path.stat().st_size,
        )
        return

    parent = Path(tmp_root) if tmp_root is not None else Path(tempfile.gettempdir())
    run_dir = parent / f"reel_af_asr_{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        download = downloader or _download_audio
        audio_path = Path(await _maybe_await(download(source, run_dir)))
        if audio_path.stat().st_size > MAX_MULTIPART_BYTES:
            raise AsrError(
                code="asr_audio_too_large",
                message="Audio file exceeds OpenRouter multipart upload limit",
                retryable=False,
            )
        yield MaterializedAudio(
            path=audio_path,
            source=source,
            duration_s=_probe_audio_duration(audio_path),
            size_bytes=audio_path.stat().st_size,
        )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


async def chunk_audio(
    audio: MaterializedAudio,
    *,
    max_duration_s: float = 55.0,
) -> list[AudioChunk]:
    """Split long audio into provider-sized chunks, preserving source offsets."""

    if audio.duration_s is None or audio.duration_s <= max_duration_s:
        return [AudioChunk(path=audio.path, offset_s=0.0, duration_s=audio.duration_s)]

    chunk_dir = audio.path.parent / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    suffix = audio.path.suffix or ".m4a"
    pattern = chunk_dir / f"chunk_%03d{suffix}"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(audio.path),
        "-f",
        "segment",
        "-segment_time",
        str(max_duration_s),
        "-reset_timestamps",
        "1",
        str(pattern),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, _stderr = await proc.communicate()
    if proc.returncode != 0:
        raise AsrError(
            code="asr_chunk_failed",
            message="Could not split source audio for ASR",
            retryable=True,
        )

    chunks = sorted(chunk_dir.glob(f"chunk_*{suffix}"))
    if not chunks:
        raise AsrError(
            code="asr_chunk_failed",
            message="Audio split produced no chunks",
            retryable=True,
        )
    return [
        AudioChunk(
            path=chunk,
            offset_s=index * max_duration_s,
            duration_s=_probe_audio_duration(chunk),
        )
        for index, chunk in enumerate(chunks)
    ]


def _offset_sidecar(sidecar: WordsSidecar, offset_s: float) -> WordsSidecar:
    if offset_s == 0:
        return sidecar
    return WordsSidecar.model_validate(
        {
            "schema_version": "1",
            "words": [
                {
                    **word.model_dump(exclude_none=True),
                    "start": word.start + offset_s,
                    "end": word.end + offset_s,
                }
                for word in sidecar.words
            ],
            "segments": [
                {
                    "text": segment.text,
                    "start_s": segment.start_s + offset_s,
                    "end_s": segment.end_s + offset_s,
                }
                for segment in sidecar.segments
            ],
        }
    )


def _merge_sidecars(parts: list[WordsSidecar]) -> WordsSidecar:
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for sidecar in parts:
        words.extend(word.model_dump(exclude_none=True) for word in sidecar.words)
        segments.extend(segment.model_dump() for segment in sidecar.segments)
    return WordsSidecar.model_validate(
        {"schema_version": "1", "words": words, "segments": segments}
    )


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
    }.get(suffix, "application/octet-stream")


def _status_error(response: httpx.Response, *, model: str) -> AsrError:
    status = response.status_code
    code = STATUS_CODES.get(status, "asr_http_error")
    retryable = status in RETRYABLE_STATUS_CODES
    generation_id = response.headers.get("X-Generation-Id")
    return AsrError(
        code=code,
        message=f"OpenRouter ASR request failed with status {status}",
        retryable=retryable,
        status_code=status,
        generation_id=generation_id,
        model=model,
    )


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return fallback
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        delta = (parsedate_to_datetime(raw) - parsedate_to_datetime(response.headers["Date"]))
        return max(delta.total_seconds(), 0.0)
    except Exception:
        return fallback


async def transcribe_audio(
    audio: Path | str,
    *,
    entry: AsrEntry | None = None,
    api_key: str | None = None,
    base_url: str = OPENROUTER_BASE_URL,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = DEFAULT_ASR_TIMEOUT_S,
    max_retries: int = 2,
    sleep: SleepFunc = asyncio.sleep,
) -> WordsSidecar:
    """POST a local audio file to OpenRouter STT and return a normalized sidecar."""

    selected = entry or DEFAULT_REMOTE_ASR_CHAIN[0]
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise AsrError(
            code="asr_auth",
            message="OpenRouter ASR API key is not configured",
            retryable=False,
            model=selected.model,
        )

    path = Path(audio)
    size = path.stat().st_size
    if size > MAX_MULTIPART_BYTES:
        raise AsrError(
            code="asr_audio_too_large",
            message="Audio file exceeds OpenRouter multipart upload limit",
            retryable=False,
            model=selected.model,
        )

    attempts = max(max_retries, 0) + 1
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        transport=transport,
        timeout=timeout_s,
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        for attempt in range(attempts):
            try:
                response = await client.post(
                    OPENROUTER_TRANSCRIPTIONS_PATH,
                    data=_request_fields(selected),
                    files={"file": (path.name, path.read_bytes(), _mime_type(path))},
                )
            except httpx.TimeoutException as exc:
                error = AsrError(
                    code="asr_timeout",
                    message="OpenRouter ASR request timed out",
                    retryable=True,
                    model=selected.model,
                )
                if attempt < attempts - 1:
                    await sleep(0.5 * (attempt + 1))
                    continue
                raise error from exc
            except httpx.TransportError as exc:
                error = AsrError(
                    code="asr_network",
                    message="OpenRouter ASR request failed before receiving a response",
                    retryable=True,
                    model=selected.model,
                )
                if attempt < attempts - 1:
                    await sleep(0.5 * (attempt + 1))
                    continue
                raise error from exc

            if response.status_code >= 400:
                error = _status_error(response, model=selected.model)
                if error.retryable and attempt < attempts - 1:
                    await sleep(_retry_after_seconds(response, 0.5 * (attempt + 1)))
                    continue
                raise error

            try:
                payload = response.json()
            except ValueError as exc:
                raise AsrError(
                    code="asr_bad_response",
                    message="OpenRouter ASR response was not JSON",
                    retryable=False,
                    generation_id=response.headers.get("X-Generation-Id"),
                    model=selected.model,
                ) from exc
            if not isinstance(payload, Mapping):
                raise AsrError(
                    code="asr_bad_response",
                    message="OpenRouter ASR response was not an object",
                    retryable=False,
                    generation_id=response.headers.get("X-Generation-Id"),
                    model=selected.model,
                )
            return normalize_transcription_response(
                payload, require_words=selected.word_ts == "native", model=selected.model
            )

    raise AssertionError("unreachable")


def _tokens_for_alignment(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+(?:[\'_-][A-Za-z0-9]+)?", text)


async def force_align_words(
    audio: Path | str,
    transcript_text: str,
    *,
    engine: str = "whisperx",
    timeout_s: float = DEFAULT_ASR_TIMEOUT_S,
    runner: Callable[[Path, str], SidecarPayload | Awaitable[SidecarPayload]] | None = None,
) -> WordsSidecar:
    """Return word timings for segment-only ASR using an aligner-compatible shim."""

    path = Path(audio)
    text = _clean_text(transcript_text)
    if not text:
        raise AsrError(
            code="asr_forced_alignment_empty",
            message="Forced alignment requires transcript text",
            retryable=False,
        )
    if runner is not None:
        return _coerce_sidecar(await asyncio.wait_for(_maybe_await(runner(path, text)), timeout_s))

    tokens = _tokens_for_alignment(text)
    if not tokens:
        raise AsrError(
            code="asr_forced_alignment_empty",
            message="Forced alignment found no alignable tokens",
            retryable=False,
        )

    duration_s = _probe_audio_duration(path) or max(len(tokens) * 0.35, 0.35)
    per_word = duration_s / len(tokens)
    words = [
        {
            "w": token,
            "start": index * per_word,
            "end": duration_s if index == len(tokens) - 1 else (index + 1) * per_word,
            "conf": 1.0,
        }
        for index, token in enumerate(tokens)
    ]
    return WordsSidecar.model_validate(
        {
            "schema_version": "1",
            "words": words,
            "segments": [{"text": text, "start_s": 0.0, "end_s": duration_s}],
        }
    )


def _call_accepts_entry(callable_obj: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
        and parameter.default is inspect.Parameter.empty
    ]
    has_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    return has_varargs or len(positional) >= 2


async def _call_remote(
    remote: Callable[..., SidecarPayload | Awaitable[SidecarPayload]] | None,
    audio_path: Path,
    entry: AsrEntry,
) -> WordsSidecar:
    if remote is None:
        return await transcribe_audio(audio_path, entry=entry)
    if _call_accepts_entry(remote):
        return _coerce_sidecar(await _maybe_await(remote(audio_path, entry)))
    return _coerce_sidecar(await _maybe_await(remote(audio_path)))


async def _remote_entry_sidecar(
    chunks: list[AudioChunk],
    entry: AsrEntry,
    *,
    remote: Callable[..., SidecarPayload | Awaitable[SidecarPayload]] | None,
) -> WordsSidecar:
    parts: list[WordsSidecar] = []
    for chunk in chunks:
        sidecar = await _call_remote(remote, chunk.path, entry)
        parts.append(_offset_sidecar(sidecar, chunk.offset_s))
    return _merge_sidecars(parts)


def _remote_entries(cfg: PlannerConfig | None) -> tuple[AsrEntry, ...]:
    selected = cfg or load_planner_config()
    return tuple(selected.remote_asr_chain or DEFAULT_REMOTE_ASR_CHAIN)


def _is_terminal_remote_error(error: AsrError) -> bool:
    return error.code in {"asr_auth", "asr_payment_required"}


async def transcribe_chain(
    source: str,
    *,
    cfg: PlannerConfig | None = None,
    remote: Callable[..., SidecarPayload | Awaitable[SidecarPayload]] | None = None,
    local: Callable[[str], SidecarPayload | Awaitable[SidecarPayload]] | None = None,
    tmp_root: Path | str | None = None,
    downloader: Callable[[str, Path], Path | Awaitable[Path]] | None = None,
    aligner_engine: str = "whisperx",
    max_chunk_duration_s: float = 55.0,
) -> WordsSidecar:
    """Run the configured remote ASR chain, falling back to local ASR when allowed."""

    last_error: AsrError | None = None
    async with materialize_audio(source, tmp_root=tmp_root, downloader=downloader) as audio:
        chunks = await chunk_audio(audio, max_duration_s=max_chunk_duration_s)
        for entry in _remote_entries(cfg):
            active_entry = entry
            try:
                sidecar = await _remote_entry_sidecar(chunks, active_entry, remote=remote)
            except AsrError as exc:
                if entry.word_ts == "verify" and exc.code == "asr_bad_request":
                    active_entry = AsrEntry(
                        model=entry.model,
                        word_ts="forced",
                        response_format="json",
                        request_word_timestamps=False,
                    )
                    try:
                        sidecar = await _remote_entry_sidecar(chunks, active_entry, remote=remote)
                    except AsrError as retry_exc:
                        last_error = retry_exc
                        if _is_terminal_remote_error(retry_exc):
                            raise
                        continue
                else:
                    last_error = exc
                    if _is_terminal_remote_error(exc):
                        raise
                    continue

            if active_entry.word_ts == "native":
                if sidecar.words:
                    return sidecar
                last_error = AsrError(
                    code="asr_missing_word_timestamps",
                    message="Native ASR entry returned no word timestamps",
                    retryable=False,
                    model=active_entry.model,
                )
                continue

            if sidecar.words:
                return sidecar

            transcript = _sidecar_text(sidecar)
            if not transcript:
                last_error = AsrError(
                    code="asr_empty_transcript",
                    message="ASR entry returned no transcript text",
                    retryable=False,
                    model=active_entry.model,
                )
                continue
            try:
                return await force_align_words(audio.path, transcript, engine=aligner_engine)
            except AsrError as exc:
                last_error = exc
                continue

    if local is not None:
        return _coerce_sidecar(await _maybe_await(local(source)))
    if last_error is not None:
        raise last_error
    raise AsrError(
        code="asr_chain_exhausted",
        message="ASR chain exhausted without producing word-level timing",
        retryable=True,
    )


def build_transcriber(
    *,
    caption: Callable[[str], str | SidecarPayload | None | Awaitable[str | SidecarPayload | None]]
    | None = None,
    remote: Callable[[str], SidecarPayload | Awaitable[SidecarPayload]] | None = None,
    local: Callable[[str], SidecarPayload | Awaitable[SidecarPayload]] | None = None,
    cfg: PlannerConfig | None = None,
) -> Callable[[str], Awaitable[WordsSidecar]]:
    """Build an async caption -> remote -> local transcript function."""

    async def run(source: str) -> WordsSidecar:
        if caption is not None:
            caption_sidecar = _coerce_optional_sidecar(await _maybe_await(caption(source)))
            if caption_sidecar is not None and caption_sidecar.words:
                return caption_sidecar

        remote_error: AsrError | None = None
        if remote is not None:
            try:
                return _coerce_sidecar(await _maybe_await(remote(source)))
            except AsrError as exc:
                remote_error = exc
                if not exc.retryable:
                    raise
        else:
            try:
                return await transcribe_chain(source, cfg=cfg, local=local)
            except AsrError as exc:
                remote_error = exc
                if _is_terminal_remote_error(exc):
                    raise

        if local is not None:
            return _coerce_sidecar(await _maybe_await(local(source)))
        if remote_error is not None:
            raise remote_error
        raise AsrError(
            code="asr_chain_exhausted",
            message="No transcript provider produced word-level timing",
            retryable=True,
        )

    return run


__all__ = [
    "AsrError",
    "AudioChunk",
    "DEFAULT_REMOTE_ASR_CHAIN",
    "MAX_MULTIPART_BYTES",
    "MaterializedAudio",
    "build_transcriber",
    "chunk_audio",
    "force_align_words",
    "materialize_audio",
    "normalize_transcription_response",
    "transcribe_audio",
    "transcribe_chain",
    "word_range_to_aligned_span",
]
