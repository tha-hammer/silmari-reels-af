"""Continuous TTS — one TTS call for the full script, split on silences.

Uses AgentField's OpenRouterProvider.generate_audio() for the actual call
(NEVER bypasses the SDK — all provider calls go through AgentField).

This solves DISCRETE WORDS — per-segment calls have no prosody continuity,
so every sentence dies flat at the period. Generating the whole script in
one call carries intonation across sentence boundaries.

Known SDK gap (worth filing upstream):
  OpenRouterProvider.generate_audio() hardcodes
  messages=[{"role":"user","content":text}] with no way to inject a
  system message. Without a system role, chat-completions audio models
  like gpt-audio-mini may RESPOND to the script ("Sure, I can help…")
  instead of READING it verbatim as narrator. We work around this by
  prepending compact narrator directives to the user text so the model
  treats the whole payload as a "read this verbatim" task.

Approach:
  1. SDK generate_audio() with format='wav' (returns complete WAV bytes
     base64-encoded — no PCM-wrapping needed).
  2. Write the wav, run ffmpeg `silencedetect` to find sentence-boundary
     silences.
  3. Split at silence midpoints; assign per-scene clips.
  4. Fall back to proportional-by-word-count splits if silence count is off.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import Optional

from agentfield.media_providers import OpenRouterProvider

from reel_af.agents.scene_breaker import Scene
from reel_af.models import BeatArtifact

# gpt-audio family streams PCM16 @ 24kHz mono.
TTS_SAMPLE_RATE = 24000


def _wrap_pcm16_bytes_as_wav(
    pcm: bytes, sample_rate: int = TTS_SAMPLE_RATE, channels: int = 1
) -> bytes:
    """Wrap raw little-endian 16-bit PCM samples in a WAV container.

    Required because the SDK's generate_audio() hardcodes stream=true,
    and OpenRouter only accepts format='pcm16' when streaming — so we get
    raw PCM back and must add the WAV header ourselves (stdlib only).
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()

# Default model: gpt-audio-mini via chat-completions audio modality.
DEFAULT_TTS_MODEL = "openrouter/openai/gpt-audio-mini"

# Voice map by tone — SDK clamps unknown voices to "alloy", so we ensure
# every value here is in the supported set (alloy/echo/fable/onyx/nova/shimmer).
_VOICE_BY_TONE: dict[str, str] = {
    "urgent":  "onyx",      # deep, serious — gravitas
    "wonder":  "nova",      # warm, curious
    "deadpan": "echo",      # neutral, dry
    "earnest": "alloy",     # friendly, warm
    "playful": "shimmer",   # bright, conversational
}


def voice_for_tone(tone: str) -> str:
    """Pick a gpt-audio voice that matches the script's tone."""
    return _VOICE_BY_TONE.get(tone, "nova")


# ───── Narrator directive (compensates for SDK's no-system-message gap) ─


