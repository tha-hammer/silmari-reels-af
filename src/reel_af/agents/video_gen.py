"""Video generation — Gemini image first frame → Veo image-to-video.

For each shot:
  1. Generate a vertical first frame with Gemini flash image (fast, cheap).
  2. Feed it to Veo 3.1 Lite as image_url + first_frame, plus the motion
     prompt from the shot director. Veo produces a 4-second 720×1280 MP4
     with the requested motion starting from that frame.

This gives us per-shot motion AND visual consistency across shots (because
each shot has its own coherent first-frame). The first-frame pass is the
trust anchor; Veo just animates it.

Models are env-overridable so we can swap when providers churn:
  REEL_AF_IMAGE_MODEL  — image-gen model (default: gemini-2.5-flash-image)
  REEL_AF_VIDEO_MODEL  — i2v model       (default: veo-3.1-lite)

Cost: ~$0.32/Veo + ~$0.002/image = ~$0.32 per shot. 7 shots = ~$2.25/reel.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from agentfield.media_providers import OpenRouterProvider

# Ensure SDK bug-fixes are applied before any SDK call (data-URL save +
# Veo download auth). See AGENTFIELD_SDK_ISSUES.md and src/reel_af/sdk_patches.py.
import reel_af.sdk_patches  # noqa: F401

from reel_af.agents.scene_breaker import Scene
from reel_af.agents.shot_director_v2 import ShotPlanV2
from reel_af.models import BeatArtifact

IMAGE_MODEL = os.getenv(
    "REEL_AF_IMAGE_MODEL", "openrouter/google/gemini-2.5-flash-image"
)
VIDEO_MODEL = os.getenv(
    "REEL_AF_VIDEO_MODEL", "openrouter/google/veo-3.1-lite"
)

# Veo accepts duration 4 / 6 / 8 seconds. We clamp segment durations to the
# nearest accepted value, slightly OVER the spoken duration so the video has
# a tail; ffmpeg will trim to the actual audio length at assembly.
_VEO_DURATIONS = (4, 6, 8)

# Global style block appended to every image prompt so all shots feel like
# the same reel. Editable in one place.
STYLE_NOTE = (
    "cinematic documentary still, warm natural light, shallow depth of field, "
    "35mm film grain, VERTICAL portrait composition (taller than wide, the "
    "subject occupies the upper-middle two-thirds), fills the frame, no text "
    "or letters"
)


def _veo_duration(est_s: float) -> int:
    """Pick the smallest accepted Veo duration ≥ est_s, capped at 8s."""
    for d in _VEO_DURATIONS:
        if d >= est_s:
            return d
    return _VEO_DURATIONS[-1]


def _augment(prompt: str) -> str:
    """Append the global style block to an image prompt."""
    base = prompt.strip().rstrip(".")
    return f"{base}. {STYLE_NOTE}."


def _image_to_data_url(path: Path) -> str:
    """Encode a local JPEG/PNG as a data: URL so Veo can use it as first_frame."""
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    return f"data:image/{suffix};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _save_image_output(img, dest: Path) -> None:
    """Save an SDK ImageOutput to disk.

    The SDK's stock ImageOutput.save() can't handle `data:image/...` URLs
    (calls requests.get on them, which 500s). The fix is applied by
    sdk_patches at import time, so we can just call the SDK's save().
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest))


def _crop_to_9x16(src: Path, dest: Path, target_w: int = 720) -> Path:
    """Center-crop the still to 9:16 vertical for Veo i2v input.

    Gemini returns square (1024×1024); Veo expects vertical 9:16. Take a
    centered 9:16 strip and resize to 720×1280 (Veo's native res).
    """
    from PIL import Image
    target_h = target_w * 16 // 9
    img = Image.open(src).convert("RGB")
    w, h = img.size
    desired_ratio = 9 / 16  # width / height
    cur_ratio = w / h
    if cur_ratio > desired_ratio:
        # Too wide — crop width.
        new_w = int(h * desired_ratio)
        x0 = (w - new_w) // 2
        img = img.crop((x0, 0, x0 + new_w, h))
    elif cur_ratio < desired_ratio:
        # Too tall — crop height.
        new_h = int(w / desired_ratio)
        y0 = (h - new_h) // 2
        img = img.crop((0, y0, w, y0 + new_h))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    img.save(dest, "JPEG", quality=92)
    return dest


