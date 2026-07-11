"""First-frame image generation per beat — Gemini 2.5 Flash Image.

Each beat needs a single still image that Veo will animate into a clip.
The image generator returns a square frame; we center-crop to 9:16 720x1280
which is Veo's native vertical resolution.

Style notes vary by content mode so scientific reels don't end up looking
like a perfume ad.
"""

from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from agentfield.media_providers import OpenRouterProvider
from PIL import Image

import reel_af.sdk_patches  # noqa: F401

_CONFIG_PATH = Path(__file__).parent / "config" / "images.json"


@lru_cache(maxsize=1)
def _image_config() -> dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text())


_IMAGE_CFG = _image_config()
CROP_9X16 = str(_IMAGE_CFG["crop_9x16"])
CROP_4X5 = str(_IMAGE_CFG["crop_4x5"])
_DEFAULT_CROP = str(_IMAGE_CFG["default_crop"])
_CROP_9X16_TARGET_W = int(_IMAGE_CFG["crop_9x16_target_w"])
_CROP_4X5_TARGET_W = int(_IMAGE_CFG["crop_4x5_target_w"])
_CROP_9X16_RATIO_W = int(_IMAGE_CFG["crop_9x16_ratio_w"])
_CROP_9X16_RATIO_H = int(_IMAGE_CFG["crop_9x16_ratio_h"])
_CROP_4X5_RATIO_W = int(_IMAGE_CFG["crop_4x5_ratio_w"])
_CROP_4X5_RATIO_H = int(_IMAGE_CFG["crop_4x5_ratio_h"])
_JPEG_QUALITY = int(_IMAGE_CFG["jpeg_quality"])
_PROVIDER_IMAGE_COUNT = int(_IMAGE_CFG["provider_image_count"])
_NO_IMAGE_ERROR_TEMPLATE = str(_IMAGE_CFG["no_image_error_template"])
_CROP_9X16_RATIO = _CROP_9X16_RATIO_W / _CROP_9X16_RATIO_H
_CROP_4X5_RATIO = _CROP_4X5_RATIO_W / _CROP_4X5_RATIO_H

IMAGE_MODEL = os.getenv(
    "REEL_AF_IMAGE_MODEL", str(_IMAGE_CFG["default_image_model"])
)

# Style notes appended to every image prompt. Picked by content_mode.
_GENERAL_STYLE_NOTE = str(_IMAGE_CFG["general_style_note"])
_SCIENTIFIC_STYLE_NOTE = str(_IMAGE_CFG["scientific_style_note"])


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


def _crop_to_ratio(
    src: Path,
    dest: Path,
    *,
    ratio: float,
    target_w: int,
    target_h: int,
) -> Path:
    """Center-crop and resize locally for provider aspect targets.

    We DO NOT pass image_config={"aspect_ratio": "..."} to the SDK — no
    upstream OpenRouter provider exposes the param, so any request with it
    404s. We crop locally.
    """
    img = Image.open(src).convert("RGB")
    w, h = img.size
    desired_ratio = ratio
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
    img.save(str(dest), format="JPEG", quality=_JPEG_QUALITY)
    return dest


def _crop_to_9x16(src: Path, dest: Path, target_w: int = _CROP_9X16_TARGET_W) -> Path:
    """Center-crop the still to 9:16 vertical for Veo i2v input.

    Gemini returns roughly square (1024x1024); Veo expects vertical 9:16.
    Take a centered 9:16 strip and resize to 720x1280 (Veo's native res).
    """
    return _crop_to_ratio(
        src,
        dest,
        ratio=_CROP_9X16_RATIO,
        target_w=target_w,
        target_h=target_w * _CROP_9X16_RATIO_H // _CROP_9X16_RATIO_W,
    )


def _crop_to_4x5(src: Path, dest: Path, target_w: int = _CROP_4X5_TARGET_W) -> Path:
    """Center-crop to 4:5 Instagram portrait, 1080x1350 by default."""
    return _crop_to_ratio(
        src,
        dest,
        ratio=_CROP_4X5_RATIO,
        target_w=target_w,
        target_h=target_w * _CROP_4X5_RATIO_H // _CROP_4X5_RATIO_W,
    )


_CROP_TARGETS = {
    CROP_9X16: _crop_to_9x16,
    CROP_4X5: _crop_to_4x5,
}


async def generate_first_frame(
    provider: OpenRouterProvider,
    image_prompt: str,
    idx: int,
    out_dir: Path,
    content_mode: str = "general",
    *,
    model: str | None = None,
    crop: str = _DEFAULT_CROP,
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
        n=_PROVIDER_IMAGE_COUNT,
    )
    # generate_image returns a MultimodalResponse; its .images are
    # ImageOutput(url, b64_json, ...) rather than PIL images. Persist the
    # first one's bytes to raw_path.
    images = _response_images(resp)
    if not images:
        raise RuntimeError(_NO_IMAGE_ERROR_TEMPLATE.format(idx=idx))
    await _save_image_output(images[0], raw_path)
    cropper = _CROP_TARGETS.get(crop, _crop_to_9x16)
    return cropper(raw_path, final_path)


async def _save_image_output(image, dest: Path) -> None:
    """Persist an SDK ImageOutput (b64_json or url) to `dest` as raw bytes."""
    b64 = getattr(image, "b64_json", None)
    url = getattr(image, "url", None)
    if b64:
        dest.write_bytes(base64.b64decode(b64))
        return
    is_data_url = bool(url and url.startswith("data:"))
    if is_data_url:
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
    can_save = callable(save)
    if can_save:
        dest.parent.mkdir(parents=True, exist_ok=True)
        save(dest)
        return
    raise RuntimeError("image output had neither b64_json nor url")


def _response_images(resp: Any) -> Sequence[Any]:
    images = getattr(resp, "images", None)
    if images is not None:
        return images
    is_sequence_response = isinstance(resp, Sequence)
    is_bytes_like = isinstance(resp, (str, bytes, bytearray))
    if is_sequence_response and not is_bytes_like:
        return resp
    return []
