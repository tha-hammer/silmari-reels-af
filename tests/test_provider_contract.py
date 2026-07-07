"""Contract tests — the REAL provider factories must expose the methods the
finish stage actually calls. These would have caught the image-only provider
being handed to the text `.ai()` call (no live API — just interface checks).
See thoughts/.../2026-07-07-reel-finish-test-construction-postmortem.md.
"""
from reel_af.cli import _composite_text_provider, _composite_image_provider


def test_text_provider_exposes_ai():
    # hooks.generate_hook / pick_image_moments call provider.ai(...)
    assert hasattr(_composite_text_provider(), "ai")


def test_image_provider_exposes_generate_image():
    # image_cutins generation calls provider.generate_image(...)
    assert hasattr(_composite_image_provider(), "generate_image")
