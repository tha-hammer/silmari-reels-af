from __future__ import annotations

import json

import pytest

from reel_af.render.finish_config import ReelFinishConfig
from reel_af.render.hooks import (
    CRISP_YTDLP_FORMAT,
    build_crisp_ytdlp_command,
    generate_hook,
    pick_image_moments,
)


class TextProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_crisp_ytdlp_command_uses_vertical_native_format(tmp_path):
    out_path = tmp_path / "source.mp4"

    cmd = build_crisp_ytdlp_command("https://youtu.be/example", out_path)

    assert cmd[:2] == ["yt-dlp", "-f"]
    assert CRISP_YTDLP_FORMAT == "137+140/137+bestaudio[ext=m4a]"
    assert CRISP_YTDLP_FORMAT in cmd
    assert "--merge-output-format" in cmd
    assert cmd[cmd.index("--merge-output-format") + 1] == "mp4"
    assert "height<=1080" not in " ".join(cmd)


@pytest.mark.asyncio
async def test_generate_hook_truncates_mock_provider_output_to_eight_words():
    provider = TextProvider(
        "Hook: this is an absurdly long hook that should be clipped immediately"
    )

    hook = await generate_hook(
        "A transcript about why vertical reels need sharper source downloads.",
        provider,
    )

    assert hook
    assert len(hook.split()) <= 8
    assert hook == "this is an absurdly long hook that should"
    assert provider.calls


@pytest.mark.asyncio
async def test_pick_image_moments_returns_configured_safe_non_overlapping_picks():
    provider = TextProvider(
        json.dumps(
            {
                "moments": [
                    {
                        "t_start": 0.25,
                        "t_end": 8.0,
                        "image_prompt": "a crisp close-up of the vertical source clip",
                    },
                    {
                        "t_start": 8.0,
                        "t_end": 9.0,
                        "image_prompt": "timeline view showing the exact cut point",
                    },
                    {
                        "t_start": 18.5,
                        "t_end": 24.5,
                        "image_prompt": "final reel preview with captions and cut-ins",
                    },
                ]
            }
        )
    )
    cfg = ReelFinishConfig(image_count=3)

    picks = await pick_image_moments(
        "The reel explains why the old download was blurry, then shows the fix.",
        provider,
        cfg,
        duration_s=24.0,
    )

    assert len(picks) == cfg.image_count
    previous_end = 0.0
    for pick in picks:
        assert cfg.image_edge_guard_s < pick.t_start < pick.t_end < 24.0 - cfg.image_edge_guard_s
        assert cfg.image_min_dur_s <= pick.t_end - pick.t_start <= cfg.image_max_dur_s
        assert pick.t_start >= previous_end
        assert pick.image_prompt.strip()
        previous_end = pick.t_end
