"""Per-shot Veo i2v with first-frame and ken-burns fallbacks (parallel fan-out)."""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from agentfield.media_providers import OpenRouterProvider
from PIL import Image, ImageDraw

# Ensure SDK bug-fixes are applied before any SDK call. The Veo download-
# auth and data-URL save patches landed in agentfield main (PR #600); the
# in-pipeline mirror at src/reel_af/sdk_patches.py is kept until the SDK
# upgrade is pinned in pyproject.toml. Importing it is a no-op once the
# pinned SDK has the fixes — leaving it in is belt-and-suspenders.
import reel_af.sdk_patches  # noqa: F401
from reel_af.v2.models import MotionHint, Shot, ShotArtifact, ShotVisual
from reel_af.v2.render.images import _style_note, gen_first_frame_v2

VIDEO_MODEL = os.getenv(
    "REEL_AF_VIDEO_MODEL", "openrouter/google/veo-3.1-lite"
)


def _image_to_data_url(path: Path) -> str:
    """Encode a local JPEG/PNG as a data: URL for Veo first_frame input."""
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    return (
        f"data:image/{suffix};base64,"
        f"{base64.b64encode(path.read_bytes()).decode()}"
    )


def _motion_clause(hint: MotionHint) -> str:
    """Map ShotVisual.motion_hint to a free-text Veo prompt clause.

    Veo takes a single prompt string — there's no separate motion field —
    so we append a "Camera: ..." sentence. `static` gets the explicit
    "no movement" phrasing so Veo doesn't drift.
    """
    if hint == "static":
        return "Camera: static, no movement"
    # Normalize the enum value (e.g. "slow_zoom_in" -> "slow zoom in").
    return f"Camera: {hint.replace('_', ' ')}"


async def _gen_video(
    provider: OpenRouterProvider,
    visual: ShotVisual,
    shot: Shot,
    first_frame: Path,
    out_dir: Path,
    content_mode: str = "general",
) -> Path:
    """Veo image-to-video. Uses the first frame as the starting still.

    Veo duration is taken directly from `shot.veo_duration` (one of
    {4, 6, 8}) — the shot planner has already done the audio-vs-bucket
    math, so we just consume it.
    """
    frame_url = _image_to_data_url(first_frame)
    style = _style_note(content_mode)
    motion = _motion_clause(visual.motion_hint)
    # Compose Veo prompt: the literal scene is set by first_frame; the
    # motion clause says what should HAPPEN. We also tell Veo not to add
    # on-screen text (it sometimes does).
    veo_prompt = (
        f"{visual.image_prompt}. {motion}. {style}. "
        f"Do not add any text, captions, or letters to the frame."
    )

    result = await provider.generate_video(
        prompt=veo_prompt,
        model=VIDEO_MODEL,
        duration=shot.veo_duration,
        aspect_ratio="9:16",
        resolution="720p",
        # frame_images with frame_type=first_frame anchors i2v properly.
        frame_images=[
            {
                "type": "image_url",
                "image_url": {"url": frame_url},
                "frame_type": "first_frame",
            }
        ],
        # Generous timeout — Veo Lite takes ~30-90s end-to-end.
        poll_interval=8.0,
        timeout=420.0,
    )
    if not result.videos:
        raise RuntimeError(
            f"v2.render.video: Veo returned no video for shot {shot.idx}"
        )
    out = out_dir / f"seg-{shot.idx:02d}.mp4"
    result.videos[0].save(str(out))
    return out


async def _still_as_video(
    still_path: Path, duration: float, out_path: Path
) -> Path:
    """Render `still_path` as an `duration`s MP4 with a slow ken-burns
    zoom so the scene still has motion. Used as the second-tier fallback
    when Veo fails or moderation rejects the request.
    """
    safe = max(duration, 0.1)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-t", f"{duration:.3f}",
        "-i", str(still_path),
        "-vf",
        f"scale=1280:2280:force_original_aspect_ratio=increase,"
        f"crop=1280:2280,"
        f"crop=720:1280:"
        f"x='(100 - 100*t/{safe:.3f})':"
        f"y='(500 - 500*t/{safe:.3f})',"
        f"fps=30,format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "21",
        "-r", "30",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"v2.render.video ken-burns ffmpeg failed: "
            f"{err.decode(errors='replace')[-400:]}"
        )
    return out_path


