"""Regression: the DSL-hooks default segment fetcher resolves its downloader.

`_default_segment_fetch` lazily imports `download_crisp_source`. It was importing
it from `reel_af.render.video` (where it does not exist) instead of
`reel_af.render.hooks`, so the DSL-hooks render died with
`dsl_render_failed: cannot import name 'download_crisp_source'` — but only once a
compile succeeded and the render actually ran (the import is inside the function
body, so module import of app.py never triggered it).
"""

from __future__ import annotations

import types

import reel_af.app as app_mod


def test_default_segment_fetch_resolves_downloader_from_hooks(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []

    def spy(source_url, output_path, **_kw):
        calls.append((source_url, str(output_path)))
        return output_path

    # Patch the REAL location; the lazy `from reel_af.render.hooks import ...`
    # inside _default_segment_fetch must resolve here (raises ImportError if the
    # function points at the wrong module).
    monkeypatch.setattr("reel_af.render.hooks.download_crisp_source", spy)

    target = tmp_path / "seg.mp4"
    request = types.SimpleNamespace(source_url="https://youtu.be/x", target_path=target)
    result = app_mod._default_segment_fetch(request)

    assert result == target
    assert calls == [("https://youtu.be/x", str(target))]
