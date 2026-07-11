"""Single-pass stitch — concat filter + libass + audio mux in one ffmpeg call.

Each beat is rendered as a SILENT 1080×1920 clip in parallel, then ONE
final ffmpeg call concats them with a sample-accurate ``concat`` filter,
burns the global ASS file (word-burst + optional accents) via libass,
and muxes the full TTS WAV — all in a single encode.

Why single-pass:
  • The concat FILTER is sample-accurate at the re-encoded boundary, so
    there's no sub-frame drift between beats.
  • Audio is muxed in the same pass so the AAC encoder primes once for
    the whole reel — no per-shot priming drift.
  • Subtitles are burned once with libass at canvas resolution.

Accent decision: accents are wired directly into the global ASS via
``build_reel_ass_with_accents``. Accent timing uses cumulative
``beat.target_duration_s`` as the source of truth — small estimate-vs-
reality drift is acceptable since viewers can't perceive ±200ms accent
timing and it avoids a second ffmpeg pass.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from reel_af.models import AccentOverlay, Beat, BeatArtifact, Card
from reel_af.planning.safe_zone import CANVAS_H, CANVAS_W
from reel_af.render.subtitles import (
    write_reel_ass,
    write_reel_ass_with_accents,
)

# Each beat is a full 1080x1920 libx264 encode. Rendering ALL beats at once spikes
# memory and gets OOM-killed (SIGKILL/-9) on a small node, so concurrent beat
# renders are capped by a semaphore. Override via REEL_STITCH_CONCURRENCY.
_DEFAULT_BEAT_RENDER_CONCURRENCY = 2


def _max_beat_concurrency() -> int:
    try:
        return max(1, int(os.getenv("REEL_STITCH_CONCURRENCY", str(_DEFAULT_BEAT_RENDER_CONCURRENCY))))
    except ValueError:
        return _DEFAULT_BEAT_RENDER_CONCURRENCY


TARGET_W = CANVAS_W
TARGET_H = CANVAS_H
FPS = 30  # match Veo's native 24-30 fps to skip interpolation.


# ───── Font discovery ────────────────────────────────────────────────


_FONT_CANDIDATES: tuple[str, ...] = (
    # Prefer Montserrat — that's what safe_zone metrics assume.
    "/Library/Fonts/Montserrat-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    # Fallbacks — close-enough bold sans-serifs.
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _find_font() -> str:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError("stitch: no usable font found on this system.")


def _ass_font_name(font_path: str) -> str:
    """Best-effort family name from the font filename."""
    stem = Path(font_path).stem.lower()
    if "montserrat" in stem: return "Montserrat"
    if "arial" in stem:      return "Arial"
    if "helvetica" in stem:  return "Helvetica"
    if "dejavu" in stem:     return "DejaVu Sans"
    if "liberation" in stem: return "Liberation Sans"
    return "sans-serif"


# ───── ffmpeg path escaping ──────────────────────────────────────────


def _ffmpeg_path_arg(path: Path | str) -> str:
    """Escape a filesystem path for use as a filter argument value.

    Filter-graph parsing treats ``:`` as kv separator and ``\\`` as escape,
    so paths with colons need both escaped.
    """
    return str(path).replace("\\", "\\\\").replace(":", r"\:")


def _probe_duration(path: Path) -> float:
    """Duration of a media file via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ───── Per-beat SILENT render ────────────────────────────────────────


async def _render_beat(
    beat: Beat,
    artifact: BeatArtifact,
    out_path: Path,
) -> None:
    """Render one beat as a SILENT 1080×1920 clip.

    Scale + crop the Veo source to the canvas, trim to beat.veo_duration,
    emit at final codec settings so all clips concat cleanly. No
    subtitles, no accents, no audio at this stage — they all happen in
    the single-pass final encode.
    """
    if artifact.video_path is None:
        raise RuntimeError(
            f"stitch: beat {beat.idx} has no video_path on artifact."
        )

    dur = float(beat.veo_duration)
    # setsar=1 normalizes pixel aspect ratio. Veo occasionally emits
    # clips with SAR 0:1 (undefined) or slightly non-square SAR that
    # the concat filter rejects later. Forcing 1:1 here makes every
    # silent clip compatible with concat regardless of Veo's metadata.
    vfilter = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},"
        f"setsar=1,"
        f"fps={FPS},"
        f"format=yuv420p,"
        f"trim=end={dur:.3f},setpts=PTS-STARTPTS"
    )

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(artifact.video_path),
        "-filter_complex", f"[0:v]{vfilter}[v]",
        "-map", "[v]",
        "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "18",
        "-r", str(FPS),
        "-movflags", "+faststart",
        "-t", f"{dur:.3f}",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"stitch: beat {beat.idx} silent render failed "
            f"(exit {proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"  stderr: {stderr.decode(errors='replace')[-800:]}"
        )