def _placeholder_frame(idx: int, out_dir: Path) -> Path:
    """Last-resort 720x1280 dark gradient JPEG used when image gen fails
    every retry. We still want a valid first frame so the pipeline can
    continue and the user gets a complete reel rather than a hard fail.
    """
    out = out_dir / f"seg-{idx:02d}-frame-placeholder.jpg"
    w, h = 720, 1280
    img = Image.new("RGB", (w, h), (12, 12, 16))
    draw = ImageDraw.Draw(img)
    for step in range(0, 60, 2):
        shade = 18 + step // 2
        draw.rectangle(
            [step * 8, step * 14, w - step * 8, h - step * 14],
            outline=(shade, shade, shade + 4),
        )
    img.save(out, "JPEG", quality=90)
    return out


async def _gen_shot(
    provider: OpenRouterProvider,
    shot: Shot,
    visual: ShotVisual,
    out_dir: Path,
    audio_durations: dict[int, float] | None,
    content_mode: str = "general",
) -> ShotArtifact:
    """Generate first-frame + video for one shot, with two fallbacks.

    Fallback ladder (same as v1):
      1. Image gen fails after retries  -> placeholder still + ken-burns.
                                           first_frame_path = placeholder.
      2. Image gen OK, Veo fails        -> real first frame + ken-burns.
                                           first_frame_path = real frame.

    Ken-burns fallback duration is sized off the shot's actual audio
    duration (+0.5s tail) when known so timing tracks the voice. Falls
    back to shot.duration_s + 0.5 if no audio map is supplied.
    """
    audio_dur = (audio_durations or {}).get(shot.idx)
    fb_dur = (audio_dur + 0.5) if audio_dur else (shot.duration_s + 0.5)

    # Tier 1: first frame.
    try:
        frame = await gen_first_frame_v2(
            provider, visual, shot, out_dir, content_mode=content_mode,
        )
    except Exception as e:  # noqa: BLE001
        print(
            f"[v2.render.video] shot {shot.idx} image gen failed after "
            f"retries ({e}); using placeholder frame."
        )
        frame = _placeholder_frame(shot.idx, out_dir)
        fallback = out_dir / f"seg-{shot.idx:02d}-fallback.mp4"
        video = await _still_as_video(frame, duration=fb_dur, out_path=fallback)
        return ShotArtifact(
            idx=shot.idx, first_frame_path=frame, video_path=video,
        )

    # Tier 2: Veo i2v, with ken-burns fallback on failure.
    try:
        video = await _gen_video(
            provider, visual, shot, frame, out_dir,
            content_mode=content_mode,
        )
    except Exception as e:  # noqa: BLE001
        print(
            f"[v2.render.video] shot {shot.idx} Veo failed ({e}); falling "
            f"back to still + ken-burns."
        )
        fallback = out_dir / f"seg-{shot.idx:02d}-fallback.mp4"
        video = await _still_as_video(frame, duration=fb_dur, out_path=fallback)

    return ShotArtifact(
        idx=shot.idx, first_frame_path=frame, video_path=video,
    )


async def generate_videos(
    shots: list[Shot],
    visuals: list[ShotVisual],
    out_dir: Path,
    audio_durations: dict[int, float] | None = None,
    content_mode: str = "general",
) -> list[ShotArtifact]:
    """Fan out per-shot first-frame + Veo generation in parallel.

    Each shot runs the two-tier fallback ladder in `_gen_shot`. All
    shots execute concurrently via `asyncio.gather(return_exceptions=True)`
    so a single bad shot doesn't bring down the rest; we surface the
    aggregated error count + first exception once the wave completes.

    audio_durations: {shot_idx: seconds} — real per-shot audio durations
    used to size the ken-burns fallback so timing tracks the voice.
    """
    if len(shots) != len(visuals):
        raise ValueError(
            f"v2.render.video: shots ({len(shots)}) and visuals "
            f"({len(visuals)}) length mismatch"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    provider = OpenRouterProvider()
    results = await asyncio.gather(
        *(
            _gen_shot(
                provider, shot, visual, out_dir,
                audio_durations=audio_durations,
                content_mode=content_mode,
            )
            for shot, visual in zip(shots, visuals)
        ),
        return_exceptions=True,
    )
    errs = [r for r in results if isinstance(r, Exception)]
    if errs:
        raise RuntimeError(
            f"v2.render.video: {len(errs)}/{len(shots)} shots failed. "
            f"First error: {errs[0]}"
        )
    return [r for r in results if isinstance(r, ShotArtifact)]
