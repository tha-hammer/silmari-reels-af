"""v2 Phase 6 — single-pass stitch (audio-as-timeline architecture).

Replaces the older multi-step path (per-shot ASS + per-shot karaoke burn +
concat demuxer + final mux). New flow:

  1. Per-shot ffmpeg renders SILENT video only — scale + crop + trim. No
     subtitles, no accent, no audio. Runs in parallel.

  2. ONE final ffmpeg invocation: ``concat`` FILTER (sample-accurate,
     unlike the concat demuxer) → ``subtitles`` filter (libass) → AAC mux.
     The single global ASS file holds every card across every shot, timed
     in reel-global seconds. One AAC encode for the whole reel — no
     per-shot priming drift.

Accents (Layer 2) are NOT rendered yet; :func:`_build_accent_filter` is
preserved as scaffolding for the follow-up that will add them as ASS
Layer 2 events on the global timeline.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
import subprocess
from pathlib import Path

from reel_af.v2.models import (
    AccentOverlay,
    Shot,
    ShotArtifact,
    ShotVisual,
)
from reel_af.v2.planning.safe_zone import (
    ACCENT_FILL,
    ACCENT_FONT_PX,
    ACCENT_LOWER_Y_PCT,
    ACCENT_STROKE,
    ACCENT_STROKE_PX,
    ACCENT_UPPER_Y_PCT,
    CANVAS_H,
    CANVAS_W,
    SUBTITLE_HIGHLIGHT,
)
from reel_af.v2.render.subtitle_ass import write_reel_ass

TARGET_W = CANVAS_W
TARGET_H = CANVAS_H
FPS = 30  # match Veo's native 24-30 fps to skip interpolation.

# ───── Font discovery ────────────────────────────────────────────────

_FONT_CANDIDATES: tuple[str, ...] = (
    # Prefer Montserrat if installed — that's what safe_zone metrics assume.
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
    raise RuntimeError("stitch_v2: no usable font found.")


def _ass_font_name(font_path: str) -> str:
    """Best-effort family name from the discovered font path.

    libass needs the family name, not the filesystem path. Falls back to
    ``"sans-serif"`` if the file basename doesn't look familiar.
    """
    stem = Path(font_path).stem.lower()
    if "montserrat" in stem:
        return "Montserrat"
    if "arial" in stem:
        return "Arial"
    if "helvetica" in stem:
        return "Helvetica"
    if "dejavu" in stem:
        return "DejaVu Sans"
    if "liberation" in stem:
        return "Liberation Sans"
    return "sans-serif"


# ───── ffmpeg text + duration helpers ────────────────────────────────


def _ffmpeg_escape(s: str) -> str:
    """Escape a string for use inside a ``drawtext=text='...'`` filter arg.

    Same hardening as before: swap unsafe quotes, neutralize ``%`` (which
    introduces ffmpeg's ``%{...}`` placeholder syntax), backslash-escape
    filter-graph separators.
    """
    s = s.replace("'", "’")  # right single quote
    s = s.replace('"', "”")  # right double quote
    s = s.replace("%", " PCT")
    return (
        s.replace("\\", "\\\\")
         .replace(":", r"\:")
         .replace(",", r"\,")
    )


def _ffmpeg_path_arg(path: Path | str) -> str:
    """Escape a filesystem path for use as a filter argument value.

    Filter-graph parsing treats ``:`` as the kv separator and ``\\`` as the
    escape lead-in, so windows-style or simply colon-containing paths need
    both escaped.
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


def _build_accent_filter(
    accent: AccentOverlay,
    shot_duration_s: float,
    font_path: str,
) -> str:
    """Build a Layer 2 drawtext filter for an accent overlay.

    NOT WIRED into the single-pass path yet. Preserved as scaffolding for
    the follow-up that will move Layer 2 accents to ASS events on the
    global timeline (\\an2/\\an5/\\an8 + reel-coordinate timing windows).
    """
    font_px = ACCENT_FONT_PX
    if accent.position == "upper_third":
        y_px = int(ACCENT_UPPER_Y_PCT * CANVAS_H)
    else:
        y_px = int(ACCENT_LOWER_Y_PCT * CANVAS_H)

    font_arg = _ffmpeg_path_arg(font_path)
    kwargs = (
        f"fontfile={font_arg}"
        f":fontsize={font_px}"
        f":fontcolor={ACCENT_FILL}"
        f":bordercolor={ACCENT_STROKE}"
        f":borderw={ACCENT_STROKE_PX}"
    )
    text_esc = _ffmpeg_escape(accent.text.upper())
    return (
        f"drawtext={kwargs}"
        f":text='{text_esc}'"
        f":x=(w-text_w)/2"
        f":y={y_px}"
        f":enable='between(t,0,{shot_duration_s:.3f})'"
    )


# ───── Per-shot SILENT render ────────────────────────────────────────


async def _render_shot(
    shot: Shot,
    artifact: ShotArtifact,
    out_path: Path,
) -> None:
    """Render one shot as a SILENT, untextured video clip at 1080×1920.

    No subtitles, no accent, no audio — just scale + crop the source MP4 to
    the canvas, trim to ``shot.duration_s``, and emit at our codec settings
    so all clips concat cleanly in the next pass.
    """
    if artifact.video_path is None:
        raise RuntimeError(
            f"stitch_v2: shot {shot.idx} has no video_path on artifact."
        )

    vfilter = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},"
        f"fps={FPS},"
        f"format=yuv420p,"
        f"trim=end={shot.duration_s:.3f},setpts=PTS-STARTPTS"
    )

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(artifact.video_path),
        "-filter_complex", f"[0:v]{vfilter}[v]",
        "-map", "[v]",
        "-an",                                      # no audio
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "18",
        "-r", str(FPS),
        "-movflags", "+faststart",
        "-t", f"{shot.duration_s:.3f}",
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
            f"stitch_v2: shot {shot.idx} silent render failed "
            f"(exit {proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"  stderr: {stderr.decode(errors='replace')[-800:]}"
        )


