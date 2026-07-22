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


class ThreePhaseUnsupportedError(FootageStitchError):
    """Raised (internally) when a reel's transition windows cannot be isolated
    inside their own segments — the stitcher then falls back to the pairwise fold."""


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
    """Pure ffmpeg graph plus ordered media inputs (legacy single-graph path;
    retained as the reference math + for graph tests). Execution uses the pairwise
    plan below, which bounds peak memory to ~2 inputs regardless of clip count."""

    input_paths: tuple[Path, ...]
    filter_complex: str
    video_label: str
    audio_label: str
    duration_s: float


@dataclass(frozen=True)
class _NormStep:
    """Normalize one segment (source fragment or synthetic black) to a standalone,
    canvas-uniform mp4 — a single-input pass so memory stays bounded."""

    idx: int
    kind: str  # "source" | "black"
    input_path: Path | None
    trim_start_s: float
    trim_end_s: float
    duration_s: float
    pre_normalized: bool


@dataclass(frozen=True)
class _FoldStep:
    """Fold the accumulated reel with the next normalized segment via a 2-input
    transition (xfade / fade-to-color / concat) into a fresh intermediate mp4."""

    next_idx: int
    effect: str
    transition_duration_s: float
    current_duration_s: float  # accumulated duration of the reel BEFORE this fold
    audio_fade: bool
    result_duration_s: float


@dataclass(frozen=True)
class _PairwisePlan:
    norm_steps: tuple[_NormStep, ...]
    fold_steps: tuple[_FoldStep, ...]
    total_duration_s: float


@dataclass(frozen=True)
class _TransitionClipStep:
    """Render one transition in isolation from the two normalized neighbors'
    windows: xfade → a D-long clip, fade-to-color → fade-out(D) ⧺ fade-in(D) = 2D."""

    position: int  # 1-based transition index == right segment idx
    left_idx: int
    right_idx: int
    effect: str
    transition_duration_s: float
    left_tail_offset_s: float  # seek into the normalized left segment
    audio_fade: bool
    result_duration_s: float


@dataclass(frozen=True)
class _ConcatEntry:
    """One ordered part of the final concat-copy assembly."""

    kind: str  # "body" | "transition"
    index: int  # segment idx for bodies, transition position for transitions
    inpoint_s: float  # stream-copy cut into the normalized segment (body only)
    duration_s: float


@dataclass(frozen=True)
class _ThreePhasePlan:
    """normalize once → isolated transition clips → concat-demuxer -c copy.
    O(N) encode work and ≤2 encode generations per frame regardless of N."""

    norm_steps: tuple[_NormStep, ...]
    keyframe_times: tuple[tuple[float, ...], ...]  # per segment, for copy cuts
    transition_steps: tuple[_TransitionClipStep, ...]
    concat_entries: tuple[_ConcatEntry, ...]
    total_duration_s: float


# Bodies shorter than one frame cannot survive a stream-copy cut; skip them.
_MIN_CONCAT_PART_S = 1.0 / FPS


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
    """Render ``reel`` to an mp4 by normalizing each segment then folding transitions
    pairwise through intermediate files. Every ffmpeg pass opens ≤2 inputs, so peak
    memory is bounded regardless of clip count (the single-graph path OOM-killed the
    agent once the reel grew past ~16 segments)."""

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("footage stitch requires ffmpeg and ffprobe on PATH")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_3p: _ThreePhasePlan | None
    try:
        plan_3p = plan_three_phase_stitch(reel, segment_assets)
    except ThreePhaseUnsupportedError:
        plan_3p = None
    safe_run_id = _safe_run_id(run_id)
    work_dir = out_dir / f"{safe_run_id}-stitch"
    work_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"{safe_run_id}.mp4"

    try:
        if plan_3p is not None:
            result = await _run_three_phase(plan_3p, work_dir, timeout_s=timeout_s)
        else:
            result = await _run_pairwise_fold(
                plan_pairwise_stitch(reel, segment_assets), work_dir, timeout_s=timeout_s
            )
        shutil.move(str(result), str(final_path))
    except BaseException as exc:
        stderr = getattr(exc, "stderr", "")
        if stderr:
            (out_dir / f"{safe_run_id}.stderr.txt").write_text(stderr)
        final_path.unlink(missing_ok=True)
        raise

    return final_path


