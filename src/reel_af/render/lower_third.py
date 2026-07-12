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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

# Repo-root ``remotion/`` project: this file is src/reel_af/render/lower_third.py
_DEFAULT_PROJECT_DIR = Path(__file__).resolve().parents[3] / "remotion"
_COMPOSITION_ID = "LowerThird"
_ENTRY = "src/index.ts"
_FRAME_GLOB = "element-*.png"
_DEFAULT_TITLE_WORDS = 8


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    if isinstance(cfg, Mapping):
        val = cfg.get(name)
    else:
        val = getattr(cfg, name, None) if cfg is not None else None
    return default if val is None else val


def project_dir(cfg: Any = None) -> Path:
    """The Remotion project dir — ``lower_third_project_dir`` or the repo default."""
    override = str(_cfg(cfg, "lower_third_project_dir", "")).strip()
    return Path(override) if override else _DEFAULT_PROJECT_DIR


# Snake-cased tunable key → (camelCase Remotion prop, caster). Shared by both
# overlays (``middle_third`` imports this) — the one place the naming boundary is
# crossed. A prop is emitted only when the merged cfg actually carries the key, so
# an un-tuned render omits it and the composition's ``defaultProps`` (== the old
# hardcoded literal) fills in, keeping un-tuned output pixel-identical.
_EFFECT_PROP_BY_KEY: dict[str, tuple[str, Any]] = {
    "font_scale": ("fontScale", float),
    "accent_bar_px": ("accentBarPx", int),
    "corner_radius": ("cornerRadius", int),
    "anim_style": ("anim", str),
    "anim_damping": ("animDamping", float),
    "anim_mass": ("animMass", float),
}


def overlay_effect_props(cfg: Any = None) -> dict[str, Any]:
    """Map the effect tunables shared by both overlays (font/accent-bar/corner/
    animation) from a merged cfg to camelCase Remotion props, casting each and
    omitting any key the cfg does not carry."""
    props: dict[str, Any] = {}
    for key, (prop, cast) in _EFFECT_PROP_BY_KEY.items():
        value = _cfg(cfg, key, None)
        if value is not None:
            props[prop] = cast(value)
    return props


def render_lower_third(
    title: str,
    out_seq_dir: Path,
    *,
    accent: str = "#7E22CE",
    chrome: Optional[str] = None,
    cfg: Any = None,
    force: bool = False,
    runner: Any = subprocess.run,
) -> Path:
    """Render the lower-third to a transparent PNG sequence in ``out_seq_dir``.

    Skips rendering when the sequence already exists (unless ``force``), so the
    same title is only rendered once. Returns the sequence directory. The
    Remotion invocation goes through the injected ``runner`` (default
    ``subprocess.run``) so tests can capture the emitted ``--props`` payload
    without a Node/Chromium subprocess — mirrors ``composite_window``.
    """
    out_seq_dir = Path(out_seq_dir)
    if not force and out_seq_dir.exists() and any(out_seq_dir.glob(_FRAME_GLOB)):
        return out_seq_dir
    out_seq_dir.mkdir(parents=True, exist_ok=True)
    accent = str(_cfg(cfg, "lower_third_accent", _cfg(cfg, "overlay_accent", accent)))
    prop_dict: dict[str, Any] = {"title": title, "accent": accent, **overlay_effect_props(cfg)}
    if _cfg(cfg, "box_opacity", None) is not None:
        prop_dict["boxOpacity"] = float(_cfg(cfg, "box_opacity", None))
    props = json.dumps(prop_dict)
    cmd = [
        "npx", "remotion", "render", _ENTRY, _COMPOSITION_ID, str(out_seq_dir),
        f"--props={props}", "--sequence", "--image-format=png",
    ]
    if chrome:
        cmd.append(f"--browser-executable={chrome}")
    runner(cmd, cwd=str(project_dir(cfg)), check=True, capture_output=True)
    return out_seq_dir


def input_args(seq_dir: Path, fps: int = 30) -> list[str]:
    """ffmpeg input args for a Remotion PNG sequence (glob — Remotion pads the
    frame index to the digit-width of the total frame count, so a fixed %0Nd
    pattern is fragile; glob sorts correctly for any width)."""
    return ["-framerate", str(fps), "-pattern_type", "glob",
            "-i", str(Path(seq_dir) / _FRAME_GLOB)]


def overlay_filter(in_label: str, out_label: str, lt_input_index: int, cfg: Any = None) -> str:
    """ffmpeg ``overlay`` filter compositing the lower-third over ``in_label`` for
    the first ``lower_third_duration_s`` seconds → ``out_label``."""
    dur = float(_cfg(cfg, "lower_third_duration_s", 6.0))
    return (
        f"[{in_label}][{lt_input_index}:v]"
        f"overlay=0:0:eof_action=pass:enable='between(t,0,{dur})'[{out_label}]"
    )


def title_from_words(
    words: Sequence[tuple[float, float, str]],
    t0: float,
    t1: float,
    cfg: Any = None,
) -> str:
    """Build the lower-third title for a source window from preset/config or words."""
    configured = str(_cfg(cfg, "lower_third_title", "")).strip()
    if configured:
        return configured

    max_words = max(1, int(_cfg(cfg, "lower_third_title_words", _DEFAULT_TITLE_WORDS)))
    window_words = [
        text.strip()
        for start, end, text in words
        if start >= t0 and end <= t1 and text.strip()
    ]
    title = " ".join(window_words[:max_words]).strip()
    return title or "Untitled reel"


def composite_window(
    source: Path,
    t0: float,
    dur_s: float,
    seq_dir: Path,
    out: Path,
    *,
    fps: int = 30,
    cfg: Any = None,
    runner: Any = subprocess.run,
) -> Path:
    """Composite a lower-third PNG sequence over a preset-sized source window."""
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas_w = int(_cfg(cfg, "canvas_w", 1920))
    canvas_h = int(_cfg(cfg, "canvas_h", 1080))
    filter_complex = (
        f"[0:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
        f"crop={canvas_w}:{canvas_h},setsar=1[base];"
        f"{overlay_filter('base', 'v', 1, cfg)}"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{t0}", "-t", f"{dur_s}", "-i", str(source),
        *input_args(seq_dir, fps),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


__all__ = [
    "project_dir",
    "render_lower_third",
    "input_args",
    "overlay_filter",
    "title_from_words",
    "composite_window",
]
