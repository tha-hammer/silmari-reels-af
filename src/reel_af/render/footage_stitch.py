"""Real-footage stitcher for composite DSL reels.

The generated-reel renderer in :mod:`reel_af.render.stitch` works on Veo clips
plus generated TTS. This module is the parallel real-footage path: source media
segments, synthetic black spans, and ffmpeg transitions.
"""

from __future__ import annotations

import asyncio
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from reel_af.dsl.models import (
    AUDIO_SAMPLE_RATE,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DOWNLOAD_TIMEOUT_S,
    FADE_TO_COLOR_EFFECTS,
    FFMPEG_TIMEOUT_S,
    FFPROBE_DURATION_EPSILON_S,
    FPS,
    MAX_FILTER_GRAPH_CHARS,
    DownloadedSegment,
    FootageReel,
    SegmentAssetMap,
    SegmentFetchRequest,
    validate_renderable,
)

XFADE_EFFECTS = frozenset(
    {
        "dissolve",
        "smoothleft",
        "smoothright",
        "smoothup",
        "smoothdown",
        "hblur",
        "circleopen",
        "radial",
        "pixelize",
    }
)
class FootageStitchError(RuntimeError):
    """Base error for real-footage stitching failures."""


class MissingSegmentAssetError(FootageStitchError):
    """Raised when a source segment has no downloaded media asset."""


class SegmentAssetValidationError(FootageStitchError):
    """Raised when reel segments/assets cannot be stitched."""


class FFmpegProcessError(FootageStitchError):
    """Raised when ffmpeg exits nonzero."""

    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str) -> None:
        self.cmd = tuple(cmd)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            "footage stitch ffmpeg failed "
            f"(exit {returncode}):\n"
            f"  cmd: {' '.join(shlex.quote(str(part)) for part in cmd)}\n"
            f"  stderr: {stderr[-1600:]}"
        )


@dataclass(frozen=True)
class FootageFilterGraph:
    """Pure ffmpeg graph plus ordered media inputs."""

    input_paths: tuple[Path, ...]
    filter_complex: str
    video_label: str
    audio_label: str
    duration_s: float


SegmentFetchFn = Callable[[SegmentFetchRequest], DownloadedSegment | Path | str]


def download_segments(
    reel: FootageReel,
    out_dir: Path,
    fetch: SegmentFetchFn,
    *,
    timeout_s: float = DOWNLOAD_TIMEOUT_S,
) -> dict[str, DownloadedSegment]:
    """Download every source segment through the injected fetch contract.

    Black segments intentionally do not request assets.
    """

    _ = timeout_s  # The v1 fetch contract is synchronous; callers own timeouts.
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    assets: dict[str, DownloadedSegment] = {}
    seen: set[str] = set()
    for segment in _source_segments(reel):
        segment_id = _segment_id(segment)
        if segment_id in seen:
            raise SegmentAssetValidationError(f"duplicate source segment id: {segment_id}")
        seen.add(segment_id)
        request = _make_fetch_request(
            segment_id=segment_id,
            source_url=_segment_source_url(segment, reel),
            start_s=_float_attr(segment, "start_s"),
            end_s=_float_attr(segment, "end_s"),
            target_path=out_dir / f"{segment_id}.mp4",
        )
        downloaded = fetch(request)
        assets[segment_id] = _coerce_downloaded_segment(downloaded, segment)

    validate_segment_assets(reel, assets)
    return assets


def validate_segment_assets(reel: FootageReel, segment_assets: SegmentAssetMap) -> None:
    """Validate that every source segment has an existing media asset."""

    missing: list[str] = []
    seen: set[str] = set()
    for segment in _source_segments(reel):
        segment_id = _segment_id(segment)
        if segment_id in seen:
            raise SegmentAssetValidationError(f"duplicate source segment id: {segment_id}")
        seen.add(segment_id)
        asset = segment_assets.get(segment_id)
        if asset is None:
            missing.append(segment_id)
            continue
        path = Path(_attr(asset, "path"))
        if not path.exists():
            raise MissingSegmentAssetError(
                f"MISSING_SEGMENT_ASSET: asset path for {segment_id} does not exist: {path}"
            )
    if missing:
        raise MissingSegmentAssetError(
            "MISSING_SEGMENT_ASSET: missing asset(s) for source segment id(s): "
            + ", ".join(missing)
        )


