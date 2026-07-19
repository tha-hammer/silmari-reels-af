"""Shared test helpers.

Everything here exists to let the suite exercise the *documented* behaviour
of reel-af without making a single real network call. Providers are faked,
media bytes are synthetic, and the only external dependency is ffmpeg/ffprobe
(already required to run the app at all).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

import pytest
from PIL import Image

# Skip ffmpeg-dependent tests when the binaries are absent (e.g. a bare CI
# image). The README lists ffmpeg as a hard requirement, so locally these run.
requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)


# ───── synthetic media ───────────────────────────────────────────────


def silence_pcm(seconds: float = 0.5, rate: int = 24000) -> bytes:
    """Raw little-endian 16-bit mono PCM silence — what Gemini TTS streams."""
    return b"\x00\x00" * int(rate * seconds)


def square_png_bytes(size: int = 512, color: tuple = (120, 120, 120)) -> bytes:
    """A real, PIL-decodable square PNG to stand in for a generated still."""
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeImage:
    """Stand-in for the SDK's image-output object — only needs ``.save()``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def save(self, path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self._data)


def make_fake_provider(
    *,
    speech_pcm: bytes = b"",
    image_data: Optional[bytes] = None,
    video_bytes: bytes = b"\x00",
    speech_error: Optional[Exception] = None,
    image_error: Optional[Exception] = None,
    video_error: Optional[Exception] = None,
):
    """Build a drop-in replacement for ``OpenRouterProvider``.

    Every call is recorded on the class-level ``calls`` list as
    ``(method, kwargs)`` so tests can assert *what the renderer asked the
    provider to do* — the exact surface the provider refactor will touch.
    """
    calls: list = []

    class FakeProvider:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", dict(kwargs)))

        async def generate_speech(self, **kwargs):
            calls.append(("speech", kwargs))
            if speech_error is not None:
                raise speech_error
            return speech_pcm

        async def generate_image(self, **kwargs):
            calls.append(("image", kwargs))
            if image_error is not None:
                raise image_error
            return [FakeImage(image_data)] if image_data is not None else []

        async def generate_video(self, **kwargs):
            calls.append(("video", kwargs))
            if video_error is not None:
                raise video_error
            return video_bytes

    FakeProvider.calls = calls
    return FakeProvider


# ───── model fixtures ────────────────────────────────────────────────


def make_beat(idx: int = 0, role: str = "hook", veo_duration: int = 4):
    from reel_af.models import Beat

    return Beat(
        idx=idx,
        role=role,
        text="A surprising one-sentence claim.",
        target_duration_s=float(veo_duration),
        veo_duration=veo_duration,
    )


def make_visual(motion_hint: str = "static"):
    from reel_af.models import BeatVisual

    return BeatVisual(
        image_prompt="a research lab bench with an instrument",
        motion_hint=motion_hint,
        visual_anchor="evidence-1",
    )


# ───── out-of-process config probe ───────────────────────────────────

_PROBE = textwrap.dedent(
    """
    import json
    import reel_af.app as a
    import reel_af.render.tts as tts
    import reel_af.render.images as images
    import reel_af.render.video as video
    print("__PROBE__" + json.dumps({
        "model": a.app.ai_config.model,
        "api_base": a.app.ai_config.api_base,
        "api_key": a.app.ai_config.api_key,
        "tts_default": tts.DEFAULT_TTS_MODEL,
        "image_model": images.IMAGE_MODEL,
        "video_model": video.VIDEO_MODEL,
        "use_veo": video.USE_VEO,
    }))
    """
)


def run_config_probe(overrides: Optional[dict] = None) -> dict:
    """Import reel-af in a clean subprocess under ``overrides`` and report the
    resolved provider config.

    Module-level constants (``IMAGE_MODEL``, ``USE_VEO``, the agent's
    ``ai_config``) are read at import time, so a subprocess is the honest way
    to characterise "env var X in → config Y out". A value of ``None`` in
    ``overrides`` unsets that variable for the child.
    """
    src = str(Path(__file__).resolve().parent.parent / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHON_DOTENV_DISABLED"] = "true"
    env.setdefault("OPENROUTER_API_KEY", "test-dummy")
    for key, value in (overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr}"
    line = next(
        ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("__PROBE__")
    )
    return json.loads(line[len("__PROBE__"):])
