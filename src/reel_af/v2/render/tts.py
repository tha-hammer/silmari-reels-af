"""V2 TTS — single SDK call producing full WAV + per-word timings (replaces v1 silence-detection scene splitter in tts_continuous.py)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from agentfield.media_providers import OpenRouterProvider

import reel_af.sdk_patches  # noqa: F401  # adds OpenRouterProvider.generate_speech() at import time
from reel_af.agents.tts_continuous import (
    DEFAULT_TTS_MODEL,
    _wrap_pcm16_bytes_as_wav,
    voice_for_tone,
)
from reel_af.v2.models import WordTiming

# Silence unused-import warnings for the re-exported public surface.
__all__ = ["generate_tts", "voice_for_tone"]


# ───── Tag stripping ────────────────────────────────────────────────


# Matches inline Gemini TTS stage-direction tags like [excited], [pause],
# [whispers], [sigh]. These are interpreted by the model as delivery cues
# and NEVER spoken aloud — so they must be removed before we tokenize the
# narration into words for timing assignment.
_TAG_RE = re.compile(r"\[[^\]]*\]")


def _strip_tts_tags(text: str) -> str:
    """Remove `[...]` bracketed Gemini TTS tags and collapse whitespace.

    The spoken audio never contains the literal tag text, so the word list
    we tile against the audio duration must exclude them.
    """
    stripped = _TAG_RE.sub(" ", text)
    return " ".join(stripped.split())


# ───── SDK call (mirrors v1 tts_continuous._sdk_generate_wav) ───────


async def _sdk_generate_wav(
    tagged_script: str,
    voice: str,
    model: str,
    timeout: float = 300.0,
) -> bytes:
    """Generate a complete WAV via AgentField's SDK + Gemini TTS.

    Returns ready-to-write WAV bytes (header + PCM). Gemini outputs raw
    PCM at 24 kHz mono 16-bit; we add the WAV header locally.

    The narrator directive can be passed via the `instructions=` kwarg on
    the patched generate_speech() (separate field from `text`, so it
    never leaks into the spoken audio). For now this v2 entrypoint keeps
    the directive inline — same call shape as v1, replacing it is a
    follow-up once compose.py starts emitting an explicit narrator
    persona string separately from the script text.
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
            f"v2.render.tts: generate_speech returned no audio for model {model}"
        )
    return _wrap_pcm16_bytes_as_wav(pcm)


# ───── ffprobe duration helper ───────────────────────────────────────


def _probe_duration(audio_path: Path) -> float:
    """Return the audio file's duration in seconds via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ───── Public entrypoints ───────────────────────────────────────────


async def synthesize_audio(
    narration: str,
    voice: str,
    out_dir: Path,
    model: Optional[str] = None,
) -> tuple[Path, float]:
    """Just synthesize the narration. Returns ``(audio_path, duration_s)``.

    Word timings are produced separately by ``alignment.align_audio`` so the
    pipeline has a clean two-step split (synth then align) and each step is
    its own reasoner in the DAG.

    ``narration`` must already include any inline Gemini TTS tags
    (``[excited]``, ``[pause]``, …) — those are stage directions Gemini
    interprets but never speaks. They appear in the script verbatim and
    DO NOT appear in the synthesized audio.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tts_model = model or os.environ.get("REEL_AF_TTS_MODEL", DEFAULT_TTS_MODEL)

    wav_bytes = await _sdk_generate_wav(
        tagged_script=narration,
        voice=voice,
        model=tts_model,
    )
    full_audio_path = out_dir / "full.wav"
    full_audio_path.write_bytes(wav_bytes)
    duration_s = _probe_duration(full_audio_path)
    return full_audio_path, duration_s


async def generate_tts(
    narration: str,
    voice: str,
    out_dir: Path,
    model: Optional[str] = None,
) -> tuple[Path, list[WordTiming]]:
    """Synthesize the full narration once and return audio + word timings.

    `narration` must already include any inline Gemini TTS tags
    (`[excited]`, `[pause]`, …) — those are stage directions the model
    interprets but never speaks. They appear in the script verbatim from
    compose.py but DO NOT appear in the returned WordTiming list.

    Word timing strategy (v1): tags are stripped, the spoken text is
    tokenized on whitespace, and total audio duration is distributed
    across words with weight proportional to len(word) — a cheap
    syllable-count proxy. Adequate for karaoke caption alignment because
    (a) the spoken text is known exactly, (b) modern TTS pacing is
    consistent, and (c) ±50ms misalignment is invisible to viewers.

    Returns:
        (full_audio_path, word_timings) — the full .wav and a flat list
        of WordTiming covering every spoken word in order, tiling
        [0, total_duration_s] with no gaps.
    """
    # TODO(v2.1): swap Path-A proportional timing for forced alignment
    # (whisper.cpp or aeneas) for sub-frame karaoke accuracy. The Path-A
    # output is good enough for v1 — viewers cannot perceive the residual
    # misalignment in 9:16 vertical reels.

    out_dir.mkdir(parents=True, exist_ok=True)
    tts_model = model or os.environ.get("REEL_AF_TTS_MODEL", DEFAULT_TTS_MODEL)

    # 1. Synthesize via SDK — Gemini reads the tags as stage directions.
    wav_bytes = await _sdk_generate_wav(
        tagged_script=narration,
        voice=voice,
        model=tts_model,
    )
    full_audio_path = out_dir / "full.wav"
    full_audio_path.write_bytes(wav_bytes)

    # 2. Probe duration of the synthesized audio.
    total_dur = _probe_duration(full_audio_path)

    # 3. Strip tags from the narration to get the spoken word list.
    spoken = _strip_tts_tags(narration)
    words = spoken.split()
    if not words:
        # Defensive: empty narration → no timings, just return the audio.
        return full_audio_path, []

    # 4. Distribute time proportionally to word length (rough syllable
    #    proxy). Tile [0, total_dur] with no gaps so card_packer downstream
    #    can rely on adjacent-word continuity.
    weights = [max(len(w), 1) for w in words]
    total_w = sum(weights)
    timings: list[WordTiming] = []
    cursor = 0.0
    for i, (w, weight) in enumerate(zip(words, weights)):
        dur = total_dur * weight / total_w
        # Pin the last word's end_s exactly to total_dur to avoid drift.
        end_s = total_dur if i == len(words) - 1 else cursor + dur
        timings.append(WordTiming(word=w, start_s=cursor, end_s=end_s))
        cursor = end_s

    return full_audio_path, timings