def build_footage_filtergraph(reel: FootageReel, segment_assets: SegmentAssetMap) -> FootageFilterGraph:
    """Build the ffmpeg filtergraph without filesystem or subprocess effects."""

    validate_renderable(reel)
    validate_segment_assets(reel, segment_assets)
    segments = list(_segments(reel))
    if not segments:
        raise SegmentAssetValidationError("reel has no segments")

    transitions = list(_transitions(reel))
    expected_transition_count = max(0, len(segments) - 1)
    if len(transitions) != expected_transition_count:
        raise SegmentAssetValidationError(
            "transition count mismatch: "
            f"expected {expected_transition_count}, got {len(transitions)}"
        )

    filters: list[str] = []
    input_paths: list[Path] = []
    durations: list[float] = []

    for idx, segment in enumerate(segments):
        duration_s = _segment_duration(segment)
        durations.append(duration_s)
        if _kind(segment) == "black":
            filters.append(
                f"color=c=black:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={FPS}:"
                f"d={duration_s:.3f}[v{idx}]"
            )
            filters.append(
                f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}:"
                f"d={duration_s:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            continue

        segment_id = _segment_id(segment)
        asset = segment_assets[segment_id]
        input_idx = len(input_paths)
        input_paths.append(Path(_attr(asset, "path")))

        trim_start_s = max(0.0, _float_attr(segment, "start_s") - _float_attr(asset, "source_start_s", 0.0))
        trim_end_s = trim_start_s + duration_s
        pre_normalized = bool(_attr(asset, "pre_normalized", False))
        filters.append(
            _source_video_fragment(
                input_idx,
                idx,
                trim_start_s,
                trim_end_s,
                pre_normalized=pre_normalized,
            )
        )
        filters.append(
            f"[{input_idx}:a]atrim=start={trim_start_s:.3f}:end={trim_end_s:.3f},"
            "asetpts=PTS-STARTPTS,"
            f"aresample={AUDIO_SAMPLE_RATE},"
            f"aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo[a{idx}]"
        )

    current_v = "v0"
    current_a = "a0"
    current_duration_s = durations[0]

    for idx, transition in enumerate(transitions, start=1):
        _validate_transition_indexes(transition, idx - 1, idx)
        next_v = f"v{idx}"
        next_a = f"a{idx}"
        next_duration_s = durations[idx]
        effect = _transition_effect(transition)
        transition_duration_s = _transition_duration(transition, effect)
        audio_fade = bool(_attr(transition, "audio_fade", True))

        if effect == "none" or transition_duration_s == 0:
            out_v = f"vc{idx}"
            out_a = f"ac{idx}"
            filters.append(f"[{current_v}][{next_v}]concat=n=2:v=1:a=0[{out_v}]")
            filters.append(f"[{current_a}][{next_a}]concat=n=2:v=0:a=1[{out_a}]")
            current_v = out_v
            current_a = out_a
            current_duration_s += next_duration_s
            continue

        if effect in FADE_TO_COLOR_EFFECTS:
            _validate_fade_to_color_duration(
                transition_duration_s,
                current_duration_s,
                next_duration_s,
                idx,
            )
            color = "white" if effect == "fadewhite" else "black"
            out_v = f"vf{idx}"
            out_a = f"af{idx}"
            left_v = f"vf{idx}l"
            right_v = f"vf{idx}r"
            fade_start_s = max(0.0, current_duration_s - transition_duration_s)
            filters.append(
                f"[{current_v}]fade=t=out:st={fade_start_s:.3f}:"
                f"d={transition_duration_s:.3f}:color={color}[{left_v}]"
            )
            filters.append(
                f"[{next_v}]fade=t=in:st=0:d={transition_duration_s:.3f}:"
                f"color={color}[{right_v}]"
            )
            filters.append(f"[{left_v}][{right_v}]concat=n=2:v=1:a=0[{out_v}]")
            if audio_fade:
                left_a = f"af{idx}l"
                right_a = f"af{idx}r"
                filters.append(
                    f"[{current_a}]afade=t=out:st={fade_start_s:.3f}:"
                    f"d={transition_duration_s:.3f}[{left_a}]"
                )
                filters.append(f"[{next_a}]afade=t=in:st=0:d={transition_duration_s:.3f}[{right_a}]")
                filters.append(f"[{left_a}][{right_a}]concat=n=2:v=0:a=1[{out_a}]")
            else:
                filters.append(f"[{current_a}][{next_a}]concat=n=2:v=0:a=1[{out_a}]")
            current_v = out_v
            current_a = out_a
            current_duration_s += next_duration_s
            continue

        if effect not in XFADE_EFFECTS:
            raise SegmentAssetValidationError(f"unsupported xfade effect: {effect}")

        _validate_xfade_duration(transition_duration_s, current_duration_s, next_duration_s, idx)
        offset_s = current_duration_s - transition_duration_s
        out_v = f"vx{idx}"
        out_a = f"ax{idx}"
        left_v = f"vx{idx}ltb"
        right_v = f"vx{idx}rtb"
        filters.append(f"[{current_v}]settb=AVTB[{left_v}]")
        filters.append(f"[{next_v}]settb=AVTB[{right_v}]")
        filters.append(
            f"[{left_v}][{right_v}]xfade=transition={effect}:"
            f"duration={transition_duration_s:.3f}:offset={offset_s:.3f}[{out_v}]"
        )
        if audio_fade:
            filters.append(
                f"[{current_a}][{next_a}]acrossfade=d={transition_duration_s:.3f}:"
                f"c1=tri:c2=tri[{out_a}]"
            )
        else:
            cut_a = f"ax{idx}cut"
            filters.append(f"[{current_a}]atrim=duration={offset_s:.3f},asetpts=PTS-STARTPTS[{cut_a}]")
            filters.append(f"[{cut_a}][{next_a}]concat=n=2:v=0:a=1[{out_a}]")
        current_v = out_v
        current_a = out_a
        current_duration_s += next_duration_s - transition_duration_s

    filter_complex = ";".join(filters)
    if len(filter_complex) > MAX_FILTER_GRAPH_CHARS:
        raise SegmentAssetValidationError(
            "filter graph too large: "
            f"{len(filter_complex)} chars > {MAX_FILTER_GRAPH_CHARS}"
        )

    expected_duration = _optional_float_attr(reel, "duration_s")
    if expected_duration is not None and abs(expected_duration - current_duration_s) > FFPROBE_DURATION_EPSILON_S:
        raise SegmentAssetValidationError(
            "reel duration does not match derived stitch duration: "
            f"reel={expected_duration:.3f}s derived={current_duration_s:.3f}s"
        )

    return FootageFilterGraph(
        input_paths=tuple(input_paths),
        filter_complex=filter_complex,
        video_label=f"[{current_v}]",
        audio_label=f"[{current_a}]",
        duration_s=current_duration_s,
    )


