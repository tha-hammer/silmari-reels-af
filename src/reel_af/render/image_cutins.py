"""Final-reel image cut-ins for the screenshare pane.

This module handles two concerns for the richer finish pass:

* generate still images from selected image moments using the existing Gemini
  first-frame path; and
* build/run an ffmpeg graph that fits those images into ``cfg.image_region``.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from agentfield.media_providers import OpenRouterProvider
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from reel_af.render.finish_config import ReelFinishConfig
from reel_af.render.images import generate_first_frame

IMAGE_CUTIN_TIMEOUT_S = 120.0


class ImageCutInError(RuntimeError):
    """Raised when image cut-in generation or rendering cannot complete."""


class ImageRegion(BaseModel):
    """Pixel region where images are placed inside the final reel."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0, validation_alias=AliasChoices("width", "w"))
    height: int = Field(gt=0, validation_alias=AliasChoices("height", "h"))


class _HasImageRegion(Protocol):
    image_region: Any


class ImageCutIn(BaseModel):
    """A generated-image moment on the final reel timeline."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start_s: float = Field(
        ge=0,
        validation_alias=AliasChoices("start_s", "t_start", "at_s"),
    )
    end_s: float = Field(
        gt=0,
        validation_alias=AliasChoices("end_s", "t_end", "until_s"),
    )
    image_prompt: str = Field(min_length=1)
    image_path: Path | None = None

    @model_validator(mode="after")
    def _valid_window(self) -> "ImageCutIn":
        if self.end_s <= self.start_s:
            raise ValueError("image cut-in end_s must be greater than start_s")
        self.image_prompt = self.image_prompt.strip()
        if not self.image_prompt:
            raise ValueError("image cut-in image_prompt must be non-empty")
        return self


class ImageOverlayFilterGraph(BaseModel):
    """Pure ffmpeg graph plus output metadata for image cut-ins."""

    model_config = ConfigDict(extra="forbid")

    filter_complex: str
    video_label: str = "[v]"
    image_input_count: int = Field(ge=0)


def normalize_image_cutins(
    cut_ins: Iterable[ImageCutIn | Mapping[str, Any] | Sequence[Any]],
) -> list[ImageCutIn]:
    """Validate cut-in moments and sort them by final-reel time."""

    normalized = [_coerce_image_cutin(cut_in) for cut_in in cut_ins]
    return sorted(normalized, key=lambda cut_in: (cut_in.start_s, cut_in.end_s))


async def generate_image_cutin(
    provider: OpenRouterProvider,
    cut_in: ImageCutIn | Mapping[str, Any] | Sequence[Any],
    *,
    idx: int,
    out_dir: Path,
    content_mode: str = "general",
) -> ImageCutIn:
    """Generate one cut-in image by wrapping ``generate_first_frame``."""

    normalized = _coerce_image_cutin(cut_in)
    image_path = await generate_first_frame(
        provider=provider,
        image_prompt=normalized.image_prompt,
        idx=idx,
        out_dir=out_dir,
        content_mode=content_mode,
    )
    return normalized.model_copy(update={"image_path": image_path})


async def generate_image_cutins(
    provider: OpenRouterProvider,
    cut_ins: Iterable[ImageCutIn | Mapping[str, Any] | Sequence[Any]],
    *,
    out_dir: Path,
    content_mode: str = "general",
) -> list[ImageCutIn]:
    """Generate all selected image moments in ffmpeg input order."""

    generated: list[ImageCutIn] = []
    for idx, cut_in in enumerate(normalize_image_cutins(cut_ins)):
        generated.append(
            await generate_image_cutin(
                provider,
                cut_in,
                idx=idx,
                out_dir=out_dir,
                content_mode=content_mode,
            )
        )
    return generated


def build_image_overlay_filtergraph(
    cut_ins: Iterable[ImageCutIn | Mapping[str, Any] | Sequence[Any]],
    *,
    config: ReelFinishConfig | _HasImageRegion,
    image_input_start: int = 1,
) -> ImageOverlayFilterGraph:
    """Build the pure ffmpeg graph for final-reel image cut-ins."""

    if image_input_start < 1:
        raise ValueError("image_input_start must be at least 1")

    cut_in_list = normalize_image_cutins(cut_ins)
    if not cut_in_list:
        return ImageOverlayFilterGraph(filter_complex="[0:v]null[v]", image_input_count=0)

    region = image_region_from_config(config)
    filters: list[str] = ["[0:v]setpts=PTS-STARTPTS[base]"]
    previous = "base"

    for idx, cut_in in enumerate(cut_in_list):
        input_idx = image_input_start + idx
        image_label = f"img{idx}"
        out_label = f"ov{idx}"
        filters.append(
            f"[{input_idx}:v]"
            f"scale={region.width}:{region.height}:force_original_aspect_ratio=increase,"
            f"crop={region.width}:{region.height},setsar=1,format=rgba"
            f"[{image_label}]"
        )
        filters.append(
            f"[{previous}][{image_label}]"
            f"overlay=x={region.x}:y={region.y}:"
            f"enable='between(t,{cut_in.start_s:.3f},{cut_in.end_s:.3f})'"
            f"[{out_label}]"
        )
        previous = out_label

    filters.append(f"[{previous}]format=yuv420p[v]")
    return ImageOverlayFilterGraph(
        filter_complex=";".join(filters),
        image_input_count=len(cut_in_list),
    )


def build_image_overlay(
    cut_ins: Iterable[ImageCutIn | Mapping[str, Any] | Sequence[Any]],
    *,
    config: ReelFinishConfig | _HasImageRegion,
    image_input_start: int = 1,
) -> str:
    """Return only the ffmpeg filter string for finish-stage composition."""

    return build_image_overlay_filtergraph(
        cut_ins,
        config=config,
        image_input_start=image_input_start,
    ).filter_complex


async def render_image_cutins(
    *,
    base_reel_path: Path,
    cut_ins: Iterable[ImageCutIn | Mapping[str, Any] | Sequence[Any]],
    out_path: Path,
    config: ReelFinishConfig | _HasImageRegion,
    timeout_s: float = IMAGE_CUTIN_TIMEOUT_S,
) -> Path:
    """Render image cut-ins onto a stitched final-reel mp4."""

    base_reel_path = Path(base_reel_path)
    if not base_reel_path.exists():
        raise ImageCutInError(f"base reel does not exist: {base_reel_path}")

    normalized = normalize_image_cutins(cut_ins)
    image_paths = image_paths_for_cutins(normalized)
    graph = build_image_overlay_filtergraph(normalized, config=config)
    duration_s = probe_duration(base_reel_path)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_image_overlay_ffmpeg_cmd(
        base_reel_path=base_reel_path,
        image_paths=image_paths,
        graph=graph,
        out_path=out_path,
        duration_s=duration_s,
    )
    await _run_ffmpeg(cmd, timeout_s=timeout_s)
    return out_path


def build_image_overlay_ffmpeg_cmd(
    *,
    base_reel_path: Path,
    image_paths: Sequence[Path],
    graph: ImageOverlayFilterGraph,
    out_path: Path,
    duration_s: float,
) -> list[str]:
    """Build the ffmpeg command for a precomputed image overlay graph."""

    if duration_s <= 0:
        raise ValueError("duration_s must be greater than zero")
    if len(image_paths) != graph.image_input_count:
        raise ImageCutInError(
            "image path count does not match filtergraph inputs: "
            f"expected {graph.image_input_count}, got {len(image_paths)}"
        )

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(base_reel_path)]
    for image_path in image_paths:
        cmd += ["-loop", "1", "-i", str(image_path)]
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


def image_paths_for_cutins(
    cut_ins: Iterable[ImageCutIn | Mapping[str, Any] | Sequence[Any]],
) -> list[Path]:
    """Return image paths in the same order the overlay graph consumes them."""

    paths: list[Path] = []
    for idx, cut_in in enumerate(normalize_image_cutins(cut_ins)):
        if cut_in.image_path is None:
            raise ImageCutInError(f"image cut-in {idx} has no image_path")
        image_path = Path(cut_in.image_path)
        if not image_path.exists():
            raise ImageCutInError(f"image cut-in file does not exist: {image_path}")
        paths.append(image_path)
    return paths


def image_region_from_config(config: ReelFinishConfig | _HasImageRegion) -> ImageRegion:
    """Read ``image_region`` from the shared finish config."""

    try:
        region = config.image_region
    except AttributeError as exc:
        raise ValueError("finish config must provide image_region") from exc

    if isinstance(region, ImageRegion):
        return region
    if isinstance(region, Mapping):
        return ImageRegion.model_validate(region)
    if isinstance(region, Sequence) and not isinstance(region, (str, bytes, bytearray)):
        if len(region) != 4:
            raise ValueError("image_region sequence must be (x, y, width, height)")
        x, y, width, height = region
        return ImageRegion(x=x, y=y, width=width, height=height)
    if all(hasattr(region, name) for name in ("x", "y", "w", "h")):
        return ImageRegion(x=region.x, y=region.y, width=region.w, height=region.h)
    return ImageRegion.model_validate(region)


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


def _coerce_image_cutin(cut_in: ImageCutIn | Mapping[str, Any] | Sequence[Any]) -> ImageCutIn:
    if isinstance(cut_in, ImageCutIn):
        return cut_in
    if isinstance(cut_in, Mapping):
        return ImageCutIn.model_validate(cut_in)
    if isinstance(cut_in, Sequence) and not isinstance(cut_in, (str, bytes, bytearray)):
        if len(cut_in) not in {3, 4}:
            raise ValueError(
                "image cut-in sequences must be (start_s, end_s, image_prompt[, image_path])"
            )
        payload: dict[str, Any] = {
            "start_s": cut_in[0],
            "end_s": cut_in[1],
            "image_prompt": cut_in[2],
        }
        if len(cut_in) == 4:
            payload["image_path"] = cut_in[3]
        return ImageCutIn.model_validate(payload)
    raise TypeError(f"unsupported image cut-in type: {type(cut_in)!r}")


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
        raise TimeoutError(f"image cut-in ffmpeg timed out after {timeout_s:.1f}s") from None

    if proc.returncode != 0:
        raise ImageCutInError(
            "image cut-in ffmpeg failed "
            f"(exit {proc.returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(str(part)) for part in cmd)}\n"
            f"  stderr: {stderr.decode(errors='replace')[-1200:]}"
        )


__all__ = [
    "IMAGE_CUTIN_TIMEOUT_S",
    "ImageCutIn",
    "ImageCutInError",
    "ImageOverlayFilterGraph",
    "ImageRegion",
    "build_image_overlay",
    "build_image_overlay_ffmpeg_cmd",
    "build_image_overlay_filtergraph",
    "generate_image_cutin",
    "generate_image_cutins",
    "image_paths_for_cutins",
    "image_region_from_config",
    "normalize_image_cutins",
    "probe_duration",
    "render_image_cutins",
]
