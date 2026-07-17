"""Integration (real ffmpeg): the production fetch cuts the actual span and
downloads the shared source once (AF-dxl)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import reel_af.app as app_mod
import reel_af.render.hooks as hooks
from reel_af.dsl.models import SegmentFetchRequest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required",
)


def _duration_s(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(proc.stdout.strip())


def test_fetch_cuts_real_span_and_downloads_source_once(monkeypatch, tmp_path, lavfi_mp4_factory):
    source = lavfi_mp4_factory(name="src", duration_s=6.0)

    downloads: list[str] = []

    def fake_download(source_url, output_path, **_kw):
        downloads.append(source_url)
        shutil.copy(source, output_path)  # stand in for the network fetch of the full source
        return Path(output_path)

    monkeypatch.setattr(hooks, "download_source", fake_download)
    # cut_source_span is NOT patched — exercise the real ffmpeg cut.

    url = "https://example.com/source.mp4"
    out = tmp_path / "seg"
    out.mkdir()
    r1 = SegmentFetchRequest(
        segment_id="s1", source_url=url, start_s=1.0, end_s=3.0, target_path=out / "s1.mp4"
    )
    r2 = SegmentFetchRequest(
        segment_id="s2", source_url=url, start_s=4.0, end_s=6.0, target_path=out / "s2.mp4"
    )

    p1 = app_mod._default_segment_fetch(r1)
    p2 = app_mod._default_segment_fetch(r2)

    # both segments produced, each ~2s (the cut span), not the whole 6s source
    assert p1.exists() and p2.exists()
    assert abs(_duration_s(p1) - 2.0) < 0.35
    assert abs(_duration_s(p2) - 2.0) < 0.35
    # distinct time regions of the moving testsrc2 pattern → distinct encoded bytes
    assert p1.read_bytes() != p2.read_bytes()
    # the source was downloaded ONCE for both segments
    assert downloads == [url]
    assert len(list(out.glob("_source-*.mp4"))) == 1