async def stitch_footage_reel(
    reel: FootageReel,
    segment_assets: SegmentAssetMap,
    out_dir: Path,
    run_id: str,
    *,
    timeout_s: float = FFMPEG_TIMEOUT_S,
) -> Path:
    """Render ``reel`` to an mp4 using ffmpeg."""

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("footage stitch requires ffmpeg and ffprobe on PATH")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    graph = build_footage_filtergraph(reel, segment_assets)
    final_path = out_dir / f"{_safe_run_id(run_id)}.mp4"
    cmd = _ffmpeg_cmd(graph, final_path)

    try:
        await _run_ffmpeg(cmd, timeout_s=timeout_s)
    except BaseException as exc:
        (out_dir / f"{_safe_run_id(run_id)}.filtergraph.txt").write_text(graph.filter_complex)
        stderr = getattr(exc, "stderr", "")
        if stderr:
            (out_dir / f"{_safe_run_id(run_id)}.stderr.txt").write_text(stderr)
        final_path.unlink(missing_ok=True)
        raise

    return final_path


def _source_video_fragment(
    input_idx: int,
    idx: int,
    trim_start_s: float,
    trim_end_s: float,
    *,
    pre_normalized: bool,
) -> str:
    """Build the per-source-segment video filter fragment.

    ``pre_normalized`` inputs (already 1080×1920 from the overlay pass) keep
    timing, SAR, fps, and pixel format but skip a second spatial ``scale``/
    ``crop`` so the canvas is spatially normalized exactly once.
    """

    spatial = (
        ""
        if pre_normalized
        else (
            f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT},"
        )
    )
    return (
        f"[{input_idx}:v]trim=start={trim_start_s:.3f}:end={trim_end_s:.3f},"
        "setpts=PTS-STARTPTS,"
        f"{spatial}setsar=1,fps={FPS},format=yuv420p[v{idx}]"
    )


def _ffmpeg_cmd(graph: FootageFilterGraph, final_path: Path) -> list[str]:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for input_path in graph.input_paths:
        cmd += ["-i", str(input_path)]
    cmd += [
        "-filter_complex",
        graph.filter_complex,
        "-map",
        graph.video_label,
        "-map",
        graph.audio_label,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-t",
        f"{graph.duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(final_path),
    ]
    return cmd


