"""First-frame image generation per shot (wraps OpenRouterProvider.generate_image)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agentfield.media_providers import OpenRouterProvider
from PIL import Image

from reel_af.v2.models import Shot, ShotVisual

IMAGE_MODEL = os.getenv(
    "REEL_AF_IMAGE_MODEL", "openrouter/google/gemini-2.5-flash-image"
)


# Style notes appended to every image prompt. Picked by content_mode so
# scientific reels don't end up looking like a perfume ad. Copied verbatim
# from v1 video_gen.py to keep visual continuity while v1 is still around.
_GENERAL_STYLE_NOTE = (
    "cinematic documentary still, warm natural light, shallow depth of field, "
    "35mm film grain, VERTICAL portrait composition (taller than wide, the "
    "subject occupies the upper-middle two-thirds), fills the frame, no text "
    "or letters"
)

_SCIENTIFIC_STYLE_NOTE = (
    "documentary photograph from a working research lab, sharp focus throughout, "
    "neutral white-balanced lighting (overhead fluorescent or a single bright "
    "desk lamp, no warm filters), realistic colors, no shallow depth-of-field "
    "blur, no film grain, no lens flares; VERTICAL portrait composition with "
    "the artifact (plot / paper / interface / instrument) occupying the upper "
    "two-thirds; the frame should look like a phone snapshot of an actual "
    "research workspace, not a movie still; no text or letters in frame"
)


def _style_note(content_mode: str) -> str:
    return (
        _SCIENTIFIC_STYLE_NOTE
        if content_mode == "scientific"
        else _GENERAL_STYLE_NOTE
    )


def _augment(prompt: str, content_mode: str = "general") -> str:
    """Append the style block (mode-aware) to an image prompt."""
    base = prompt.strip().rstrip(".")
    return f"{base}. {_style_note(content_mode)}."


def _save_image_output(img, dest: Path) -> None:
    """Save an SDK ImageOutput to disk.

    The SDK's data-URL handling was patched in main; the in-pipeline
    sdk_patches.py mirror is still imported by video.py for safety until
    pyproject pins the new SDK version.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest))


def _crop_to_9x16(src: Path, dest: Path, target_w: int = 720) -> Path:
    """Center-crop the still to 9:16 vertical for Veo i2v input.

    Gemini returns square (1024x1024); Veo expects vertical 9:16. Take a
    centered 9:16 strip and resize to 720x1280 (Veo's native res).

    We DO NOT pass image_config={"aspect_ratio": "9:16"} to the SDK — per
    AGENTFIELD_SDK_ISSUES #3 no upstream OpenRouter provider exposes the
    param, so any request with it 404s. We crop locally.
    """
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


async def gen_first_frame_v2(
    provider: OpenRouterProvider,
    visual: ShotVisual,
    shot: Shot,
    out_dir: Path,
    content_mode: str = "general",
    max_retries: int = 4,
) -> Path:
    """Generate a vertical 9:16 first frame from ShotVisual.image_prompt.

    OpenRouter routes the Gemini image model across multiple upstream
    providers; some 404 with 'No endpoints found that support the requested
    output modalities: image, text' when routing lands on a non-image-
    capable replica. We retry with exponential backoff to ride out those
    routing blips before bubbling up. The SDK now retries internally on
    NotFoundError (vision.py:189-217) so this call-site loop is belt-and-
    suspenders — harmless, costs nothing on the happy path.

    Returns the path to the cropped 720x1280 JPEG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = _augment(visual.image_prompt, content_mode=content_mode)
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
                f"[v2.render.images] shot {shot.idx} attempt {attempt + 1}/"
                f"{max_retries} image gen failed ({type(exc).__name__}: "
                f"{str(exc)[:160]}); retrying after backoff."
            )
            await asyncio.sleep(2 ** attempt)
            continue
        if not result.images:
            last_err = RuntimeError("model returned no images")
            await asyncio.sleep(2 ** attempt)
            continue
        raw = out_dir / f"seg-{shot.idx:02d}-frame-raw.png"
        out = out_dir / f"seg-{shot.idx:02d}-frame.jpg"
        _save_image_output(result.images[0], raw)
        _crop_to_9x16(raw, out)
        return out
    raise RuntimeError(
        f"v2.render.images: image model {IMAGE_MODEL} failed {max_retries} "
        f"times for shot {shot.idx}. Last error: {last_err}"
    )
