"""Continuous TTS — one TTS call for the full script, split on silences.

Calls AgentField's OpenRouterProvider through the `/audio/speech` endpoint
(via the runtime SDK patch in sdk_patches.py) to reach dedicated TTS
models like Google Gemini 3.1 Flash TTS. Those models interpret inline
`[tag]` directives as stage directions and never speak them aloud — so
tone instructions stay out of the spoken text, unlike the chat-audio
gpt-audio models we used before.

This solves DISCRETE WORDS — per-segment calls have no prosody continuity,
so every sentence dies flat at the period. Generating the whole script in
one call carries intonation across sentence boundaries.

Approach:
  1. The script_writer + tag_injector produce a script with inline Gemini
     audio tags (e.g. "[curious] GPT-4o on MATH? [excited] Beaten by RL.")
  2. SDK generate_speech() returns raw PCM (Gemini's only format).
  3. Wrap raw PCM as WAV (stdlib `wave`).
  4. Use ffmpeg `silencedetect` to find sentence-boundary silences.
  5. Split at silence midpoints; assign per-scene clips.
  6. Fall back to proportional-by-word-count splits if silence count is off.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import Optional

from agentfield.media_providers import OpenRouterProvider

# Ensure the runtime patch that adds OpenRouterProvider.generate_speech()
# is applied before we try to call it. See AGENTFIELD_SDK_ISSUES.md.
import reel_af.sdk_patches  # noqa: F401

from reel_af.agents.scene_breaker import Scene
from reel_af.models import BeatArtifact

# Gemini 3.1 Flash TTS streams PCM @ 24kHz mono 16-bit.
TTS_SAMPLE_RATE = 24000


def _wrap_pcm16_bytes_as_wav(
    pcm: bytes, sample_rate: int = TTS_SAMPLE_RATE, channels: int = 1
) -> bytes:
    """Wrap raw little-endian 16-bit PCM samples in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()

# Default TTS model — Gemini 3.1 Flash TTS via OpenRouter /audio/speech.
# Picked because it supports 200+ inline audio tags ([excited], [pause],
# [whispers], …) that steer delivery without polluting the spoken text.
DEFAULT_TTS_MODEL = "google/gemini-3.1-flash-tts-preview"

# Gemini voice map by script tone. Gemini's voice set includes Achernar,
# Achird, Algenib, Algieba, Alnilam, Aoede, Autonoe, Callirrhoe, Charon,
# Despina, Enceladus, Erinome, Fenrir, Gacrux, Iapetus, Kore, Laomedeia,
# Leda, Orus, Pulcherrima, Puck, Rasalgethi, Sadachbia, Sadaltager,
# Schedar, Sulafat, Umbriel, Vindemiatrix, Zephyr, Zubenelgenubi.
# Picks below are the most reliable for English narration in each tone.
_VOICE_BY_TONE: dict[str, str] = {
    "urgent":  "Charon",      # deep, serious — gravitas
    "wonder":  "Kore",        # warm, curious — default scientific narrator
    "deadpan": "Schedar",     # neutral, measured
    "earnest": "Aoede",       # friendly, warm
    "playful": "Puck",        # bright, conversational
}


def voice_for_tone(tone: str) -> str:
    """Pick a Gemini voice that matches the script's tone."""
    return _VOICE_BY_TONE.get(tone, "Kore")


# ───── SDK-based TTS via OpenRouterProvider.generate_speech() ────────


async def _sdk_generate_wav(
    tagged_script: str,
    voice: str,
    model: str,
    timeout: float = 300.0,
) -> bytes:
    """Generate a complete WAV via AgentField's SDK + Gemini TTS.

    Returns ready-to-write WAV bytes (header + PCM). Gemini outputs raw
    PCM at 24 kHz mono 16-bit; we add the WAV header (stdlib `wave`).

    The script passed in must already have Gemini audio tags inserted
    by tag_injector — Gemini treats the bracketed cues as stage
    directions and never speaks them aloud, which is what gives us
    expressive delivery without instruction-leak.
    """
    provider = OpenRouterProvider()
    pcm = await provider.generate_speech(  # type: ignore[attr-defined]
        text=tagged_script,
        model=model,
        voice=voice,
        response_format="pcm",
        timeout=timeout,
    )
    if not pcm:
        raise RuntimeError(
            f"tts_continuous: generate_speech returned no audio for model {model}"
        )
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

    # full_script here MUST already contain Gemini inline audio tags
    # (the entry reasoner runs tag_injector right after compose_script
    # and feeds the tagged script in). Gemini reads the words and
    # interprets the tags as stage directions.
    wav_bytes = await _sdk_generate_wav(
        tagged_script=full_script,
        voice=voice,
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