async def _run_ffmpeg(cmd: Sequence[str], *, timeout_s: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(f"footage stitch ffmpeg timed out after {timeout_s:.1f}s") from None
    except BaseException:
        proc.kill()
        await proc.communicate()
        raise

    stderr = stderr_bytes.decode(errors="replace")
    if proc.returncode != 0:
        raise FFmpegProcessError(cmd, proc.returncode or -1, stderr)


def _make_fetch_request(
    *,
    segment_id: str,
    source_url: str,
    start_s: float,
    end_s: float,
    target_path: Path,
) -> SegmentFetchRequest:
    return SegmentFetchRequest(
        segment_id=segment_id,
        source_url=source_url,
        start_s=start_s,
        end_s=end_s,
        target_path=target_path,
    )


def _coerce_downloaded_segment(downloaded: Any, segment: Any) -> DownloadedSegment:
    if isinstance(downloaded, (str, Path)):
        return _make_downloaded_segment(
            segment_id=_segment_id(segment),
            path=Path(downloaded),
            source_start_s=_float_attr(segment, "start_s"),
            source_end_s=_float_attr(segment, "end_s"),
        )
    _ = Path(_attr(downloaded, "path"))
    return downloaded


def _make_downloaded_segment(
    *,
    segment_id: str,
    path: Path,
    source_start_s: float,
    source_end_s: float,
) -> DownloadedSegment:
    return DownloadedSegment(
        segment_id=segment_id,
        path=path,
        source_start_s=source_start_s,
        source_end_s=source_end_s,
    )


def _segments(reel: Any) -> Sequence[Any]:
    return list(_attr(reel, "segments"))


def _source_segments(reel: Any) -> list[Any]:
    return [segment for segment in _segments(reel) if _kind(segment) == "source"]


def _transitions(reel: Any) -> Sequence[Any]:
    return list(_attr(reel, "transitions", ()))


def _segment_duration(segment: Any) -> float:
    if _kind(segment) == "black":
        return _float_attr(segment, "duration_s")
    duration_s = _float_attr(segment, "end_s") - _float_attr(segment, "start_s")
    if duration_s <= 0:
        raise SegmentAssetValidationError(f"source segment has non-positive duration: {_segment_id(segment)}")
    return duration_s


def _transition_effect(transition: Any) -> str:
    return str(_attr(transition, "effect", "none"))


def _transition_duration(transition: Any, effect: str) -> float:
    if effect == "none":
        return 0.0
    return _float_attr(transition, "duration_s", 1.0)


def _validate_transition_indexes(transition: Any, before_index: int, after_index: int) -> None:
    actual_before = int(_attr(transition, "before_index", before_index))
    actual_after = int(_attr(transition, "after_index", after_index))
    if (actual_before, actual_after) != (before_index, after_index):
        raise SegmentAssetValidationError(
            "transition indexes are not adjacent/order-preserving: "
            f"expected ({before_index}, {after_index}), got ({actual_before}, {actual_after})"
        )


def _validate_xfade_duration(duration_s: float, left_s: float, right_s: float, index: int) -> None:
    if duration_s <= 0 or duration_s >= min(left_s, right_s):
        raise SegmentAssetValidationError(
            f"transition {index} duration {duration_s:.3f}s must be >0 and "
            f"< min(left={left_s:.3f}, right={right_s:.3f}) for xfade"
        )


def _validate_fade_to_color_duration(duration_s: float, left_s: float, right_s: float, index: int) -> None:
    if duration_s <= 0 or duration_s > min(left_s, right_s):
        raise SegmentAssetValidationError(
            f"transition {index} duration {duration_s:.3f}s must be >0 and "
            f"<= min(left={left_s:.3f}, right={right_s:.3f}) for fade-to-color"
        )


def _segment_id(segment: Any) -> str:
    return str(_attr(segment, "segment_id"))


def _segment_source_url(segment: Any, reel: Any) -> str:
    return str(_attr(segment, "source_url", _attr(reel, "source_url", "")))


def _kind(value: Any) -> str:
    return str(_attr(value, "kind"))


def _optional_float_attr(value: Any, name: str) -> float | None:
    try:
        return float(_attr(value, name))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _float_attr(value: Any, name: str, default: float | None = None) -> float:
    raw = _attr(value, name, default)
    if raw is None:
        raise AttributeError(name)
    return float(raw)


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        if default is not None:
            return default
        raise KeyError(name)
    if hasattr(value, name):
        return getattr(value, name)
    if default is not None:
        return default
    raise AttributeError(name)


def _safe_run_id(run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in run_id.strip())
    return safe or "footage-reel"


__all__ = [
    "DownloadedSegment",
    "FFmpegProcessError",
    "FootageFilterGraph",
    "FootageStitchError",
    "MissingSegmentAssetError",
    "SegmentAssetMap",
    "SegmentAssetValidationError",
    "SegmentFetchFn",
    "SegmentFetchRequest",
    "build_footage_filtergraph",
    "download_segments",
    "stitch_footage_reel",
    "validate_segment_assets",
]
