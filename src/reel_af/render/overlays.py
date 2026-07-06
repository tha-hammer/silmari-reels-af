"""Line-timed overlays for real-footage clips.

This module owns the reusable part of the old A1 ``footage_polish.py`` path:
zoom punch-ins and full-frame visual cut-ins over source footage. It is a
library API, so callers provide any downloaded/generated assets and this module
only builds/runs the local ffmpeg composition.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
FPS = 30
DEFAULT_ZOOM = 1.5
OVERLAY_TIMEOUT_S = 120.0

CutInKind = Literal["zoom", "visual"]


class OverlayError(RuntimeError):
    """Raised when an overlay plan cannot be rendered."""


class CutInOverlay(BaseModel):
    """A timed overlay over one source-footage segment."""

    model_config = ConfigDict(extra="forbid")

    type: CutInKind
    at_s: float = Field(ge=0)
    until_s: float = Field(gt=0)
    line: str | None = None
    image_prompt: str | None = None
    zoom_focus: str = "center"

    @model_validator(mode="after")
    def _valid_window_and_payload(self) -> "CutInOverlay":
        if self.until_s <= self.at_s:
            raise ValueError("cut-in until_s must be greater than at_s")
        if self.type == "visual" and not self.image_prompt:
            raise ValueError("visual cut-ins require image_prompt")
        return self


class OverlayFilterGraph(BaseModel):
    """Pure ffmpeg overlay graph output."""

    model_config = ConfigDict(extra="forbid")

    filter_complex: str
    video_label: str = "[v]"
    visual_input_count: int = Field(ge=0)


def normalize_cut_ins(cut_ins: Iterable[CutInOverlay | Mapping[str, Any]]) -> list[CutInOverlay]:
    """Validate and sort cut-ins by absolute source time."""

    normalized = [
        item if isinstance(item, CutInOverlay) else CutInOverlay.model_validate(item)
        for item in cut_ins
    ]
    return sorted(normalized, key=lambda cut_in: (cut_in.at_s, cut_in.until_s, cut_in.type))


def visual_prompts(cut_ins: Iterable[CutInOverlay | Mapping[str, Any]]) -> list[str]:
    """Return visual-image prompts in the same order ffmpeg image inputs are consumed."""

    return [
        cut_in.image_prompt or ""
        for cut_in in normalize_cut_ins(cut_ins)
        if cut_in.type == "visual"
    ]


def zoom_crop_xy(
    focus: str,
    zoom: float = DEFAULT_ZOOM,
    *,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
) -> tuple[int, int]:
    """Crop origin for a zoomed 9:16 canvas."""

    if zoom <= 1.0:
        raise ValueError("zoom must be greater than 1.0")
    scaled_w = int(width * zoom)
    scaled_h = int(height * zoom)
    center_x = (scaled_w - width) // 2
    center_y = (scaled_h - height) // 2
    focus_map = {
        "center": (center_x, center_y),
        "upper": (center_x, int((scaled_h - height) * 0.12)),
        "lower": (center_x, int((scaled_h - height) * 0.9)),
        "left": (0, center_y),
        "right": (scaled_w - width, center_y),
    }
    return focus_map.get((focus or "center").lower(), (center_x, center_y))


def build_overlay_filtergraph(
    cut_ins: Iterable[CutInOverlay | Mapping[str, Any]],
    *,
    segment_start_s: float,
    segment_duration_s: float,
    visual_input_start: int = 1,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
    fps: int = FPS,
    zoom: float = DEFAULT_ZOOM,
) -> OverlayFilterGraph:
    """Build a pure ffmpeg graph for zoom and visual cut-ins.

    ``segment_start_s`` is the absolute source timestamp that corresponds to
    local t=0 in the segment clip. Cut-in windows are absolute source times and
    are clamped to the segment duration.
    """

    if segment_start_s < 0:
        raise ValueError("segment_start_s must be nonnegative")
    if segment_duration_s <= 0:
        raise ValueError("segment_duration_s must be greater than zero")
    if visual_input_start < 1:
        raise ValueError("visual_input_start must be at least 1")

    cut_in_list = normalize_cut_ins(cut_ins)
    active_cut_ins = [
        cut_in
        for cut_in in cut_in_list
        if _relative_window(cut_in, segment_start_s, segment_duration_s) is not None
    ]

    fc: list[str] = [
        (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p[base_src]"
        )
    ]

    zoom_cut_ins = [cut_in for cut_in in active_cut_ins if cut_in.type == "zoom"]
    if zoom_cut_ins:
        split_labels = "".join(f"[z{i}]" for i in range(len(zoom_cut_ins)))
        fc.append(f"[base_src]split={len(zoom_cut_ins) + 1}[base]{split_labels}")
        base_label = "base"
    else:
        base_label = "base_src"

    zoom_idx = 0
    visual_idx = 0
    overlay_labels: dict[int, str] = {}
    for idx, cut_in in enumerate(active_cut_ins):
        if cut_in.type == "zoom":
            x, y = zoom_crop_xy(cut_in.zoom_focus, zoom, width=width, height=height)
            fc.append(
                f"[z{zoom_idx}]scale={int(width * zoom)}:{int(height * zoom)},"
                f"crop={width}:{height}:{x}:{y},setsar=1[cut{idx}]"
            )
            overlay_labels[idx] = f"cut{idx}"
            zoom_idx += 1
            continue

        input_idx = visual_input_start + visual_idx
        fc.append(
            f"[{input_idx}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p[cut{idx}]"
        )
        overlay_labels[idx] = f"cut{idx}"
        visual_idx += 1

    previous = base_label
    for idx, cut_in in enumerate(active_cut_ins):
        window = _relative_window(cut_in, segment_start_s, segment_duration_s)
        if window is None:
            continue
        start_s, end_s = window
        out_label = f"ov{idx}"
        fc.append(
            f"[{previous}][{overlay_labels[idx]}]"
            f"overlay=enable='between(t,{start_s:.3f},{end_s:.3f})'[{out_label}]"
        )
        previous = out_label

    fc.append(f"[{previous}]null[v]")
    return OverlayFilterGraph(
        filter_complex=";".join(fc),
        visual_input_count=sum(1 for cut_in in active_cut_ins if cut_in.type == "visual"),
    )


async def render_overlay_clip(
    segment_path: Path,
    cut_ins: Iterable[CutInOverlay | Mapping[str, Any]],
    visual_images: Sequence[Path],
    out_path: Path,
    *,
    segment_start_s: float,
    segment_duration_s: float | None = None,
    timeout_s: float = OVERLAY_TIMEOUT_S,
) -> Path:
    """Render one source segment with overlays using local ffmpeg."""

    segment_path = Path(segment_path)
    if not segment_path.exists():
        raise OverlayError(f"segment file does not exist: {segment_path}")

    duration_s = segment_duration_s if segment_duration_s is not None else probe_duration(segment_path)
    graph = build_overlay_filtergraph(
        cut_ins,
        segment_start_s=segment_start_s,
        segment_duration_s=duration_s,
    )
    if graph.visual_input_count != len(visual_images):
        raise OverlayError(
            "visual image count does not match visual cut-ins: "
            f"expected {graph.visual_input_count}, got {len(visual_images)}"
        )
    for image in visual_images:
        if not Path(image).exists():
            raise OverlayError(f"visual image does not exist: {image}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_overlay_ffmpeg_cmd(
        segment_path,
        visual_images,
        graph,
        out_path,
        duration_s=duration_s,
    )
    await _run_ffmpeg(cmd, timeout_s=timeout_s)
    return out_path


def build_overlay_ffmpeg_cmd(
    segment_path: Path,
    visual_images: Sequence[Path],
    graph: OverlayFilterGraph,
    out_path: Path,
    *,
    duration_s: float,
) -> list[str]:
    """Build the ffmpeg command for a precomputed overlay graph."""

    if duration_s <= 0:
        raise ValueError("duration_s must be greater than zero")

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(segment_path)]
    for image in visual_images:
        cmd += ["-loop", "1", "-i", str(image)]
    cmd += [
        "-filter_complex",
        graph.filter_complex,
        "-map",
        graph.video_label,
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-t",
        f"{duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return cmd


def probe_duration(path: Path) -> float:
    """Return media duration using ffprobe."""

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
        capture_output=True,
        text=True,
        check=True,
    )
    return float(proc.stdout.strip())


def _relative_window(
    cut_in: CutInOverlay,
    segment_start_s: float,
    segment_duration_s: float,
) -> tuple[float, float] | None:
    start_s = max(0.0, cut_in.at_s - segment_start_s)
    end_s = min(segment_duration_s, cut_in.until_s - segment_start_s)
    if end_s <= start_s:
        return None
    return start_s, end_s


async def _run_ffmpeg(cmd: Sequence[str], *, timeout_s: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(f"overlay ffmpeg timed out after {timeout_s:.1f}s") from None

    if proc.returncode != 0:
        raise OverlayError(
            "overlay ffmpeg failed "
            f"(exit {proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(str(part)) for part in cmd)}\n"
            f"  stderr: {stderr.decode(errors='replace')[-1200:]}"
        )


__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "DEFAULT_ZOOM",
    "FPS",
    "CutInOverlay",
    "OverlayError",
    "OverlayFilterGraph",
    "build_overlay_ffmpeg_cmd",
    "build_overlay_filtergraph",
    "normalize_cut_ins",
    "probe_duration",
    "render_overlay_clip",
    "visual_prompts",
    "zoom_crop_xy",
]
