"""Remotion animated lower-third — an optional pipeline overlay.

Renders the ``LowerThird`` Remotion composition (the ``remotion/`` project at the
repo root) to a transparent PNG sequence for a given title, and provides the
ffmpeg input args + overlay filter to composite it over a reel for the first
``lower_third_duration_s`` seconds.

Gated by ``ReelFinishConfig.lower_third_enabled``; a config-driven alternative to
the ASS title banner (``captions._banner_events``). Requires Node + a Chromium
(``remotion/`` is installed via ``npm install`` there; pass a browser path when
the system Chromium isn't auto-discovered).

Design note: a PNG sequence (rgba) is used rather than ProRes/WebM because
freetype/libass-free alpha compositing through ffmpeg ``overlay`` is only
reliable from an alpha-carrying source, and Remotion's ProRes 4444 / VP8 paths
did not emit an alpha channel in this environment.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

# Repo-root ``remotion/`` project: this file is src/reel_af/render/lower_third.py
_DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "remotion"
_COMPOSITION_ID = "LowerThird"
_ENTRY = "src/index.ts"
_FRAME_GLOB = "element-*.png"
_FRAME_PATTERN = "element-%03d.png"


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    val = getattr(cfg, name, None) if cfg is not None else None
    return default if val is None else val


def project_dir(cfg: Any = None) -> Path:
    """The Remotion project dir — ``lower_third_project_dir`` or the repo default."""
    override = str(_cfg(cfg, "lower_third_project_dir", "")).strip()
    return Path(override) if override else _DEFAULT_PROJECT_DIR


def render_lower_third(
    title: str,
    out_seq_dir: Path,
    *,
    accent: str = "#7E22CE",
    chrome: Optional[str] = None,
    cfg: Any = None,
    force: bool = False,
) -> Path:
    """Render the lower-third to a transparent PNG sequence in ``out_seq_dir``.

    Skips rendering when the sequence already exists (unless ``force``), so the
    same title is only rendered once. Returns the sequence directory.
    """
    out_seq_dir = Path(out_seq_dir)
    if not force and out_seq_dir.exists() and any(out_seq_dir.glob(_FRAME_GLOB)):
        return out_seq_dir
    out_seq_dir.mkdir(parents=True, exist_ok=True)
    accent = str(_cfg(cfg, "lower_third_accent", accent))
    props = json.dumps({"title": title, "accent": accent})
    cmd = [
        "npx", "remotion", "render", _ENTRY, _COMPOSITION_ID, str(out_seq_dir),
        f"--props={props}", "--sequence", "--image-format=png",
    ]
    if chrome:
        cmd.append(f"--browser-executable={chrome}")
    subprocess.run(cmd, cwd=str(project_dir(cfg)), check=True, capture_output=True)
    return out_seq_dir


def input_args(seq_dir: Path, fps: int = 30) -> list[str]:
    """ffmpeg input args for the lower-third PNG sequence (place before its use)."""
    return ["-framerate", str(fps), "-i", str(Path(seq_dir) / _FRAME_PATTERN)]


def overlay_filter(in_label: str, out_label: str, lt_input_index: int, cfg: Any = None) -> str:
    """ffmpeg ``overlay`` filter compositing the lower-third over ``in_label`` for
    the first ``lower_third_duration_s`` seconds → ``out_label``."""
    dur = float(_cfg(cfg, "lower_third_duration_s", 6.0))
    return f"[{in_label}][{lt_input_index}:v]overlay=0:0:enable='between(t,0,{dur})'[{out_label}]"


__all__ = ["project_dir", "render_lower_third", "input_args", "overlay_filter"]