# ───── Single-pass final assembly ────────────────────────────────────


async def _single_pass_assemble(
    clip_paths: list[Path],
    audio_path: Path,
    ass_path: Path,
    font_path: str,
    out_path: Path,
) -> None:
    """One ffmpeg call: concat filter + libass burn + AAC mux.

    The ``concat`` FILTER (not the concat demuxer) is sample-accurate at the
    re-encoded boundary — there's no sub-frame drift between clips. Audio
    is muxed in the same pass so the AAC encoder primes once, at the very
    start of the reel, not once per clip.
    """
    n = len(clip_paths)
    if n == 0:
        raise RuntimeError("stitch_v2: no clips to assemble.")

    ass_arg = _ffmpeg_path_arg(ass_path)
    font_dir_arg = _ffmpeg_path_arg(Path(font_path).parent)

    # Build the concat filter input spec: [0:v][1:v]...[N-1:v]
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
            f"stitch_v2: single-pass assemble failed (exit {proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"  stderr: {stderr.decode(errors='replace')[-1200:]}"
        )


# ───── Entry point ──────────────────────────────────────────────────


async def stitch_v2(
    shots: list[Shot],
    visuals: list[ShotVisual],          # accepted for interface symmetry; not consumed
    artifacts: list[ShotArtifact],
    accents: list[AccentOverlay | None], # accepted; Layer 2 not rendered yet (TODO)
    full_audio_path: Path,
    out_dir: Path,
    run_id: str,
    subtitle_highlight_color: str = SUBTITLE_HIGHLIGHT,  # noqa: ARG001
) -> Path:
    """Single-pass stitch: per-shot silent video render in parallel, then
    ONE final ffmpeg invocation that concats with sample-accurate filter +
    burns the global ASS subtitles + muxes the full TTS audio in one encode.

    Eliminates the cumulative AAC priming + per-shot ASS clock translation
    of the previous architecture.
    """
    # TODO(accents): Layer 2 accents not rendered yet. The follow-up will
    # emit them as ASS events on the global timeline alongside the karaoke
    # layer. See _build_accent_filter for the carry-over scaffolding.
    _ = accents

    # Defensive: bail loudly if ffmpeg toolchain isn't installed.
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(
            "stitch_v2: ffmpeg / ffprobe not found on PATH. "
            "`brew install ffmpeg` (macOS) or apt install ffmpeg (Linux)."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    font_path = _find_font()

    # Validate parallel-list lengths up front — easier to debug here than
    # halfway through a fan-out of ffmpeg subprocesses.
    n = len(shots)
    if len(artifacts) != n or len(visuals) != n:
        raise RuntimeError(
            "stitch_v2: input list length mismatch — "
            f"shots={n} visuals={len(visuals)} artifacts={len(artifacts)}"
        )

    artifacts_by_idx: dict[int, ShotArtifact] = {a.idx: a for a in artifacts}

    # Step 1 — write ONE global ASS file covering every card across every
    # shot, timed in reel-global seconds. libass burns this in the final
    # pass, on top of the concatenated silent video.
    reel_ass = out_dir / "reel.ass"
    write_reel_ass(shots, reel_ass, font_name=_ass_font_name(font_path))

    # Step 2 — render each shot's SILENT clip in parallel. Each ffmpeg
    # subprocess pegs ~1 core; wall-time collapses to ~max(per-shot).
    clip_paths: list[Path] = []
    render_jobs: list[asyncio.Task[None]] = []
    for shot in shots:
        artifact = artifacts_by_idx.get(shot.idx)
        if artifact is None:
            raise RuntimeError(
                f"stitch_v2: no artifact found for shot idx={shot.idx}"
            )
        out_clip = out_dir / f"clip-{shot.idx:02d}-silent.mp4"
        clip_paths.append(out_clip)
        render_jobs.append(
            asyncio.create_task(
                _render_shot(shot=shot, artifact=artifact, out_path=out_clip)
            )
        )
    await asyncio.gather(*render_jobs)

    # Step 3 — single ffmpeg invocation: concat filter (sample-accurate)
    # → subtitles filter (libass karaoke) → AAC mux. One encode for the
    # entire reel; one priming sample at the head; no drift.
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


# Re-export the helpers for tests / orchestrator introspection.
__all__ = [
    "stitch_v2",
    "_render_shot",
    "_single_pass_assemble",
    "_build_accent_filter",
    "_ass_font_name",
    "_ffmpeg_escape",
    "_ffmpeg_path_arg",
    "_find_font",
    "_probe_duration",
]
