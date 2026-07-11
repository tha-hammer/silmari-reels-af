from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from util import make_fake_provider, square_png_bytes

from reel_af.render.images import generate_first_frame


async def test_generate_first_frame_uses_explicit_model(tmp_path: Path):
    fake = make_fake_provider(image_data=square_png_bytes(256))
    provider = fake()

    await generate_first_frame(
        provider,
        "a quiet lab bench",
        0,
        tmp_path,
        model="premium/model-x",
    )

    image_calls = [kw for method, kw in fake.calls if method == "image"]
    assert image_calls and image_calls[0]["model"] == "premium/model-x"


async def test_generate_first_frame_defaults_to_env_model(tmp_path: Path):
    from reel_af.render import images

    fake = make_fake_provider(image_data=square_png_bytes(256))
    provider = fake()

    await generate_first_frame(provider, "a lab bench", 0, tmp_path)

    image_calls = [kw for method, kw in fake.calls if method == "image"]
    assert image_calls[0]["model"] == images.IMAGE_MODEL


@pytest.mark.parametrize("size", [256, 512, 1000])
async def test_carousel_crop_is_4x5_portrait(tmp_path: Path, size: int):
    fake = make_fake_provider(image_data=square_png_bytes(size))

    path = await generate_first_frame(fake(), "x", 0, tmp_path, crop="4x5")

    with Image.open(path) as image:
        assert image.size == (1080, 1350)


async def test_default_crop_still_9x16(tmp_path: Path):
    fake = make_fake_provider(image_data=square_png_bytes(512))

    path = await generate_first_frame(fake(), "x", 0, tmp_path)

    with Image.open(path) as image:
        assert image.size == (720, 1280)


def test_carousel_default_preset_is_4x5_portrait():
    from reel_af.render.presets import load_preset, preset_names

    assert "carousel-default" in preset_names()
    cfg = load_preset("carousel-default")
    assert (cfg["canvas_w"], cfg["canvas_h"]) == (1080, 1350)
    assert cfg["slide_count"] >= 1
    assert cfg["kind"] == "carousel"
    assert cfg.get("overlay") not in {"middle_third", "lower_third"}


@pytest.mark.parametrize("blank", ["", "   "])
async def test_blank_model_falls_back_to_default(tmp_path: Path, blank: str):
    from reel_af.render import images

    fake = make_fake_provider(image_data=square_png_bytes(256))

    await generate_first_frame(fake(), "x", 0, tmp_path, model=blank)

    image_calls = [kw for method, kw in fake.calls if method == "image"]
    assert image_calls[0]["model"] == images.IMAGE_MODEL
