from __future__ import annotations

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
