"""B11 — ``reel-af composite <url>`` CLI thin front-end.

The command parses the URL + flags and drives ``composite_to_reel`` in order.
The pipeline itself is mocked (spy) so we assert wiring, not ffmpeg. ``--fast``
threads ``raw=True`` and builds no provider.
"""

from __future__ import annotations

from typer.testing import CliRunner

import reel_af.cli as cli_mod

runner = CliRunner()


def _patch(monkeypatch, calls: list[dict]):
    async def fake_pipeline(
        url, out_dir, *, text_provider=None, image_provider=None, cfg=None, raw=False, **kw
    ):
        calls.append(
            {
                "url": url,
                "out_dir": str(out_dir),
                "raw": raw,
                "text_provider": text_provider,
                "image_provider": image_provider,
            }
        )
        return out_dir / ("base.mp4" if raw else "final.mp4")

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # A stand-in text provider that (like the real Agent) exposes .ai().
    class _TextProvider:
        async def ai(self, *, system, user, schema=None):
            return {"hook": "x"}

    monkeypatch.setattr(cli_mod, "_composite_text_provider", lambda: _TextProvider())
    monkeypatch.setattr(cli_mod, "_composite_image_provider", lambda: "IMAGE_PROVIDER")
    # composite_to_reel is imported inside the command; patch it at the source.
    import reel_af.render.composite_pipeline as pipe
    monkeypatch.setattr(pipe, "composite_to_reel", fake_pipeline)


def test_composite_default_is_rich_finish(monkeypatch, tmp_path):
    calls: list[dict] = []
    _patch(monkeypatch, calls)
    result = runner.invoke(cli_mod.app, ["composite", "http://youtu.be/x", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://youtu.be/x"
    assert call["raw"] is False              # rich finish by default
    # The TEXT provider wired for hook/moments must expose .ai() (the bug).
    assert hasattr(call["text_provider"], "ai")
    assert call["image_provider"] == "IMAGE_PROVIDER"
    assert "final.mp4" in result.output


def test_composite_fast_flag_opts_out_and_builds_no_provider(monkeypatch, tmp_path):
    calls: list[dict] = []
    _patch(monkeypatch, calls)
    result = runner.invoke(
        cli_mod.app, ["composite", "http://youtu.be/x", "--fast", "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    call = calls[0]
    assert call["raw"] is True               # --fast => plain stitched reel
    assert call["text_provider"] is None     # no providers built on the fast path
    assert call["image_provider"] is None
    assert "base.mp4" in result.output


def test_composite_requires_url(monkeypatch, tmp_path):
    calls: list[dict] = []
    _patch(monkeypatch, calls)
    result = runner.invoke(cli_mod.app, ["composite"])
    assert result.exit_code != 0             # url is a required argument
    assert calls == []


def test_real_text_provider_exposes_ai_and_image_provider_does_not():
    """NON-mocked wiring guard: the TEXT provider must expose .ai() (hook/moments);
    the media image provider must NOT (that was the bug — image-only provider fed
    to generate_hook → 'provider must expose ai(...)')."""
    text = cli_mod._composite_text_provider()
    assert hasattr(text, "ai"), "text provider must expose .ai() for hook + moments"

    image = cli_mod._composite_image_provider()
    assert not hasattr(image, "ai"), "media image provider has no .ai() — never route hooks to it"
