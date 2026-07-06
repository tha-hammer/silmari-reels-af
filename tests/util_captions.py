"""Shared helpers for the caption/banner (B2/B3/B4) tests.

Kept out of ``util.py`` (owned by another agent) to avoid a file-reservation
conflict. Provides:
  • a duck-typed ``StubFinishConfig`` mirroring the fields ``captions.py``
    reads off ``ReelFinishConfig`` — so B3/B4 are testable before B0 lands and
    keep passing once the real config is imported.
  • tiny ffmpeg reel/​whisper-JSON fixtures.
  • an ASS ``Dialogue`` parser so tests can assert on emitted geometry/timing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)


# ───── duck-typed stand-in for ReelFinishConfig (B0) ─────────────────


@dataclass
class StubStyle:
    """Only the attributes ``captions.py`` reads off a style object.

    Every field is optional at read time (the module has proven defaults),
    so a bare object with a couple overrides is a valid style.
    """

    fontname: str = "Arial"
    fontsize: int = 58
    primary: str = "&H00FFFFFF"


@dataclass
class StubFinishConfig:
    canvas_w: int = 1080
    canvas_h: int = 1920
    center_x: int = 540
    caption_safe_y: int = 1330
    divider_y: int = 772
    caption_max_words: int = 4
    caption_max_dur_s: float = 1.8
    caption_gap_s: float = 0.35
    caption_uppercase: bool = True
    banner_uppercase: bool = True
    caption_style: StubStyle = field(default_factory=lambda: StubStyle(fontsize=58, primary="&H00FFFFFF"))
    banner_style: StubStyle = field(default_factory=lambda: StubStyle(fontsize=44, primary="&H0000FFEA"))


# ───── ffmpeg / whisper fixtures ─────────────────────────────────────


def make_silent_reel(path: Path, *, seconds: float = 2.0, w: int = 1080, h: int = 1920) -> Path:
    """Render a real vertical mp4 (color source + silent audio) via ffmpeg.

    Used to exercise the *real* ffprobe/wav-extraction path in ``caption_words``
    without depending on whisper. Speech is supplied by an injected transcriber.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:d={seconds}:r=30",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def fake_whisper_json(words: list[tuple[float, float, str]]) -> dict:
    """Build a whisper-ctranslate2-shaped JSON payload from word tuples."""
    return {
        "segments": [
            {"words": [{"start": st, "end": en, "word": f" {w}"} for st, en, w in words]}
        ]
    }


# ───── ASS parsing ───────────────────────────────────────────────────


@dataclass
class DialogueLine:
    start: float
    end: float
    style: str
    x: int
    y: int
    text: str


_POS_RE = re.compile(r"\\pos\((\d+),\s*(\d+)\)")


def _parse_ass_time(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_dialogues(ass_text: str) -> list[DialogueLine]:
    """Extract every ``Dialogue:`` event with its \\pos and payload text."""
    out: list[DialogueLine] = []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = line[len("Dialogue:"):].strip()
        # Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        parts = body.split(",", 9)
        start = _parse_ass_time(parts[1].strip())
        end = _parse_ass_time(parts[2].strip())
        style = parts[3].strip()
        raw = parts[9]
        m = _POS_RE.search(raw)
        x, y = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
        text = _POS_RE.sub("", raw)
        text = re.sub(r"\{[^}]*\}", "", text)  # drop any remaining override blocks
        out.append(DialogueLine(start, end, style, x, y, text))
    return out
