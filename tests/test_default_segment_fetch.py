"""The DSL-hooks default segment fetcher downloads the source once, then cuts
each segment's own ``[start_s, end_s]`` span (AF-dxl).

Guards two things:
1. The downloader/cutter are resolved from ``reel_af.render.hooks`` (the original
   regression: it was importing from ``reel_af.render.video`` where the function
   does not exist — the render died with ``cannot import name`` only once a compile
   succeeded and the render actually ran). Monkeypatching hooks only takes effect
   if the lazy import points at hooks.
2. The source is downloaded ONCE per render dir and each segment is cut to its own
   span — not the full source relabelled, which cut ``[0, duration]`` = the intro.
"""

from __future__ import annotations

from pathlib import Path

import reel_af.app as app_mod
import reel_af.render.hooks as hooks
from reel_af.dsl.models import SegmentFetchRequest


def test_default_segment_fetch_downloads_once_and_cuts_each_span(monkeypatch, tmp_path):
    downloads: list[str] = []
    cuts: list[tuple[float, float]] = []

    def fake_download(source_url, output_path, **_kw):
        downloads.append(source_url)
        Path(output_path).write_bytes(b"FULLSOURCE")  # materialize the shared source
        return Path(output_path)

    def fake_cut(source_path, start_s, end_s, output_path, **_kw):
        cuts.append((start_s, end_s))
        Path(output_path).write_bytes(b"CUT")
        return Path(output_path)

    # Patch the REAL location; the lazy imports inside _default_segment_fetch must
    # resolve here (raises ImportError if they point at the wrong module).
    monkeypatch.setattr(hooks, "download_source", fake_download)
    monkeypatch.setattr(hooks, "cut_source_span", fake_cut)

    url = "https://t3.storageapi.dev/bucket/source.mp4"
    out = tmp_path / "seg"
    out.mkdir()
    requests = [
        SegmentFetchRequest(
            segment_id=sid, source_url=url, start_s=s, end_s=e, target_path=out / f"{sid}.mp4"
        )
        for sid, s, e in (("s1", 413.0, 415.0), ("s2", 432.0, 435.0), ("s3", 452.0, 455.0))
    ]
    for request in requests:
        assert app_mod._default_segment_fetch(request) == request.target_path

    assert downloads == [url]  # downloaded ONCE for 3 segments of the same source
    assert cuts == [(413.0, 415.0), (432.0, 435.0), (452.0, 455.0)]  # each its own span, not [0, dur]
    # exactly one shared source file was created
    assert len(list(out.glob("_source-*.mp4"))) == 1
