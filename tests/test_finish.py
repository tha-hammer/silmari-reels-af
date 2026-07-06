"""B9 orchestration + pure ffmpeg composition for ``finish_reel``.

These tests exercise the *orchestration* (ordering, config threading, the
``raw`` opt-out) with injected fakes — no real ffmpeg, no whisper, no provider
— plus the pure filtergraph/command composition that stitches CobaltMeadow's
image-overlay graph (B8) together with SapphireBarn's combined caption+banner
ASS (B3/B4) into a single burn. The real-ffmpeg closure test (B9 gate) lives in
``test_finish_closure.py``.
"""

from __future__ import annotations

from pathlib import Path

from reel_af.render.finish import (
    FinishContext,
    FinishDeps,
    ReelFinishConfig,
    build_finish_ffmpeg_cmd,
    compose_finish_filtergraph,
    finish_reel,
)

# ── Pure composition ───────────────────────────────────────────────


def test_compose_passthrough_when_no_ass() -> None:
    fc, label = compose_finish_filtergraph("[0:v]null[v]", "[v]", ass_path=None)
    assert fc == "[0:v]null[v]"
    assert label == "[v]"


def test_compose_appends_ass_burn_as_last_stage() -> None:
    fc, label = compose_finish_filtergraph("[0:v]null[v]", "[v]", ass_path=Path("/w/f.ass"))
    # ASS burn consumes the overlay output label and produces the final label.
    assert fc == "[0:v]null[v];[v]ass=/w/f.ass[vout]"
    assert label == "[vout]"


def test_compose_escapes_ass_path_colons() -> None:
    fc, _ = compose_finish_filtergraph("[0:v]null[v]", "[v]", ass_path=Path("C:/w/f.ass"))
    assert r"ass=C\:/w/f.ass" in fc


def test_build_ffmpeg_cmd_base_then_looped_images_and_copy_audio() -> None:
    cfg = ReelFinishConfig()
    images = [Path("/img/0.jpg"), Path("/img/1.jpg")]
    cmd = build_finish_ffmpeg_cmd(
        base=Path("/reel/base.mp4"),
        image_paths=images,
        overlay_filter_complex="[0:v]setpts=PTS-STARTPTS[base];[base]format=yuv420p[v]",
        overlay_label="[v]",
        ass_path=Path("/reel/final.ass"),
        out_path=Path("/reel/final.mp4"),
        duration_s=20.0,
        cfg=cfg,
    )
    assert cmd[0] == "ffmpeg"
    # base is the first input; each still image is looped.
    assert cmd.index("/reel/base.mp4") < cmd.index("/img/0.jpg") < cmd.index("/img/1.jpg")
    assert cmd.count("-loop") == 2
    # video mapped from the composed label; audio copied straight through.
    assert "[vout]" in cmd
    assert "0:a?" in cmd
    assert "copy" in cmd
    # config-driven encode + bounded to base duration.
    assert str(cfg.encode_crf) in cmd
    assert cfg.encode_preset in cmd
    assert "20.000" in cmd
    assert cmd[-1] == "/reel/final.mp4"


def test_build_ffmpeg_cmd_zero_images_has_no_loop_inputs() -> None:
    cfg = ReelFinishConfig()
    cmd = build_finish_ffmpeg_cmd(
        base=Path("/reel/base.mp4"),
        image_paths=[],
        overlay_filter_complex="[0:v]null[v]",
        overlay_label="[v]",
        ass_path=Path("/reel/final.ass"),
        out_path=Path("/reel/final.mp4"),
        duration_s=12.0,
        cfg=cfg,
    )
    assert "-loop" not in cmd
    assert cmd.count("-i") == 1


# ── Orchestration (fakes) ──────────────────────────────────────────


class _FakeGraph:
    def __init__(self, n: int) -> None:
        self.filter_complex = "[0:v]null[v]" if n == 0 else "[0:v]setpts[base];...[v]"
        self.video_label = "[v]"
        self.image_input_count = n