async def _run_three_phase(plan: _ThreePhasePlan, work_dir: Path, *, timeout_s: float) -> Path:
    """Execute the three-phase plan; every pass opens ≤2 inputs, bodies and the
    final assembly are stream copies (zero re-encode)."""

    norm_paths: list[Path] = []
    for step, keyframe_times in zip(plan.norm_steps, plan.keyframe_times):
        norm_path = work_dir / f"norm-{step.idx:03d}.mp4"
        await _run_ffmpeg(
            _normalize_cmd(step, norm_path, keyframe_times=keyframe_times),
            timeout_s=timeout_s,
        )
        norm_paths.append(norm_path)

    trans_paths: dict[int, Path] = {}
    for step in plan.transition_steps:
        trans_path = work_dir / f"trans-{step.position:03d}.mp4"
        cmd = _transition_clip_cmd(
            norm_paths[step.left_idx], norm_paths[step.right_idx], step, trans_path
        )
        await _run_ffmpeg(cmd, timeout_s=timeout_s)
        trans_paths[step.position] = trans_path

    parts: list[Path] = []
    for entry in plan.concat_entries:
        if entry.kind == "transition":
            parts.append(trans_paths[entry.index])
            continue
        norm_step = plan.norm_steps[entry.index]
        whole_segment = (
            entry.inpoint_s == 0.0
            and abs(entry.duration_s - norm_step.duration_s) < 1e-6
        )
        if whole_segment:
            parts.append(norm_paths[entry.index])
            continue
        body_path = work_dir / f"body-{entry.index:03d}.mp4"
        await _run_ffmpeg(
            _body_extract_cmd(norm_paths[entry.index], entry, body_path),
            timeout_s=timeout_s,
        )
        parts.append(body_path)

    list_path = work_dir / "concat.txt"
    list_path.write_text(_concat_list_text(parts))
    concat_path = work_dir / "concat.mp4"
    await _run_ffmpeg(_concat_cmd(list_path, concat_path), timeout_s=timeout_s)
    return concat_path


async def _run_pairwise_fold(plan: _PairwisePlan, work_dir: Path, *, timeout_s: float) -> Path:
    """Legacy pairwise fold: retained as the fallback for reels whose transition
    windows do not fit inside their own segments (O(N²) but always expressible)."""

    norm_paths: list[Path] = []
    for step in plan.norm_steps:
        norm_path = work_dir / f"norm-{step.idx:03d}.mp4"
        await _run_ffmpeg(_normalize_cmd(step, norm_path), timeout_s=timeout_s)
        norm_paths.append(norm_path)

    current = norm_paths[0]
    last = len(plan.fold_steps)
    for position, fold in enumerate(plan.fold_steps, start=1):
        clamp = plan.total_duration_s if position == last else None
        fold_path = work_dir / f"fold-{position:03d}.mp4"
        cmd = _fold_cmd(current, norm_paths[fold.next_idx], fold, fold_path, duration_clamp=clamp)
        await _run_ffmpeg(cmd, timeout_s=timeout_s)
        current = fold_path
    return current


