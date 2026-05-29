"""Smoke test for the single-pass stitch architecture.

Drives :func:`reel_af.v2.render.stitch.stitch_v2` end-to-end against the
EXISTING per-shot artifacts under ``output/v2-e2597178/`` so we don't have
to re-generate Veo clips or TTS audio just to validate the assembly path.

The clips at ``output/v2-e2597178/clip-*.mp4`` are already the silent
1080×1920 per-shot outputs from a prior run; we feed them in directly as
``ShotArtifact.video_path``. The stitcher will re-render them (no-op-ish
scale/crop, trim to duration) and then run the single-pass concat +
libass + AAC mux. We assert the final reel exists, has both streams,
and is within ±0.1 s of the source WAV.

Skipped when ``ffmpeg`` is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from reel_af.v2.models import Card, Shot, ShotArtifact, ShotVisual, WordTiming
from reel_af.v2.render.stitch import stitch_v2

# Pre-existing artifacts on disk — re-used as inputs so the smoke test
# doesn't have to round-trip Veo or TTS.
_ARTIFACT_ROOT = Path(
    "/Users/santoshkumarradha/Documents/agentfield/code/examples/reel-af/output/v2-e2597178"
)
_FULL_WAV = _ARTIFACT_ROOT / "media" / "full.wav"

# Durations were probed against the existing clip-XX.mp4 files; baking them
# into the test keeps it ffmpeg-free at fixture-build time.
_CLIP_DURATIONS_S: tuple[float, ...] = (
    6.566667,
    3.966667,
    6.900000,
    6.333333,
    6.000000,
    6.666667,
)


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _ffprobe_streams(path: Path) -> list[str]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return [s.strip() for s in out.stdout.splitlines() if s.strip()]


def _make_shot(idx: int, start_s: float, duration_s: float) -> Shot:
    """Build a minimal Shot — one card with one word — covering the window.

    The single-pass stitcher only consumes:
      • ``shot.idx``                — for clip naming + artifact lookup
      • ``shot.duration_s``         — for the per-shot trim
      • ``shot.cards[*]``           — for the global ASS file
    The visual / accent layers are ignored in the smoke path, so we leave
    them at planner-required minima.
    """
    end_s = start_s + duration_s
    word = WordTiming(word=f"shot{idx}", start_s=start_s, end_s=end_s)
    card = Card(
        text=word.word,
        words=[word],
        start_s=start_s,
        end_s=end_s,
        line_count=1,
    )
    # veo_duration must be one of {4, 6, 8} and ≥ duration_s + 1.0 in theory;
    # the renderer doesn't validate against it. Use the smallest bucket that
    # is ≥ duration_s so model validation passes.
    if duration_s <= 4.0:
        veo = 4
    elif duration_s <= 6.0:
        veo = 6
    else:
        veo = 8
    return Shot(
        idx=idx,
        cards=[card],
        start_s=start_s,
        end_s=end_s,
        duration_s=duration_s,
        role="hook" if idx == 0 else ("payoff" if idx == 5 else "mechanism"),
        veo_duration=veo,
    )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg / ffprobe required for single-pass stitch smoke test",
)
@pytest.mark.skipif(
    not _FULL_WAV.exists(),
    reason=f"missing pre-existing TTS audio at {_FULL_WAV}",
)
@pytest.mark.asyncio
async def test_single_pass_stitch_smoke(tmp_path: Path) -> None:
    # Build the parallel input lists from the pre-existing on-disk clips.
    shots: list[Shot] = []
    artifacts: list[ShotArtifact] = []
    visuals: list[ShotVisual] = []
    cursor = 0.0
    for idx, dur in enumerate(_CLIP_DURATIONS_S):
        clip = _ARTIFACT_ROOT / f"clip-{idx:02d}.mp4"
        if not clip.exists():
            pytest.skip(f"missing per-shot input at {clip}")

        shots.append(_make_shot(idx=idx, start_s=cursor, duration_s=dur))
        artifacts.append(ShotArtifact(idx=idx, video_path=clip))
        visuals.append(
            ShotVisual(
                image_prompt="(unused in stitch smoke)",
                motion_hint="static",
                visual_anchor="(unused)",
            )
        )
        cursor += dur

    out_path = await stitch_v2(
        shots=shots,
        visuals=visuals,
        artifacts=artifacts,
        accents=[None] * len(shots),
        full_audio_path=_FULL_WAV,
        out_dir=tmp_path,
        run_id="stitch-smoke",
    )

    # Output exists where stitch_v2 said it would.
    assert out_path.exists(), f"stitch_v2 returned {out_path} but file missing"
    assert out_path.name == "reel.mp4"
    assert out_path.stat().st_size > 0

    # Both streams present.
    stream_types = _ffprobe_streams(out_path)
    assert "video" in stream_types, f"no video stream in {stream_types}"
    assert "audio" in stream_types, f"no audio stream in {stream_types}"

    # Duration matches the WAV within ±0.1 s. ``-shortest`` clamps to the
    # shorter of (video, audio); since the audio == sum(clip durations) here,
    # any drift > 100 ms would indicate the concat-filter / mux pair is
    # mistiming, which is exactly the failure mode this architecture fixes.
    wav_dur = _ffprobe_duration(_FULL_WAV)
    reel_dur = _ffprobe_duration(out_path)
    assert abs(reel_dur - wav_dur) <= 0.1, (
        f"reel duration {reel_dur:.3f}s vs wav {wav_dur:.3f}s "
        f"(delta {reel_dur - wav_dur:+.3f}s) exceeds ±0.1s tolerance"
    )

    # And the global ASS file should have been written alongside the reel.
    assert (tmp_path / "reel.ass").exists(), "global ASS file not written"
