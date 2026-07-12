"""Remotion script-synced middle-third overlay — the ``middle-third-dynamic`` format.

Bakes the previously-scratch ``mt_batch`` driver into reusable, preset-driven
functions: group whisper words into short phrases, turn each source window into
Remotion ``Segment`` props, render the ``MiddleThird`` composition to a
transparent PNG sequence, and composite it over a source window via ffmpeg.

Every tunable (phrase grouping, hold, accent, vertical anchor, composition id)
is read from a preset dict (``presets.load_preset``) — no format literals here.
Alpha compositing uses a PNG sequence read by glob, matching ``lower_third`` and
the ``remotion-pngseq-alpha-overlay`` note (Remotion pads the frame index to the
digit-width of the total frame count, so a fixed ``%0Nd`` pattern is fragile).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from reel_af.render.lower_third import input_args, overlay_effect_props

# Repo-root ``remotion/`` project: this file is src/reel_af/render/middle_third.py
_DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "remotion"
_ENTRY = "src/index.ts"
_FRAME_GLOB = "element-*.png"

Word = tuple[float, float, str]
Segment = dict[str, Any]


def project_dir(preset: dict[str, Any] | None = None) -> Path:
    """The Remotion project dir — ``remotion_project_dir`` override or repo default."""
    override = str((preset or {}).get("remotion_project_dir", "")).strip()
    return Path(override) if override else _DEFAULT_PROJECT_DIR


def load_whisper_words(json_path: Path) -> list[Word]:
    """Flatten a whisper ``--word_timestamps`` JSON (``segments[].words[]``) into
    ordered ``(start_s, end_s, text)`` tuples. Used to reuse a cached transcript
    instead of re-running whisper. Empty tokens are dropped."""
    data = json.loads(Path(json_path).read_text())
    out: list[Word] = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            out.append((float(w["start"]), float(w["end"]), text))
    return out


def group_words(
    words: list[Word], *, max_words: int, max_dur_s: float, max_gap_s: float
) -> list[Word]:
    """Group consecutive words into short phrases: break on word-count, phrase
    duration, or an inter-word gap. Returns ``(start_s, end_s, text)`` phrases."""
    phrases: list[Word] = []
    cur: list[Word] = []
    for st, en, w in words:
        if cur and (
            len(cur) >= max_words
            or en - cur[0][0] > max_dur_s
            or st - cur[-1][1] > max_gap_s
        ):
            phrases.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
        cur.append((st, en, w))
    if cur:
        phrases.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    return phrases


def window_segments(
    words: list[Word], t0: float, t1: float, preset: dict[str, Any], *, fps: int
) -> list[Segment]:
    """The Remotion ``Segment`` list for the ``[t0, t1)`` window: phrases grouped
    per preset, timed relative to the window, each held ``phrase_hold_s`` but
    yielding to the next phrase / window end."""
    hold_s = float(preset.get("phrase_hold_s", 0.6))
    win = [(st - t0, en - t0, w) for st, en, w in words if st >= t0 and en <= t1]
    phrases = group_words(
        win,
        max_words=int(preset["phrase_max_words"]),
        max_dur_s=float(preset["phrase_max_dur_s"]),
        max_gap_s=float(preset["phrase_gap_s"]),
    )
    segs: list[Segment] = []
    for i, (st, en, text) in enumerate(phrases):
        nxt = phrases[i + 1][0] if i + 1 < len(phrases) else (t1 - t0)
        end = min(en + hold_s, nxt)
        frm = int(round(st * fps))
        dur = max(fps // 2, int(round((end - st) * fps)))
        if preset.get("phrase_uppercase"):
            text = text.upper()
        segs.append({"text": text, "from": frm, "durationInFrames": dur})
    return segs


def render_overlay(
    segments: list[Segment],
    total_frames: int,
    out_seq_dir: Path,
    preset: dict[str, Any],
    *,
    chrome: str | None = None,
    force: bool = False,
    runner: Any = subprocess.run,
) -> Path:
    """Render the preset's Remotion composition to a transparent PNG sequence.

    Props (segments, accent, vertical anchor, total frames, plus any tuned effect
    props merged onto ``preset``) are written to a sidecar file rather than passed
    inline — a window's segment list can exceed the shell arg length. Skips
    rendering when the sequence already exists (unless ``force``). The Remotion
    invocation goes through the injected ``runner`` (default ``subprocess.run``) so
    tests can drive the merge + prop emission without a Node/Chromium subprocess.
    Returns ``out_seq_dir``."""
    out_seq_dir = Path(out_seq_dir)
    if not force and out_seq_dir.exists() and any(out_seq_dir.glob(_FRAME_GLOB)):
        return out_seq_dir
    out_seq_dir.mkdir(parents=True, exist_ok=True)
    props = {
        "accent": str(preset["overlay_accent"]),
        "segments": segments,
        "totalFrames": total_frames,
        "verticalAnchor": float(preset.get("overlay_vertical_anchor", 0.5)),
        **overlay_effect_props(preset),
    }
    if preset.get("card_opacity") is not None:
        props["cardOpacity"] = float(preset["card_opacity"])
    if preset.get("phrase_uppercase") is not None:
        props["textTransform"] = "uppercase" if preset["phrase_uppercase"] else "none"
    props_path = out_seq_dir.parent / "props.json"
    props_path.write_text(json.dumps(props))
    cmd = [
        "npx", "remotion", "render", _ENTRY, str(preset["remotion_composition"]),
        str(out_seq_dir), f"--props={props_path}", "--sequence", "--image-format=png",
    ]
    if chrome:
        cmd.append(f"--browser-executable={chrome}")
    runner(cmd, cwd=str(project_dir(preset)), check=True, capture_output=True)
    return out_seq_dir


def composite_window(
    source: Path,
    t0: float,
    dur_s: float,
    seq_dir: Path,
    out: Path,
    *,
    fps: int = 30,
    runner: Any = subprocess.run,
) -> Path:
    """Composite the PNG-sequence overlay over the ``[t0, t0+dur_s)`` window of
    ``source`` → ``out`` (H.264/aac). Reads the sequence by glob (see module doc)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{t0}", "-t", f"{dur_s}", "-i", str(source),
        *input_args(seq_dir, fps),
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=0[v]",
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        str(out),
    ]
    runner(cmd, check=True)
    return out


__all__ = [
    "project_dir",
    "load_whisper_words",
    "group_words",
    "window_segments",
    "render_overlay",
    "composite_window",
]
