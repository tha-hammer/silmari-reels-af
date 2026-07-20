from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from reel_af import app as app_mod
from reel_af.planner import paths as paths_mod
from reel_af.planner.config import PlannerConfig, load_planner_config
from reel_af.planner.paths import (
    REEL_AF_OUTPUT_ROOT_ENV,
    evals_dir,
    resolve_output_root,
    runs_dir,
)

SRC = "https://www.youtube.com/watch?v=abc123"


def _cfg(**overrides) -> PlannerConfig:
    data = load_planner_config().model_dump()
    data.update(overrides)
    return PlannerConfig.model_validate(data)


def _fixed_uuid(hex_value: str):
    return lambda: SimpleNamespace(hex=hex_value)


def _patch_note(monkeypatch):
    monkeypatch.setattr(app_mod.app, "note", lambda *args, **kwargs: None)


def test_resolve_output_root_precedence(monkeypatch, tmp_path):
    cfg = _cfg(output_root=str(tmp_path / "config-root"))
    env_root = tmp_path / "env-root"
    explicit_root = tmp_path / "explicit-root"

    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    assert resolve_output_root(cfg=cfg) == tmp_path / "config-root"

    monkeypatch.setenv(REEL_AF_OUTPUT_ROOT_ENV, str(env_root))
    assert resolve_output_root(cfg=cfg) == env_root
    assert resolve_output_root(explicit_root, cfg=cfg) == explicit_root


def test_resolved_subdirs_use_project_relative_default_and_config(monkeypatch, tmp_path):
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)

    default_cfg = _cfg()
    assert resolve_output_root(cfg=default_cfg) == tmp_path / "resources"
    assert runs_dir("flow", "abc123", cfg=default_cfg) == (
        tmp_path / "resources" / "runs" / "flow-abc123"
    )
    assert evals_dir(cfg=default_cfg) == tmp_path / "resources" / "evals"

    custom_cfg = _cfg(output_root="custom-resources")
    assert resolve_output_root(cfg=custom_cfg) == tmp_path / "custom-resources"


def test_absolute_env_output_root_is_honored(monkeypatch, tmp_path):
    env_root = tmp_path / "volume" / "reel-af"
    cfg = _cfg(output_root="ignored-config-root")

    monkeypatch.setenv(REEL_AF_OUTPUT_ROOT_ENV, str(env_root))
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path / "project-root")

    assert resolve_output_root(cfg=cfg) == env_root
    assert evals_dir(cfg=cfg) == env_root / "evals"


async def test_transcript_to_plan_default_out_dir_uses_resources_runs(monkeypatch, tmp_path):
    from reel_af.planner import pipeline as pipeline_mod

    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", lambda: SimpleNamespace(hex="abc123456789ffff"))

    captured: list[Path] = []

    async def fake_plan(
        source_url,
        *,
        words,
        register,
        bounds,
        llm,
        out_dir,
    ):
        captured.append(Path(out_dir))
        return {
            "composite_ref": str(Path(out_dir) / "composite.ts.md"),
            "words_ref": str(Path(out_dir) / "transcript.words.json"),
            "hook_ref": str(Path(out_dir) / "hook-plan.json"),
        }

    monkeypatch.setattr(pipeline_mod, "plan", fake_plan)

    out = await app_mod.transcript_to_plan(SRC, transcribe=lambda source: object())

    expected = tmp_path / "resources" / "runs" / "transcript-to-plan-abc123456789"
    assert captured == [expected]
    assert out["composite_ref"] == str(expected / "composite.ts.md")