async def _gen_first_frame(
    provider: OpenRouterProvider,
    plan: ShotPlanV2,
    idx: int,
    out_dir: Path,
    max_retries: int = 4,
) -> Path:
    """Generate a vertical 9:16 first frame from the shot plan's image prompt.

    OpenRouter routes the Gemini image model across multiple upstream
    providers; some 404 with 'No endpoints found that support the requested
    output modalities: image, text' when the routing lands on a non-image
    capable replica. We retry with exponential backoff to ride out those
    routing blips before bubbling up.

    image_config={"aspect_ratio": "9:16"} caused OpenRouter to 404 every
    request (no provider exposes the param today). We compose for vertical
    via the prompt and post-process to 9:16 ourselves.
    """
    prompt = _augment(plan.image_prompt)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            result = await provider.generate_image(
                prompt=prompt,
                model=IMAGE_MODEL,
            )
        except Exception as exc:  # noqa: BLE001 — provider raises bare Exceptions
            last_err = exc
            print(
                f"[video_gen] shot {idx} attempt {attempt + 1}/{max_retries} "
                f"image gen failed ({type(exc).__name__}: {str(exc)[:160]}); "
                f"retrying after backoff."
            )
            await asyncio.sleep(2 ** attempt)
            continue
        if not result.images:
            last_err = RuntimeError("model returned no images")
            await asyncio.sleep(2 ** attempt)
            continue
        raw = out_dir / f"seg-{idx:02d}-frame-raw.png"
        out = out_dir / f"seg-{idx:02d}-frame.jpg"
        _save_image_output(result.images[0], raw)
        _crop_to_9x16(raw, out)
        return out
    raise RuntimeError(
        f"video_gen: image model {IMAGE_MODEL} failed {max_retries} times "
        f"for shot {idx}. Last error: {last_err}"
    )


async def _gen_video(
    provider: OpenRouterProvider,
    plan: ShotPlanV2,
    seg: Scene,
    first_frame: Path,
    out_dir: Path,
) -> Path:
    """Veo image-to-video. Uses the grok-imagine still as the starting frame."""
    frame_url = _image_to_data_url(first_frame)
    duration = _veo_duration(seg.est_duration_s)
    # Compose Veo prompt: the literal scene is set by first_frame; the motion
    # prompt is what should HAPPEN. We also pass the on-screen-text context
    # so Veo doesn't try to ALSO put text in the video (it sometimes does).
    veo_prompt = (
        f"{plan.motion_prompt}. {STYLE_NOTE}. "
        f"Do not add any text, captions, or letters to the frame."
    )

    result = await provider.generate_video(
        prompt=veo_prompt,
        model=VIDEO_MODEL,
        duration=duration,
        aspect_ratio="9:16",
        resolution="720p",
        # Use frame_images with frame_type=first_frame for proper i2v anchoring.
        frame_images=[
            {"type": "image_url", "image_url": {"url": frame_url}, "frame_type": "first_frame"}
        ],
        # Generous timeout — Veo Lite takes ~30-90s end-to-end.
        poll_interval=8.0,
        timeout=420.0,
    )
    if not result.videos:
        raise RuntimeError(f"video_gen: Veo returned no video for shot {seg.idx}")
    out = out_dir / f"seg-{seg.idx:02d}.mp4"
    result.videos[0].save(str(out))
    return out