def plan_pairwise_stitch(reel: FootageReel, segment_assets: SegmentAssetMap) -> _PairwisePlan:
    """Pure planner: validate the reel and derive the per-segment normalization steps
    and per-transition fold steps (durations, offsets, effects) — no I/O or subprocess.
    Mirrors ``build_footage_filtergraph``'s validation + duration math."""

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

    norm_steps: list[_NormStep] = []
    durations: list[float] = []
    for idx, segment in enumerate(segments):
        duration_s = _segment_duration(segment)
        durations.append(duration_s)
        if _kind(segment) == "black":
            norm_steps.append(
                _NormStep(idx, "black", None, 0.0, duration_s, duration_s, False)
            )
            continue
        asset = segment_assets[_segment_id(segment)]
        trim_start_s = max(
            0.0, _float_attr(segment, "start_s") - _float_attr(asset, "source_start_s", 0.0)
        )
        norm_steps.append(
            _NormStep(
                idx,
                "source",
                Path(_attr(asset, "path")),
                trim_start_s,
                trim_start_s + duration_s,
                duration_s,
                bool(_attr(asset, "pre_normalized", False)),
            )
        )

    fold_steps: list[_FoldStep] = []
    current_duration_s = durations[0]
    for idx, transition in enumerate(transitions, start=1):
        _validate_transition_indexes(transition, idx - 1, idx)
        next_duration_s = durations[idx]
        effect = _transition_effect(transition)
        transition_duration_s = _transition_duration(transition, effect)
        audio_fade = bool(_attr(transition, "audio_fade", True))

        if effect == "none" or transition_duration_s == 0:
            result_duration_s = current_duration_s + next_duration_s
        elif effect in FADE_TO_COLOR_EFFECTS:
            _validate_fade_to_color_duration(
                transition_duration_s, current_duration_s, next_duration_s, idx
            )
            result_duration_s = current_duration_s + next_duration_s
        elif effect in XFADE_EFFECTS:
            _validate_xfade_duration(
                transition_duration_s, current_duration_s, next_duration_s, idx
            )
            result_duration_s = current_duration_s + next_duration_s - transition_duration_s
        else:
            raise SegmentAssetValidationError(f"unsupported xfade effect: {effect}")

        fold_steps.append(
            _FoldStep(
                idx,
                effect,
                transition_duration_s,
                current_duration_s,
                audio_fade,
                result_duration_s,
            )
        )
        current_duration_s = result_duration_s

    expected_duration = _optional_float_attr(reel, "duration_s")
    if (
        expected_duration is not None
        and abs(expected_duration - current_duration_s) > FFPROBE_DURATION_EPSILON_S
    ):
        raise SegmentAssetValidationError(
            "reel duration does not match derived stitch duration: "
            f"reel={expected_duration:.3f}s derived={current_duration_s:.3f}s"
        )

    return _PairwisePlan(tuple(norm_steps), tuple(fold_steps), current_duration_s)


def plan_three_phase_stitch(reel: FootageReel, segment_assets: SegmentAssetMap) -> _ThreePhasePlan:
    """Pure planner for the three-phase stitch. Delegates validation and duration
    math to :func:`plan_pairwise_stitch`, then derives per-segment head/tail
    transition windows, isolated transition clips, and the concat-copy assembly.

    Raises :class:`ThreePhaseUnsupportedError` when a segment cannot host its
    windows (``head + tail > duration``) — e.g. an xfade wider than its own left
    segment, which the pairwise fold validates only against the *accumulated*
    reel. Callers fall back to the pairwise fold in that case.
    """

    pairwise = plan_pairwise_stitch(reel, segment_assets)
    norm_steps = pairwise.norm_steps
    seg_durs = [step.duration_s for step in norm_steps]
    head_w = [0.0] * len(norm_steps)
    tail_w = [0.0] * len(norm_steps)

    transition_steps: list[_TransitionClipStep] = []
    for fold in pairwise.fold_steps:
        duration = fold.transition_duration_s
        if fold.effect == "none" or duration == 0:
            continue
        right = fold.next_idx
        left = right - 1
        head_w[right] = duration
        tail_w[left] = duration
        result_duration_s = (
            2 * duration if fold.effect in FADE_TO_COLOR_EFFECTS else duration
        )
        transition_steps.append(
            _TransitionClipStep(
                position=right,
                left_idx=left,
                right_idx=right,
                effect=fold.effect,
                transition_duration_s=duration,
                left_tail_offset_s=seg_durs[left] - duration,
                audio_fade=fold.audio_fade,
                result_duration_s=result_duration_s,
            )
        )

    for idx, seg_dur in enumerate(seg_durs):
        if head_w[idx] + tail_w[idx] > seg_dur + 1e-9:
            raise ThreePhaseUnsupportedError(
                f"segment {idx}: transition windows "
                f"(head={head_w[idx]:.3f}s + tail={tail_w[idx]:.3f}s) exceed "
                f"segment duration {seg_dur:.3f}s"
            )

    trans_by_position = {step.position: step for step in transition_steps}
    concat_entries: list[_ConcatEntry] = []
    for idx, seg_dur in enumerate(seg_durs):
        step = trans_by_position.get(idx)
        if step is not None:
            concat_entries.append(
                _ConcatEntry("transition", idx, 0.0, step.result_duration_s)
            )
        body_duration_s = seg_dur - head_w[idx] - tail_w[idx]
        if body_duration_s >= _MIN_CONCAT_PART_S:
            concat_entries.append(
                _ConcatEntry("body", idx, head_w[idx], body_duration_s)
            )

    keyframe_times = tuple(
        (head_w[idx],) if head_w[idx] > 0 else () for idx in range(len(norm_steps))
    )
    return _ThreePhasePlan(
        norm_steps=norm_steps,
        keyframe_times=keyframe_times,
        transition_steps=tuple(transition_steps),
        concat_entries=tuple(concat_entries),
        total_duration_s=pairwise.total_duration_s,
    )