async def test_dsl_hooks_to_reels_default_out_dir_uses_resources_runs(monkeypatch, tmp_path):
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", lambda: SimpleNamespace(hex="feedfacecafeffff"))

    captured: dict[str, object] = {"resolve_dirs": []}

    def fake_resolve(ref, dest_dir, name, fetch):
        captured["resolve_dirs"].append(Path(dest_dir))
        return tmp_path / name

    def fake_download_segments(plan, out_dir, fetch_segment):
        captured["segments_dir"] = Path(out_dir)
        return []

    async def fake_stitch_footage_reel(plan, assets, out_dir, *, run_id):
        captured["base_dir"] = Path(out_dir)
        return Path(out_dir) / f"{run_id}.mp4"

    async def fake_finish_reel(base, ctx, cfg, *, out_dir, raw):
        captured["final_dir"] = Path(out_dir)
        return Path(out_dir) / "final.mp4"

    segment = SimpleNamespace(kind="source", text="hello world")
    plan = SimpleNamespace(segments=[segment], duration_s=4.2)
    compiled = SimpleNamespace(status="ok", plan=plan, diagnostics=[])

    def fake_load_hook_clip(hook_ref, clip_idx):
        return {
            "composite_ref": "composite.ts.md",
            "source_id": "abc123",
            "cut_ins": [],
            "hook": "Hook",
            "title": "Title",
        }

    monkeypatch.setattr(app_mod, "_resolve_artifact_ref", fake_resolve)
    monkeypatch.setattr(app_mod, "_load_hook_clip", fake_load_hook_clip)
    monkeypatch.setattr(app_mod, "read_composite_file", lambda path: object())
    monkeypatch.setattr(app_mod, "load_words", lambda path: object())
    monkeypatch.setattr(app_mod, "compile_composite", lambda *args, **kwargs: compiled)
    monkeypatch.setattr(app_mod, "validate_renderable", lambda plan: None)
    monkeypatch.setattr(app_mod, "map_cut_ins", lambda cut_ins, reel: ([], []))
    monkeypatch.setattr(app_mod, "download_segments", fake_download_segments)
    monkeypatch.setattr(app_mod, "stitch_footage_reel", fake_stitch_footage_reel)
    monkeypatch.setattr(app_mod, "finish_reel", fake_finish_reel)

    out = await app_mod.dsl_hooks_to_reels(
        SRC,
        "composite.ts.md",
        "transcript.words.json",
        "hook-plan.json",
        uploader=lambda *args, **kwargs: "https://bucket.example.com/final.mp4",
        text_provider=object(),
        image_provider=object(),
    )

    expected = tmp_path / "resources" / "runs" / "dsl-hooks-feedfacecafe"
    assert captured["resolve_dirs"] == [expected, expected, expected]
    assert captured["segments_dir"] == expected / "segments"
    assert captured["base_dir"] == expected / "base"
    assert captured["final_dir"] == expected / "final"
    assert out["download_url"] == "https://bucket.example.com/final.mp4"


async def test_article_to_reel_default_and_explicit_out_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", _fixed_uuid("a1b2c3d4ffffeeee"))
    _patch_note(monkeypatch)
    monkeypatch.setattr("reel_af.storage.upload_reel", lambda *args, **kwargs: None)

    async def fake_call(target, **kwargs):
        if target.endswith("reel_extract_essence"):
            return {
                "essence": {
                    "core_claim": "claim",
                    "mechanism": "mechanism",
                    "evidence": ["evidence"],
                    "content_mode": "general",
                    "domain": "science",
                }
            }
        if target.endswith("reel_compose_script"):
            return {"script": {"hook": "hook", "hook_variant": "variant"}}
        raise AssertionError(f"unexpected target {target}")

    captured: list[Path] = []

    async def fake_render_downstream(**kwargs):
        out_path = Path(kwargs["out_path"])
        captured.append(out_path)
        assert Path(kwargs["media_dir"]) == out_path / "media"
        return {"video_path": str(out_path / "reel.mp4")}

    monkeypatch.setattr(app_mod.app, "call", fake_call)
    monkeypatch.setattr(app_mod, "_render_downstream", fake_render_downstream)

    await app_mod.article_to_reel("https://example.com/article")
    explicit = tmp_path / "explicit-article"
    await app_mod.article_to_reel("https://example.com/article", out_dir=str(explicit))

    assert captured == [
        tmp_path / "resources" / "runs" / "article-a1b2c3d4",
        explicit,
    ]


