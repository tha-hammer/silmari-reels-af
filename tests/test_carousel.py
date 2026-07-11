from __future__ import annotations

from pathlib import Path

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
