"""``reel-af reels --preset <name> <source>`` — preset-driven overlay reels.

The command resolves a source + transcript, cuts the source into
preset-length windows, renders a Remotion overlay per window, and composites.
Heavy stages (download, whisper, Remotion, ffmpeg) are patched so these assert
wiring — preset dispatch, reel-count math, ``--only`` selection, cleanup — not
external tools.
"""

from __future__ import annotations

from typer.testing import CliRunner

import reel_af.cli as cli_mod

runner = CliRunner()


def test_reels_appears_in_help():
    result = runner.invoke(cli_mod.app, ["--help"])
    assert result.exit_code == 0
    assert "reels" in result.output


def test_unknown_preset_errors_cleanly(tmp_path):
    result = runner.invoke(
        cli_mod.app, ["reels", str(tmp_path / "x.mp4"), "--preset", "no-such-preset"]
    )
    assert result.exit_code != 0
    assert "unknown preset" in result.output


def test_unsupported_overlay_errors_cleanly(tmp_path):
    # A real preset whose overlay is not yet wired into `reels`.
    result = runner.invoke(
        cli_mod.app,
        ["reels", str(tmp_path / "x.mp4"), "--preset", "horizontal-youtube-lowerthird"],
    )
    assert result.exit_code != 0
    assert "middle_third" in result.output


def _patch_pipeline(monkeypatch, tmp_path, made: list, *, duration: float):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"\x00")

    monkeypatch.setattr(cli_mod, "_resolve_source", lambda source, work: src)
    monkeypatch.setattr(cli_mod, "_resolve_words", lambda source, wj, work: [(0.0, 1.0, "hi")])
    monkeypatch.setattr(cli_mod, "_ffprobe_duration", lambda source: duration)

    import reel_af.render.middle_third as mt

    monkeypatch.setattr(mt, "window_segments", lambda *a, **k: [])
    monkeypatch.setattr(mt, "render_overlay", lambda segs, tf, seq, cfg, **k: seq)

    def _composite(source, t0, dur_s, seq, out, **k):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        made.append(out.name)
        return out

    monkeypatch.setattr(mt, "composite_window", _composite)
    return src


def test_reel_count_from_duration(monkeypatch, tmp_path):
    made: list = []
    _patch_pipeline(monkeypatch, tmp_path, made, duration=250.0)  # 250 // 120 = 2
    out = tmp_path / "out"
    result = runner.invoke(
        cli_mod.app,
        ["reels", "ignored", "--preset", "middle-third-dynamic", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert made == ["reel01.mp4", "reel02.mp4"]


def test_only_selects_subset(monkeypatch, tmp_path):
    made: list = []
    _patch_pipeline(monkeypatch, tmp_path, made, duration=600.0)  # would be 5 reels
    out = tmp_path / "out"
    result = runner.invoke(
        cli_mod.app,
        ["reels", "ignored", "--preset", "middle-third-dynamic", "--out", str(out), "--only", "3"],
    )
    assert result.exit_code == 0, result.output
    assert made == ["reel03.mp4"]


def test_only_out_of_range_errors_and_makes_nothing(monkeypatch, tmp_path):
    made: list = []
    _patch_pipeline(monkeypatch, tmp_path, made, duration=250.0)  # only 2 reels exist
    out = tmp_path / "out"
    result = runner.invoke(
        cli_mod.app,
        ["reels", "ignored", "--preset", "middle-third-dynamic", "--out", str(out), "--only", "99"],
    )
    assert result.exit_code != 0
    assert "out of range" in result.output
    assert made == []


def test_source_shorter_than_one_reel_errors(monkeypatch, tmp_path):
    made: list = []
    _patch_pipeline(monkeypatch, tmp_path, made, duration=30.0)  # < 120s reel
    out = tmp_path / "out"
    result = runner.invoke(
        cli_mod.app,
        ["reels", "ignored", "--preset", "middle-third-dynamic", "--out", str(out)],
    )
    assert result.exit_code != 0
    assert "nothing to cut" in result.output
    assert made == []