async def test_topic_to_reel_default_and_explicit_out_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", _fixed_uuid("b2c3d4e5ffffeeee"))
    _patch_note(monkeypatch)
    monkeypatch.setattr("reel_af.storage.upload_reel", lambda *args, **kwargs: None)

    essence = {
        "core_claim": "claim",
        "mechanism": "mechanism",
        "evidence": ["evidence"],
        "domain": "science",
    }
    script = {
        "tease": "tease",
        "reveal": "First sentence. Second sentence.",
        "payoff": "payoff",
        "common_belief": "belief",
        "open_style": "curiosity",
        "narration": "narration",
    }

    async def fake_call(target, **kwargs):
        if target.endswith(
            (
                "reel_hunt_specific_figure",
                "reel_hunt_reversal",
                "reel_hunt_temporal",
                "reel_hunt_cross_domain",
            )
        ):
            return {"candidates": [essence]}
        if target.endswith("reel_pick_top_essences"):
            return {"chosen_essences": [essence, essence, essence]}
        if target.endswith("reel_write_narrations"):
            return {"scripts": [script, script, script]}
        if target.endswith("reel_pick_best_narration"):
            return {"winner_idx": 0, "composite_score": 1.0, "why": "ok"}
        raise AssertionError(f"unexpected target {target}")

    captured: list[Path] = []

    async def fake_render_downstream(**kwargs):
        out_path = Path(kwargs["out_path"])
        captured.append(out_path)
        assert Path(kwargs["media_dir"]) == out_path / "media"
        return {"video_path": str(out_path / "reel.mp4")}

    monkeypatch.setattr(app_mod.app, "call", fake_call)
    monkeypatch.setattr(app_mod, "_render_downstream", fake_render_downstream)

    await app_mod.topic_to_reel("black holes")
    explicit = tmp_path / "explicit-topic"
    await app_mod.topic_to_reel("black holes", out_dir=str(explicit))

    assert captured == [
        tmp_path / "resources" / "runs" / "topic-b2c3d4e5",
        explicit,
    ]


async def test_composite_to_reel_default_and_explicit_out_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", _fixed_uuid("c3d4e5f6ffffeeee"))
    _patch_note(monkeypatch)
    monkeypatch.setattr("reel_af.storage.upload_reel", lambda *args, **kwargs: None)

    captured: list[Path] = []

    def fake_run_composite_reels(**kwargs):
        out_path = Path(kwargs["out_path"])
        captured.append(out_path)
        return {"video_path": str(out_path / "reel01.mp4"), "reel_count": 1}

    monkeypatch.setattr(app_mod, "_run_composite_reels", fake_run_composite_reels)

    await app_mod.composite_to_reel("https://youtu.be/example")
    explicit = tmp_path / "explicit-composite"
    await app_mod.composite_to_reel("https://youtu.be/example", out_dir=str(explicit))

    assert captured == [
        tmp_path / "resources" / "runs" / "composite-c3d4e5f6",
        explicit,
    ]


async def test_research_to_reel_default_and_explicit_out_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", _fixed_uuid("d4e5f6a7ffffeeee"))
    _patch_note(monkeypatch)

    record = SimpleNamespace(
        result={"research_package": {"sections": [{"content": "Selected paragraph."}]}}
    )

    async def fake_renderer(**kwargs):
        out_path = Path(kwargs["out_path"])
        captured.append(out_path)
        assert Path(kwargs["media_dir"]) == out_path / "media"
        return {"video_path": str(out_path / "reel.mp4"), "duration_s": 12.0}

    captured: list[Path] = []
    selected = [{"paragraph_id": "0-0", "text": "Selected paragraph.", "position": 0}]

    await app_mod.research_to_reel(
        source_execution_id="exec-1",
        selected_paragraphs=selected,
        fetch_body=lambda _execution_id: record,
        distiller=lambda _text: {
            "core_claim": "claim",
            "mechanism": "mechanism",
            "evidence": ["evidence"],
            "content_mode": "general",
            "domain": "science",
        },
        composer=lambda _node, _essence: {"hook": "hook", "hook_variant": "variant"},
        renderer=fake_renderer,
        uploader=lambda *args, **kwargs: None,
    )
    explicit = tmp_path / "explicit-research"
    await app_mod.research_to_reel(
        source_execution_id="exec-1",
        selected_paragraphs=selected,
        out_dir=str(explicit),
        fetch_body=lambda _execution_id: record,
        distiller=lambda _text: {
            "core_claim": "claim",
            "mechanism": "mechanism",
            "evidence": ["evidence"],
            "content_mode": "general",
            "domain": "science",
        },
        composer=lambda _node, _essence: {"hook": "hook", "hook_variant": "variant"},
        renderer=fake_renderer,
        uploader=lambda *args, **kwargs: None,
    )

    assert captured == [
        tmp_path / "resources" / "runs" / "research-d4e5f6a7",
        explicit,
    ]