async def _render_beats(
    beats: list[Beat],
    artifacts_by_idx: dict[int, BeatArtifact],
    out_dir: Path,
    *,
    concurrency: int | None = None,
) -> list[Path]:
    """Render every beat's silent 1080×1920 clip and return their paths in beat order.

    Concurrency is capped by a semaphore (``concurrency`` or ``_max_beat_concurrency()``)
    so a many-beat reel cannot spawn N full-resolution ffmpeg encodes at once and get
    OOM-killed (SIGKILL/-9) on a memory-limited node.
    """
    sem = asyncio.Semaphore(concurrency if concurrency is not None else _max_beat_concurrency())
    clip_paths: list[Path] = []
    jobs: list[asyncio.Task[None]] = []

    async def _bounded(beat: Beat, artifact: BeatArtifact, out_path: Path) -> None:
        async with sem:
            await _render_beat(beat=beat, artifact=artifact, out_path=out_path)

    for beat in beats:
        artifact = artifacts_by_idx.get(beat.idx)
        if artifact is None:
            raise RuntimeError(f"stitch: no artifact found for beat idx={beat.idx}")
        out_clip = out_dir / f"beat-{beat.idx:02d}-silent.mp4"
        clip_paths.append(out_clip)
        jobs.append(asyncio.create_task(_bounded(beat, artifact, out_clip)))
    await asyncio.gather(*jobs)
    return clip_paths


# ───── Single-pass final assembly ────────────────────────────────────


async def _single_pass_assemble(
    clip_paths: list[Path],
    audio_path: Path,
    ass_path: Path,
    font_path: str,
    out_path: Path,
) -> None:
    """One ffmpeg call: concat filter + libass burn + AAC mux."""
    n = len(clip_paths)
    if n == 0:
        raise RuntimeError("stitch: no clips to assemble.")

    ass_arg = _ffmpeg_path_arg(ass_path)
    font_dir_arg = _ffmpeg_path_arg(Path(font_path).parent)

    concat_inputs = "".join(f"[{i}:v]" for i in range(n))
    filter_complex = (
        f"{concat_inputs}concat=n={n}:v=1:a=0[concat];"
        f"[concat]subtitles={ass_arg}:fontsdir={font_dir_arg}[v]"
    )

    cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error"]
    for clip in clip_paths:
        cmd += ["-i", str(clip)]
    audio_idx = n
    cmd += ["-i", str(audio_path)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", f"{audio_idx}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "18",
        "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"stitch: single-pass assemble failed (exit {proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"  stderr: {stderr.decode(errors='replace')[-1200:]}"
        )


# ───── Public entry point ────────────────────────────────────────────


async def stitch_reel(
    beats: list[Beat],
    artifacts: list[BeatArtifact],
    cards: list[Card],
    accents: list[AccentOverlay | None],
    full_audio_path: Path,
    out_dir: Path,
    run_id: str,
) -> Path:
    """Single-pass reel stitch.

    1. Write the global ASS (word-burst + per-beat accents).
    2. Render each beat's silent 1080×1920 clip in parallel.
    3. ONE final ffmpeg invocation: concat filter (sample-accurate) +
       subtitles filter (libass) + AAC mux of the full TTS WAV.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(
            "stitch: ffmpeg / ffprobe not found on PATH. "
            "`brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux)."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    font_path = _find_font()
    font_family = _ass_font_name(font_path)

    n = len(beats)
    if len(artifacts) != n:
        raise RuntimeError(
            f"stitch: beats ({n}) and artifacts ({len(artifacts)}) length mismatch"
        )
    if len(accents) != n:
        raise RuntimeError(
            f"stitch: beats ({n}) and accents ({len(accents)}) length mismatch"
        )

    artifacts_by_idx: dict[int, BeatArtifact] = {a.idx: a for a in artifacts}

    # Step 1 — write ONE global ASS file.
    reel_ass = out_dir / "reel.ass"
    if any(a is not None for a in accents):
        write_reel_ass_with_accents(
            cards=cards,
            beats=beats,
            accents=accents,
            out_path=reel_ass,
            font_name=font_family,
        )
    else:
        write_reel_ass(cards, reel_ass, font_name=font_family)

    # Step 2 — render each beat's SILENT clip, with BOUNDED concurrency (see
    # _render_beats): a semaphore caps simultaneous encodes so a many-beat reel
    # can't OOM-kill ffmpeg (SIGKILL/-9) on a memory-limited node.
    clip_paths = await _render_beats(beats, artifacts_by_idx, out_dir)

    # Step 3 — single ffmpeg invocation.
    final = out_dir / "reel.mp4"
    await _single_pass_assemble(
        clip_paths=clip_paths,
        audio_path=full_audio_path,
        ass_path=reel_ass,
        font_path=font_path,
        out_path=final,
    )
    _ = run_id  # accepted for forward-compat / log correlation
    return final


__all__ = ["stitch_reel"]