class _FakeCutin:
    def __init__(self, path: Path) -> None:
        self.image_path = path


def _fake_deps(calls: list[str]) -> FinishDeps:
    async def generate_hook(transcript, provider):
        calls.append("hook")
        return "the hook line"

    def caption_words(reel_path, cfg):
        calls.append("words")
        return [(0.0, 0.5, "hello"), (0.5, 1.0, "world")]

    def build_finish_ass(words, hook, dur, cfg):
        calls.append("finish_ass")
        assert hook == "the hook line"
        return "[Script Info]\n..."

    def write_ass(text, path):
        calls.append("write_ass")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)
        return Path(path)

    async def pick_image_moments(transcript, provider, cfg, duration_s):
        calls.append(f"moments:{duration_s}")
        return [(3.0, 5.5, "prompt a"), (10.0, 12.5, "prompt b")]

    async def generate_image_cutins(provider, cut_ins, out_dir):
        calls.append(f"gen_images:{len(list(cut_ins))}")
        out = []
        for i, _c in enumerate(range(2)):
            p = Path(out_dir) / f"frame-{i}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\xff\xd8\xff")
            out.append(_FakeCutin(p))
        return out

    def build_overlay_graph(cut_ins, cfg):
        calls.append("overlay_graph")
        return _FakeGraph(len(list(cut_ins)))

    def image_paths(cut_ins):
        return [c.image_path for c in cut_ins]

    async def run_ffmpeg(cmd, timeout_s):
        calls.append("burn")

    return FinishDeps(
        caption_words=caption_words,
        build_finish_ass=build_finish_ass,
        write_ass=write_ass,
        generate_hook=generate_hook,
        pick_image_moments=pick_image_moments,
        generate_image_cutins=generate_image_cutins,
        build_overlay_graph=build_overlay_graph,
        image_paths_for_cutins=image_paths,
        run_ffmpeg=run_ffmpeg,
        probe_duration=lambda p: 20.0,
    )


async def test_finish_reel_runs_full_pipeline_in_order(tmp_path) -> None:
    base = tmp_path / "base.mp4"
    base.write_bytes(b"\x00")
    calls: list[str] = []
    ctx = FinishContext(source_url="http://x", transcript="t", provider=object())
    out = await finish_reel(
        base, ctx, ReelFinishConfig(), deps=_fake_deps(calls), out_dir=tmp_path
    )
    assert "hook" in calls and "words" in calls and "finish_ass" in calls
    assert "moments:20.0" in calls           # duration threaded into moment picking
    assert "gen_images:2" in calls
    assert "overlay_graph" in calls
    assert calls.count("burn") == 1
    assert calls[-1] == "burn"               # ffmpeg burn is the final step
    assert out == tmp_path / "final.mp4"


async def test_finish_reel_raw_opt_out_returns_base_untouched(tmp_path) -> None:
    base = tmp_path / "base.mp4"
    base.write_bytes(b"\x00")
    calls: list[str] = []
    ctx = FinishContext(source_url=None, transcript="t", provider=object())
    out = await finish_reel(
        base, ctx, ReelFinishConfig(), deps=_fake_deps(calls), raw=True, out_dir=tmp_path
    )
    assert out == base
    assert calls == []                       # nothing invoked on the fast/raw path


async def test_finish_reel_image_count_zero_skips_images_but_still_burns(tmp_path) -> None:
    base = tmp_path / "base.mp4"
    base.write_bytes(b"\x00")
    calls: list[str] = []
    ctx = FinishContext(source_url=None, transcript="t", provider=object())
    cfg = ReelFinishConfig(image_count=0)
    await finish_reel(base, ctx, cfg, deps=_fake_deps(calls), out_dir=tmp_path)
    assert not any(c.startswith("moments") for c in calls)
    assert not any(c.startswith("gen_images") for c in calls)
    # captions/banner still burned even with no cut-ins.
    assert "finish_ass" in calls
    assert calls.count("burn") == 1