async def test_research_to_carousel_default_and_explicit_out_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_mod.uuid, "uuid4", _fixed_uuid("e5f6a7b8ffffeeee"))

    class Storage:
        async def put(self, *, run_id, idx, path):
            return f"stub://{run_id}/{idx}"

    captured: list[Path] = []

    async def fake_generate_frame(
        provider,
        prompt,
        idx,
        out_dir,
        content_mode,
        model=None,
        crop="4x5",
    ):
        out_path = Path(out_dir)
        captured.append(out_path)
        return out_path / f"frame-{idx:02d}.jpg"

    kwargs = {
        "text": "research text",
        "slide_count": 1,
        "provider": object(),
        "storage": Storage(),
        "distiller": lambda _text: app_mod.Essence(
            core_claim="claim",
            mechanism="mechanism",
            evidence=["evidence"],
            content_mode="general",
            domain="science",
        ),
        "prompt_planner": lambda _essence, count: ["slide prompt"] * count,
        "_generate_frame": fake_generate_frame,
    }

    out = await app_mod.research_to_carousel(**kwargs)
    explicit = tmp_path / "explicit-carousel"
    out_explicit = await app_mod.research_to_carousel(**kwargs, out_dir=str(explicit))

    expected = tmp_path / "resources" / "runs" / "carousel-e5f6a7b8"
    assert captured == [expected, explicit]
    assert out["out_dir"] == str(expected)
    assert out_explicit["out_dir"] == str(explicit)


def test_cli_composite_default_and_explicit_out_dir_use_resolver(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import reel_af.cli as cli_mod
    import reel_af.render.composite_pipeline as pipe

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)

    captured: list[Path] = []

    async def fake_pipeline(
        url,
        out_dir,
        *,
        text_provider=None,
        image_provider=None,
        cfg=None,
        raw=False,
        **kwargs,
    ):
        captured.append(Path(out_dir))
        return Path(out_dir) / "final.mp4"

    monkeypatch.setattr(pipe, "composite_to_reel", fake_pipeline)

    runner = CliRunner()
    result = runner.invoke(cli_mod.app, ["composite", "https://youtu.be/x", "--fast"])
    assert result.exit_code == 0, result.output

    explicit = tmp_path / "explicit-cli-composite"
    result = runner.invoke(
        cli_mod.app,
        ["composite", "https://youtu.be/x", "--fast", "--out", str(explicit)],
    )
    assert result.exit_code == 0, result.output

    assert captured == [
        tmp_path / "resources" / "runs" / "cli-composite-default",
        explicit,
    ]


def test_cli_reels_default_and_explicit_out_dir_use_resolver(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import reel_af.cli as cli_mod
    import reel_af.render.middle_third as middle_third

    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)

    src = tmp_path / "source.mp4"
    src.write_bytes(b"\x00")
    captured: list[Path] = []

    def fake_resolve_source(source, work):
        captured.append(Path(work))
        return src

    def fake_composite(source, t0, dur_s, seq, out, **kwargs):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out

    monkeypatch.setattr(cli_mod, "_resolve_source", fake_resolve_source)
    monkeypatch.setattr(cli_mod, "_resolve_words", lambda source, whisper_json, work: [])
    monkeypatch.setattr(cli_mod, "_ffprobe_duration", lambda source: 130.0)
    monkeypatch.setattr(middle_third, "window_segments", lambda *args, **kwargs: [])
    monkeypatch.setattr(middle_third, "render_overlay", lambda segments, tf, seq, cfg, **kwargs: seq)
    monkeypatch.setattr(middle_third, "composite_window", fake_composite)

    runner = CliRunner()
    result = runner.invoke(
        cli_mod.app,
        ["reels", "ignored", "--preset", "middle-third-dynamic", "--only", "1"],
    )
    assert result.exit_code == 0, result.output

    explicit = tmp_path / "explicit-cli-reels"
    result = runner.invoke(
        cli_mod.app,
        [
            "reels",
            "ignored",
            "--preset",
            "middle-third-dynamic",
            "--only",
            "1",
            "--out",
            str(explicit),
        ],
    )
    assert result.exit_code == 0, result.output

    assert captured == [
        tmp_path / "resources" / "runs" / "cli-reels-middle-third-dynamic",
        explicit,
    ]


def test_script_output_resolver_uses_shared_env_root(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env[REEL_AF_OUTPUT_ROOT_ENV] = str(tmp_path / "env-root")

    proc = subprocess.run(
        [sys.executable, "scripts/resolve_output_dir.py", "batch", "science"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert Path(proc.stdout.strip()) == tmp_path / "env-root" / "runs" / "batch-science"
