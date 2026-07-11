"""First-frame image generation per beat — Gemini 2.5 Flash Image.

Each beat needs a single still image that Veo will animate into a clip.
The image generator returns a square frame; we center-crop to 9:16 720x1280
which is Veo's native vertical resolution.

Style notes vary by content mode so scientific reels don't end up looking
like a perfume ad.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Sequence

from agentfield.media_providers import OpenRouterProvider
from PIL import Image

import reel_af.sdk_patches  # noqa: F401

IMAGE_MODEL = os.getenv(
    "REEL_AF_IMAGE_MODEL", "openrouter/google/gemini-2.5-flash-image"
)

# Style notes appended to every image prompt. Picked by content_mode.
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


def _crop_to_9x16(src: Path, dest: Path, target_w: int = 720) -> Path:
    """Center-crop the still to 9:16 vertical for Veo i2v input.

    Gemini returns roughly square (1024x1024); Veo expects vertical 9:16.
    Take a centered 9:16 strip and resize to 720x1280 (Veo's native res).

    We DO NOT pass image_config={"aspect_ratio": "9:16"} to the SDK — no
    upstream OpenRouter provider exposes the param, so any request with
    it 404s. We crop locally.
    """
    target_h = target_w * 16 // 9
    img = Image.open(src).convert("RGB")
    w, h = img.size
    desired_ratio = 9 / 16
    cur_ratio = w / h
    if cur_ratio > desired_ratio:
        new_w = int(h * desired_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif cur_ratio < desired_ratio:
        new_h = int(w / desired_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), format="JPEG", quality=92)
    return dest


async def generate_first_frame(
    provider: OpenRouterProvider,
    image_prompt: str,
    idx: int,
    out_dir: Path,
    content_mode: str = "general",
    *,
    model: str | None = None,
) -> Path:
    """Generate one 720×1280 first frame for a beat.

    Calls Gemini Image, saves the raw output, then center-crops to 9:16.
    Raises on hard failure — callers catch and fall back to a placeholder
    + ken-burns when an individual frame fails.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"frame-{idx:02d}-raw.png"
    final_path = out_dir / f"frame-{idx:02d}.jpg"

    augmented = _augment(image_prompt, content_mode)
    selected_model = (model or "").strip() or IMAGE_MODEL
    resp = await provider.generate_image(
        prompt=augmented,
        model=selected_model,
        n=1,
    )
    # generate_image returns a MultimodalResponse; its .images are
    # ImageOutput(url, b64_json, ...) rather than PIL images. Persist the
    # first one's bytes to raw_path.
    images = _response_images(resp)
    if not images:
        raise RuntimeError(
            f"generate_first_frame: image gen returned no images for beat {idx}"
        )
    await _save_image_output(images[0], raw_path)
    return _crop_to_9x16(raw_path, final_path)


async def _save_image_output(image, dest: Path) -> None:
    """Persist an SDK ImageOutput (b64_json or url) to `dest` as raw bytes."""
    b64 = getattr(image, "b64_json", None)
    url = getattr(image, "url", None)
    if b64:
        dest.write_bytes(base64.b64decode(b64))
        return
    if url and url.startswith("data:"):
        dest.write_bytes(base64.b64decode(url.split(",", 1)[1]))
        return
    if url:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                r.raise_for_status()
                dest.write_bytes(await r.read())
        return
    save = getattr(image, "save", None)
    if callable(save):
        dest.parent.mkdir(parents=True, exist_ok=True)
        save(dest)
        return
    raise RuntimeError("image output had neither b64_json nor url")


def _response_images(resp: Any) -> Sequence[Any]:
    images = getattr(resp, "images", None)
    if images is not None:
        return images
    if isinstance(resp, Sequence) and not isinstance(resp, (str, bytes, bytearray)):
        return resp
    return []