def _normalize_cmd(
    step: _NormStep, out_path: Path, *, keyframe_times: tuple[float, ...] = ()
) -> list[str]:
    """One-input (or synthetic) pass that renders a segment to a canvas-uniform mp4."""
    if step.kind == "black":
        filtergraph = (
            f"color=c=black:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={FPS}:"
            f"d={step.duration_s:.3f},format=yuv420p[v];"
            f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}:"
            f"d={step.duration_s:.3f},asetpts=PTS-STARTPTS[a]"
        )
        cmd = _base_ffmpeg(threads=2) + ["-filter_complex", filtergraph]
    else:
        video = _source_video_fragment(
            0, 0, step.trim_start_s, step.trim_end_s, pre_normalized=step.pre_normalized
        )
        audio = _source_audio_fragment(0, 0, step.trim_start_s, step.trim_end_s)
        cmd = _base_ffmpeg(threads=2) + [
            "-i",
            str(step.input_path),
            "-filter_complex",
            f"{video};{audio}",
        ]
    cmd += [
        "-map",
        "[v]" if step.kind == "black" else "[v0]",
        "-map",
        "[a]" if step.kind == "black" else "[a0]",
        *_video_encode_opts(),
    ]
    if keyframe_times:
        # Keyframes exactly where the three-phase body stream-copy cuts begin.
        cmd += ["-force_key_frames", ",".join(f"{t:.3f}" for t in keyframe_times)]
    cmd += [
        "-t",
        f"{step.duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return cmd


def _fold_filter(fold: _FoldStep) -> str:
    """The 2-input transition filtergraph (input 0 = accumulated reel, 1 = next)."""
    duration = fold.transition_duration_s
    if fold.effect == "none" or duration == 0:
        return "[0:v][1:v]concat=n=2:v=1:a=0[v];[0:a][1:a]concat=n=2:v=0:a=1[a]"

    if fold.effect in FADE_TO_COLOR_EFFECTS:
        color = "white" if fold.effect == "fadewhite" else "black"
        fade_start_s = max(0.0, fold.current_duration_s - duration)
        parts = [
            f"[0:v]fade=t=out:st={fade_start_s:.3f}:d={duration:.3f}:color={color}[lv]",
            f"[1:v]fade=t=in:st=0:d={duration:.3f}:color={color}[rv]",
            "[lv][rv]concat=n=2:v=1:a=0[v]",
        ]
        if fold.audio_fade:
            parts += [
                f"[0:a]afade=t=out:st={fade_start_s:.3f}:d={duration:.3f}[la]",
                f"[1:a]afade=t=in:st=0:d={duration:.3f}[ra]",
                "[la][ra]concat=n=2:v=0:a=1[a]",
            ]
        else:
            parts.append("[0:a][1:a]concat=n=2:v=0:a=1[a]")
        return ";".join(parts)

    # xfade
    offset_s = fold.current_duration_s - duration
    parts = [
        "[0:v]settb=AVTB[lv]",
        "[1:v]settb=AVTB[rv]",
        f"[lv][rv]xfade=transition={fold.effect}:duration={duration:.3f}:offset={offset_s:.3f}[v]",
    ]
    if fold.audio_fade:
        parts.append(f"[0:a][1:a]acrossfade=d={duration:.3f}:c1=tri:c2=tri[a]")
    else:
        parts += [
            f"[0:a]atrim=duration={offset_s:.3f},asetpts=PTS-STARTPTS[cuta]",
            "[cuta][1:a]concat=n=2:v=0:a=1[a]",
        ]
    return ";".join(parts)


def _fold_cmd(
    current_path: Path,
    next_path: Path,
    fold: _FoldStep,
    out_path: Path,
    *,
    duration_clamp: float | None,
) -> list[str]:
    cmd = _base_ffmpeg(threads=2) + [
        "-i",
        str(current_path),
        "-i",
        str(next_path),
        "-filter_complex",
        _fold_filter(fold),
        "-map",
        "[v]",
        "-map",
        "[a]",
        *_video_encode_opts(),
    ]
    if duration_clamp is not None:
        cmd += ["-t", f"{duration_clamp:.3f}"]
    cmd += ["-movflags", "+faststart", str(out_path)]
    return cmd


def _transition_clip_filter(step: _TransitionClipStep) -> str:
    """Filtergraph for one isolated transition window (input 0 = left tail
    window, already seeked; input 1 = right head window)."""

    duration = step.transition_duration_s
    # trim+setpts drops the CFR metadata xfade requires; fps restores it.
    left_v = f"[0:v]trim=end={duration:.3f},setpts=PTS-STARTPTS,fps={FPS}"
    right_v = f"[1:v]trim=end={duration:.3f},setpts=PTS-STARTPTS,fps={FPS}"
    left_a = f"[0:a]atrim=end={duration:.3f},asetpts=PTS-STARTPTS"
    right_a = f"[1:a]atrim=end={duration:.3f},asetpts=PTS-STARTPTS"

    if step.effect in FADE_TO_COLOR_EFFECTS:
        color = "white" if step.effect == "fadewhite" else "black"
        parts = [
            f"{left_v},fade=t=out:st=0.000:d={duration:.3f}:color={color}[lv]",
            f"{right_v},fade=t=in:st=0:d={duration:.3f}:color={color}[rv]",
            "[lv][rv]concat=n=2:v=1:a=0[v]",
        ]
        if step.audio_fade:
            parts += [
                f"{left_a},afade=t=out:st=0:d={duration:.3f}[la]",
                f"{right_a},afade=t=in:st=0:d={duration:.3f}[ra]",
                "[la][ra]concat=n=2:v=0:a=1[a]",
            ]
        else:
            parts += [
                f"{left_a}[la]",
                f"{right_a}[ra]",
                "[la][ra]concat=n=2:v=0:a=1[a]",
            ]
        return ";".join(parts)

    # xfade over fully-overlapped windows: offset 0, output duration = D
    parts = [
        f"{left_v},settb=AVTB[lv]",
        f"{right_v},settb=AVTB[rv]",
        f"[lv][rv]xfade=transition={step.effect}:duration={duration:.3f}:offset=0.000[v]",
    ]
    if step.audio_fade:
        parts += [
            f"{left_a}[la]",
            f"{right_a}[ra]",
            f"[la][ra]acrossfade=d={duration:.3f}:c1=tri:c2=tri[a]",
        ]
    else:
        # Pairwise hard cut: overall audio = left[0..L-D] + right[0..R].
        # The left body already carries audio to the cut, so the window's
        # audio is the right head alone.
        parts.append(f"{right_a}[a]")
    return ";".join(parts)


def _transition_clip_cmd(
    left_path: Path, right_path: Path, step: _TransitionClipStep, out_path: Path
) -> list[str]:
    """2-input pass that renders one transition window in isolation. Input seeks
    bound decode work to the windows themselves."""

    duration = step.transition_duration_s
    return (
        _base_ffmpeg(threads=2)
        + [
            "-ss",
            f"{step.left_tail_offset_s:.3f}",
            "-i",
            str(left_path),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(right_path),
            "-filter_complex",
            _transition_clip_filter(step),
            "-map",
            "[v]",
            "-map",
            "[a]",
            *_video_encode_opts(),
            "-t",
            f"{step.result_duration_s:.3f}",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )


def _body_extract_cmd(norm_path: Path, entry: _ConcatEntry, out_path: Path) -> list[str]:
    """Stream-copy a body span out of a normalized segment (keyframe-aligned
    start via -force_key_frames; end cuts are safe anywhere with bframes=0)."""

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if entry.inpoint_s > 0:
        cmd += ["-ss", f"{entry.inpoint_s:.3f}"]
    cmd += [
        "-i",
        str(norm_path),
        "-t",
        f"{entry.duration_s:.3f}",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return cmd


def _concat_list_text(paths: Sequence[Path]) -> str:
    """concat-demuxer list body; single quotes escaped the ffmpeg way."""

    lines = []
    for path in paths:
        escaped = str(path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def _concat_cmd(list_path: Path, out_path: Path) -> list[str]:
    """Zero-re-encode assembly of normalized bodies + transition clips."""

    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(out_path),
    ]


def _source_audio_fragment(input_idx: int, idx: int, trim_start_s: float, trim_end_s: float) -> str:
    """Per-source-segment audio filter fragment (trim + resample to canvas audio)."""
    return (
        f"[{input_idx}:a]atrim=start={trim_start_s:.3f}:end={trim_end_s:.3f},"
        "asetpts=PTS-STARTPTS,"
        f"aresample={AUDIO_SAMPLE_RATE},"
        f"aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo[a{idx}]"
    )


def _base_ffmpeg(threads: int) -> list[str]:
    """ffmpeg prefix that bounds per-thread buffer pools. Pairwise passes have
    ≤2 inputs, so a small fixed thread count stays memory-safe on a many-core box."""
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-threads",
        str(threads),
        "-filter_threads",
        str(threads),
        "-filter_complex_threads",
        str(threads),
    ]


def _video_encode_opts() -> list[str]:
    """Shared encoder options: trimmed x264 buffers + canvas audio. Closed GOP
    (-g/-keyint_min/-sc_threshold) keeps keyframe cadence deterministic so the
    three-phase assembly can stream-copy cut exactly."""
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-x264-params",
        "rc-lookahead=5:bframes=0:ref=1",
        "-g",
        "60",
        "-keyint_min",
        "60",
        "-sc_threshold",
        "0",
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
    ]


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
    # Bound peak memory on a multi-input filtergraph. On a many-core box ffmpeg
    # auto-threads (~1.5x cores) and every thread carries its own decoded-frame /
    # buffer pools; on a 9-input stitch that spike OOM-killed the agent (exit -9).
    # Single-threading kills the per-thread pools (the biggest knob), and a short
    # x264 lookahead with no B-frames / single ref removes the largest encoder
    # buffers. Filter-graph semantics are unchanged. (Durable fix = pairwise fold
    # with intermediate files; this bounds the current single-graph path.)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
    ]
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
        "-x264-params",
        "rc-lookahead=5:bframes=0:ref=1",
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
    "ThreePhaseUnsupportedError",
    "build_footage_filtergraph",
    "download_segments",
    "plan_three_phase_stitch",
    "stitch_footage_reel",
    "validate_segment_assets",
]