async def _still_as_video(
    still_path: Path, duration: float, out_path: Path
) -> Path:
    """Fallback when Veo moderates / fails — render the still as a 4s MP4
    with a slow ken-burns zoom so the scene still has motion."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-t", f"{duration:.3f}",
        "-i", str(still_path),
        "-vf",
        f"scale=1280:2280:force_original_aspect_ratio=increase,"
        f"crop=1280:2280,"
        f"crop=720:1280:"
        f"x='(100 - 100*t/{max(duration, 0.1):.3f})':"
        f"y='(500 - 500*t/{max(duration, 0.1):.3f})',"
        f"fps=30,format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "21",
        "-r", "30",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"video_gen fallback ffmpeg failed: {err.decode(errors='replace')[-400:]}"
        )
    return out_path


def _placeholder_frame(idx: int, out_dir: Path) -> Path:
    """Generate a dark gradient JPEG as a last-resort first frame.

    Used only when image generation fails ALL retries — we still want a
    9:16 720×1280 jpeg so the rest of the pipeline can continue and the
    user gets a complete reel rather than a hard failure.
    """
    from PIL import Image, ImageDraw
    out = out_dir / f"seg-{idx:02d}-frame-placeholder.jpg"
    w, h = 720, 1280
    img = Image.new("RGB", (w, h), (12, 12, 16))
    draw = ImageDraw.Draw(img)
    # Subtle radial-ish gradient by drawing concentric darker rectangles.
    for step in range(0, 60, 2):
        shade = 18 + step // 2
        draw.rectangle(
            [step * 8, step * 14, w - step * 8, h - step * 14],
            outline=(shade, shade, shade + 4),
        )
    img.save(out, "JPEG", quality=90)
    return out


async def gen_shot(
    provider: OpenRouterProvider,
    seg: Scene,
    plan: ShotPlanV2,
    out_dir: Path,
) -> BeatArtifact:
    """Generate first-frame + video for one segment, with two fallbacks.

    Failure modes handled in priority order:
      1. Image gen fails after retries  → placeholder still + ken-burns.
      2. Veo i2v fails (most often: content moderation false-positive)
                                        → still + ken-burns on the real frame.
    Either way the segment produces SOMETHING so the reel still assembles.
    """
    try:
        frame = await _gen_first_frame(provider, plan, seg.idx, out_dir)
    except Exception as e:
        print(
            f"[video_gen] scene {seg.idx} image gen failed after retries ({e}); "
            f"using placeholder frame."
        )
        frame = _placeholder_frame(seg.idx, out_dir)
        # Skip Veo entirely — i2v on a placeholder is a waste.
        fallback = out_dir / f"seg-{seg.idx:02d}-fallback.mp4"
        video = await _still_as_video(frame, duration=4.0, out_path=fallback)
        return BeatArtifact(idx=seg.idx, image_path=video)

    try:
        video = await _gen_video(provider, plan, seg, frame, out_dir)
    except Exception as e:
        print(f"[video_gen] scene {seg.idx} Veo failed ({e}); falling back to still.")
        fallback = out_dir / f"seg-{seg.idx:02d}-fallback.mp4"
        video = await _still_as_video(frame, duration=4.0, out_path=fallback)
    return BeatArtifact(idx=seg.idx, image_path=video)


async def generate_videos(
    segments: list[Scene],
    plans: list[ShotPlanV2],
    out_dir: Path,
) -> list[BeatArtifact]:
    """Fan-out video generation across all segments."""
    if len(segments) != len(plans):
        raise ValueError("video_gen: segments and plans length mismatch")
    out_dir.mkdir(parents=True, exist_ok=True)
    provider = OpenRouterProvider()
    results = await asyncio.gather(
        *(gen_shot(provider, s, p, out_dir) for s, p in zip(segments, plans)),
        return_exceptions=True,
    )
    errs = [r for r in results if isinstance(r, Exception)]
    if errs:
        raise RuntimeError(
            f"video_gen: {len(errs)}/{len(segments)} shots failed. First error: {errs[0]}"
        )
    return [r for r in results if isinstance(r, BeatArtifact)]