def _wrap_as_narration(script: str, tone: str) -> str:
    """Wrap the script with an inline narrator directive.

    The SDK's generate_audio() only takes a single user message, so we
    can't pass a system role to coerce the model into 'read verbatim'
    mode. We embed the directive inline. The DIVIDER block makes it
    visually obvious to the model where the literal script begins.
    """
    return (
        f"You are a professional vertical-video narrator. Read EVERY word "
        f"between the dividers below EXACTLY as written, with a {tone} "
        f"tone. Do not greet, comment, summarise, or add ANY words of "
        f"your own. Vary pace deliberately. Em-dashes are a deliberate "
        f"pause. Single-word periods are punchy beats. ALL CAPS WORDS "
        f"are emphasised. The last sentence lands slow and weighty.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{script}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ───── SDK-based TTS via OpenRouterProvider.generate_audio() ─────────


async def _sdk_generate_wav(
    full_script: str,
    voice: str,
    tone: str,
    model: str,
    timeout: float = 300.0,
) -> bytes:
    """Generate a complete WAV via AgentField's SDK.

    Returns ready-to-write WAV bytes (header + PCM16). All transport,
    streaming, and decoding happens inside OpenRouterProvider — we never
    touch HTTP. We request format='pcm16' (the only one the SDK's hardcoded
    stream=true allows) and slap a WAV header on the raw samples.
    """
    provider = OpenRouterProvider()
    response = await provider.generate_audio(
        text=_wrap_as_narration(full_script, tone),
        model=model,
        voice=voice,
        format="pcm16",
        timeout=timeout,
    )
    if response.audio is None or not response.audio.data:
        raise RuntimeError(
            f"tts_continuous: SDK generate_audio returned no audio for model {model}"
        )
    pcm = base64.b64decode(response.audio.data)
    return _wrap_pcm16_bytes_as_wav(pcm)


# ───── Silence-detection helpers ─────────────────────────────────────


def _detect_silences(audio_path: Path, threshold_db: int = -32, min_dur: float = 0.25) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect; return list of (silence_start, silence_end) tuples."""
    cmd = [
        "ffmpeg", "-i", str(audio_path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # silencedetect writes results to stderr in the form:
    #   [silencedetect @ 0x…] silence_start: 3.14
    #   [silencedetect @ 0x…] silence_end: 3.52
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return list(zip(starts, ends))


def _probe_duration(audio_path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _proportional_splits(scenes: list[Scene], total_duration: float) -> list[float]:
    """Fallback: split times by per-scene word-count proportion."""
    total_words = sum(max(len(s.sentence.split()), 1) for s in scenes)
    cuts: list[float] = []
    acc = 0
    for s in scenes[:-1]:
        acc += max(len(s.sentence.split()), 1)
        cuts.append(total_duration * acc / total_words)
    return cuts


def _pick_split_points(
    scenes: list[Scene],
    silences: list[tuple[float, float]],
    total_duration: float,
) -> list[float]:
    """Pick (n_scenes - 1) split points — silence midpoints when possible."""
    n_needed = len(scenes) - 1
    if n_needed == 0:
        return []
    # Use silence MIDPOINTS as split points so we cut between words, not on top of one.
    silence_mids = [(start + end) / 2 for start, end in silences]
    if len(silence_mids) >= n_needed:
        # If the model emitted MORE silences than sentences (e.g. internal commas),
        # pick the n_needed silences whose times best match the word-proportional split.
        target_cuts = _proportional_splits(scenes, total_duration)
        chosen: list[float] = []
        used = set()
        for t in target_cuts:
            best_idx = min(
                (i for i in range(len(silence_mids)) if i not in used),
                key=lambda i: abs(silence_mids[i] - t),
                default=None,
            )
            if best_idx is None:
                chosen.append(t)
            else:
                chosen.append(silence_mids[best_idx])
                used.add(best_idx)
        return sorted(chosen)
    # Not enough silences — fall back to proportional.
    return _proportional_splits(scenes, total_duration)


async def _split_audio(
    full_audio: Path,
    cuts: list[float],
    total_duration: float,
    out_dir: Path,
    n_scenes: int,
) -> list[Path]:
    """Cut the full audio into per-scene WAVs at the given cut points."""
    bounds = [0.0] + cuts + [total_duration]
    out_paths: list[Path] = []
    tasks = []
    for i in range(n_scenes):
        start, end = bounds[i], bounds[i + 1]
        out_path = out_dir / f"seg-{i:02d}.wav"
        out_paths.append(out_path)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(full_audio),
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-c", "copy",
            str(out_path),
        ]
        tasks.append(asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ))
    procs = await asyncio.gather(*tasks)
    for i, proc in enumerate(procs):
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"tts_continuous: failed to extract segment {i}: "
                f"{err.decode(errors='replace')[-300:]}"
            )
    return out_paths


# ───── Public entrypoint ────────────────────────────────────────────


async def generate_continuous_audio(
    full_script: str,
    scenes: list[Scene],
    voice: str,
    out_dir: Path,
    tone: str = "wonder",
    model: Optional[str] = None,
) -> tuple[list[BeatArtifact], Path]:
    """Generate the full script as ONE TTS call, split into per-scene WAVs.

    Returns (per-scene artifacts, full continuous wav path) so callers can
    debug by listening to the full continuous track.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tts_model = model or os.environ.get("REEL_AF_TTS_MODEL", DEFAULT_TTS_MODEL)

    # All transport happens inside the AgentField SDK. Narrator direction
    # is embedded in the user text (see SDK gap noted in module docstring).
    wav_bytes = await _sdk_generate_wav(
        full_script=full_script,
        voice=voice,
        tone=tone,
        model=tts_model,
    )
    full_audio_path = out_dir / "full.wav"
    full_audio_path.write_bytes(wav_bytes)

    # Find silences and split.
    total_dur = _probe_duration(full_audio_path)
    silences = _detect_silences(full_audio_path)
    cuts = _pick_split_points(scenes, silences, total_dur)
    seg_paths = await _split_audio(
        full_audio_path, cuts, total_dur, out_dir, n_scenes=len(scenes)
    )

    artifacts = [
        BeatArtifact(idx=s.idx, audio_path=p)
        for s, p in zip(scenes, seg_paths)
    ]
    return artifacts, full_audio_path
