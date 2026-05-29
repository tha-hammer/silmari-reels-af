"""Integration smoke test for v2 forced alignment.

Runs whisper-cli against an existing TTS WAV and asserts the returned
WordTimings cover the script monotonically and within audio bounds. Marked
skip when whisper-cli isn't on PATH so CI without the brew binary still
passes.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from reel_af.v2.render.alignment import align_audio


_AUDIO = Path(
    "/Users/santoshkumarradha/Documents/agentfield/code/examples/reel-af/"
    "output/v2-e2597178/media/full.wav"
)
_RESULT_JSON = Path("/tmp/v2y-result1.json")
_AUDIO_DUR_S = 36.4


@pytest.mark.skipif(
    not shutil.which("whisper-cli"),
    reason="whisper-cli not installed",
)
def test_align_audio_against_real_tts():
    assert _AUDIO.exists(), f"missing TTS WAV: {_AUDIO}"
    assert _RESULT_JSON.exists(), f"missing result JSON: {_RESULT_JSON}"

    payload = json.loads(_RESULT_JSON.read_text())
    script = payload["result"]["script"]
    assert script and isinstance(script, str)

    timings = asyncio.run(align_audio(_AUDIO, script))

    assert len(timings) > 0, "no timings returned"

    # Boundary conditions.
    assert timings[0].start_s >= 0.0, f"first start_s negative: {timings[0]}"
    assert timings[-1].end_s <= _AUDIO_DUR_S + 0.5, (
        f"last end_s {timings[-1].end_s} exceeds audio dur {_AUDIO_DUR_S}"
    )

    # Monotonic non-decreasing start and end across the whole list.
    prev_start = -1.0
    prev_end = -1.0
    for t in timings:
        assert t.start_s >= prev_start - 1e-6, f"start_s regressed: {t}"
        assert t.end_s >= prev_end - 1e-6, f"end_s regressed: {t}"
        assert t.end_s >= t.start_s - 1e-6, f"end before start: {t}"
        prev_start, prev_end = t.start_s, t.end_s

    # Visual inspection — printed when running with -s.
    print("\n--- first 10 word timings ---")
    for t in timings[:10]:
        print(f"  {t.start_s:6.2f} → {t.end_s:6.2f}  {t.word!r}")
    print(f"--- total tokens: {len(timings)} | last end_s: {timings[-1].end_s:.2f}s ---")
