"""Runtime SDK patches — fix critical AgentField bugs without bypassing the SDK.

The bugs themselves are written up in AGENTFIELD_SDK_ISSUES.md. Each patch
here exists ONLY to keep `OpenRouterProvider` callable end-to-end — when
the SDK is fixed upstream, the matching patch becomes a no-op.

The patches are applied on import. Idempotent.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


def _patch_image_output_save() -> None:
    """SDK issue #4 — ImageOutput.save() can't handle `data:image/...` URLs.

    Gemini image models return data URLs, but the SDK's save() pipes the URL
    through requests.get() which barfs on the `data:` scheme. We detect
    `data:` URLs and decode them locally instead of delegating to requests.
    """
    from agentfield.multimodal_response import ImageOutput

    if getattr(ImageOutput.save, "__reel_af_patched__", False):
        return

    original_save = ImageOutput.save

    def patched_save(self, path) -> None:  # type: ignore[no-untyped-def]
        url = getattr(self, "url", None) or ""
        if url.startswith("data:"):
            try:
                _, payload = url.split(",", 1)
            except ValueError as exc:
                raise RuntimeError(f"malformed data URL: {exc}")
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(payload))
            return
        return original_save(self, path)

    patched_save.__reel_af_patched__ = True  # type: ignore[attr-defined]
    ImageOutput.save = patched_save  # type: ignore[assignment]


def _patch_openrouter_video_download() -> None:
    """SDK issue #6 — OpenRouterProvider.generate_video() returns HTTP 401.

    OpenRouter returns `unsigned_urls` of the form
    `https://openrouter.ai/api/v1/videos/<id>/content?index=0` — despite the
    name, these are API endpoints that require the Authorization header.
    The SDK explicitly omits headers when downloading them ("video_url is a
    public CDN URL" — incorrect comment). Every video download therefore
    401s and the SDK raises before returning the video bytes.

    This patch reimplements generate_video() identically to the SDK except
    that the final download call passes the auth headers. When the SDK
    upstream adds headers (or switches to signed_urls), delete this file
    and the import in video_gen.py.
    """
    from agentfield import media_providers as _mp

    if getattr(_mp.OpenRouterProvider.generate_video, "__reel_af_patched__", False):
        return

    async def patched_generate_video(
        self,
        prompt: str,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        duration: Optional[float] = None,
        resolution: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        generate_audio: Optional[bool] = None,
        seed: Optional[int] = None,
        frame_images: Optional[list] = None,
        input_references: Optional[list] = None,
        poll_interval: float = 30.0,
        timeout: float = 600.0,
        **kwargs,
    ):
        import asyncio
        import time

        import aiohttp

        from agentfield.multimodal_response import (
            FileOutput,
            MultimodalResponse,
            VideoOutput,
        )

        api_key = self._api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY env var "
                "or pass api_key to OpenRouterProvider."
            )

        base_url = "https://openrouter.ai/api/v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        video_model = model or "openrouter/google/veo-2.0-generate-001"
        if video_model.startswith("openrouter/"):
            video_model = video_model[len("openrouter/") :]

        body: Dict[str, Any] = {"model": video_model, "prompt": prompt}
        if duration is not None:
            body["duration"] = duration
        if resolution is not None:
            body["resolution"] = resolution
        if aspect_ratio is not None:
            body["aspect_ratio"] = aspect_ratio
        if generate_audio is not None:
            body["generate_audio"] = generate_audio
        if seed is not None:
            body["seed"] = seed
        if frame_images is not None:
            body["frame_images"] = frame_images
        if input_references is not None:
            body["input_references"] = input_references
        if image_url is not None:
            body["image_url"] = image_url

        _error_messages = self._VIDEO_ERROR_MESSAGES

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/videos", headers=headers, json=body
            ) as resp:
                if resp.status != 202:
                    detail = await resp.text()
                    raise RuntimeError(
                        f"OpenRouter video submit failed: "
                        f"{_error_messages.get(resp.status, f'status {resp.status}')} "
                        f"— {detail[:500]}"
                    )
                submit_data = await resp.json()

            job_id = submit_data.get("id")
            if not job_id:
                raise RuntimeError(
                    f"OpenRouter video submit returned no job id: {submit_data}"
                )
            if not re.match(r"^[a-zA-Z0-9_-]+$", job_id):
                raise RuntimeError(f"OpenRouter returned invalid job id: {job_id!r}")

            poll_url = f"{base_url}/videos/{job_id}"
            start_time = time.monotonic()
            poll_data: Dict[str, Any] = {}
            MAX_POLL_RETRIES = 3
            consecutive_errors = 0
            while True:
                if time.monotonic() - start_time >= timeout:
                    raise TimeoutError(
                        f"OpenRouter video generation timed out after {timeout}s "
                        f"(job {job_id})"
                    )
                try:
                    async with session.get(poll_url, headers=headers) as resp:
                        if resp.status in (502, 503, 504):
                            consecutive_errors += 1
                            if consecutive_errors >= MAX_POLL_RETRIES:
                                detail = await resp.text()
                                raise RuntimeError(
                                    f"OpenRouter video poll failed after "
                                    f"{MAX_POLL_RETRIES} retries: HTTP "
                                    f"{resp.status} — {detail[:500]}"
                                )
                            await asyncio.sleep(poll_interval)
                            continue
                        if resp.status != 200:
                            detail = await resp.text()
                            raise RuntimeError(
                                f"OpenRouter video poll failed: "
                                f"{_error_messages.get(resp.status, f'status {resp.status}')} "
                                f"— {detail[:500]}"
                            )
                        consecutive_errors = 0
                        poll_data = await resp.json()
                except aiohttp.ClientError:
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_POLL_RETRIES:
                        raise
                    await asyncio.sleep(poll_interval)
                    continue

                status = poll_data.get("status", "")
                if status == "completed":
                    break
                if status == "failed":
                    raise RuntimeError(
                        f"OpenRouter video generation failed: "
                        f"{poll_data.get('error', 'unknown error')} (job {job_id})"
                    )
                await asyncio.sleep(poll_interval)

            unsigned_urls = poll_data.get("unsigned_urls", [])
            if not unsigned_urls:
                raise RuntimeError(
                    f"OpenRouter video completed but no URLs returned (job {job_id})"
                )

            video_url = unsigned_urls[0]
            _mp._assert_safe_download_url(video_url)

            # ─── THE FIX ─────────────────────────────────────────────────
            # OpenRouter's `unsigned_urls` are actually API endpoints that
            # require the Authorization header. SDK omits it; we add it.
            async with session.get(video_url, headers=headers) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"Failed to download video from {video_url}: "
                        f"HTTP {resp.status}"
                    )
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > _mp.MAX_VIDEO_BYTES:
                    raise RuntimeError(
                        f"Video too large ({int(content_length)} bytes). "
                        f"Max: {_mp.MAX_VIDEO_BYTES}"
                    )
                video_data_bytes = await resp.read()
                if len(video_data_bytes) > _mp.MAX_VIDEO_BYTES:
                    raise RuntimeError(
                        f"Video download exceeded {_mp.MAX_VIDEO_BYTES} byte limit"
                    )

        video_b64 = base64.b64encode(video_data_bytes).decode("utf-8")
        usage_data = poll_data.get("usage", {})
        cost = usage_data.get("cost")

        file_out = FileOutput(
            url=video_url, data=video_b64, mime_type="video/mp4",
            filename="generated_video.mp4",
        )
        video_out = VideoOutput(
            url=video_url, data=video_b64, mime_type="video/mp4",
            filename="generated_video.mp4",
        )
        return MultimodalResponse(
            text=prompt, audio=None, images=[],
            files=[file_out], videos=[video_out],
            raw_response={"job_id": job_id, "cost": cost, "poll_data": poll_data},
        )

    patched_generate_video.__reel_af_patched__ = True  # type: ignore[attr-defined]
    _mp.OpenRouterProvider.generate_video = patched_generate_video  # type: ignore[assignment]


def _patch_openrouter_speech() -> None:
    """SDK gap — OpenRouterProvider has no `generate_speech()`.

    The existing OpenRouterProvider.generate_audio() routes through
    /chat/completions with modalities=["text","audio"]. That works for
    chat-style audio models (openai/gpt-audio) but conflates the
    "tone instruction" with the "text to speak" — the model often reads
    the directive aloud.

    OpenRouter ALSO exposes /audio/speech (OpenAI-compatible TTS endpoint)
    which routes to dedicated TTS models — Google Gemini 3.1 Flash TTS,
    xAI Grok Voice, Kokoro, Orpheus, etc. These accept text + (optional)
    instructions as separate parameters, so the directive never gets
    spoken. We attach this as a new method on OpenRouterProvider.

    File this upstream as: "OpenRouterProvider missing /audio/speech
    support for dedicated TTS models".
    """
    from agentfield import media_providers as _mp

    if hasattr(_mp.OpenRouterProvider, "generate_speech"):
        return

    async def generate_speech(
        self,
        text: str,
        model: str = "google/gemini-3.1-flash-tts-preview",
        voice: str = "Kore",
        response_format: str = "pcm",
        instructions: Optional[str] = None,
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> bytes:
        """Call OpenRouter /audio/speech with a dedicated TTS model.

        Returns raw audio bytes in the requested response_format. For
        Gemini TTS only "pcm" is accepted (24 kHz mono 16-bit). Most
        other models accept "mp3" as well.
        """
        import aiohttp

        api_key = self._api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY env var "
                "or pass api_key to OpenRouterProvider."
            )
        send_model = model
        if send_model.startswith("openrouter/"):
            send_model = send_model[len("openrouter/") :]
        payload: Dict[str, Any] = {
            "model": send_model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
        }
        if instructions:
            payload["instructions"] = instructions
        # Allow caller to pass-through any vendor-specific param
        # (e.g. speed, sample_rate).
        for k, v in kwargs.items():
            payload.setdefault(k, v)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(
                "https://openrouter.ai/api/v1/audio/speech",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"OpenRouter /audio/speech failed ({resp.status}): "
                        f"{body[:500]}"
                    )
                return await resp.read()

    _mp.OpenRouterProvider.generate_speech = generate_speech  # type: ignore[attr-defined]


def apply_all() -> None:
    """Apply every patch. Idempotent — safe to call multiple times."""
    _patch_image_output_save()
    _patch_openrouter_video_download()
    _patch_openrouter_speech()


# Apply on import so any code that imports this module gets the fixes.
apply_all()
