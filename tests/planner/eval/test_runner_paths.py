from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from reel_af.planner import paths as paths_mod
from reel_af.planner.eval import runner
from reel_af.planner.paths import REEL_AF_OUTPUT_ROOT_ENV


def test_score_artifacts_cli_defaults_to_resolved_evals_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)

    result = SimpleNamespace(aggregate_score=2.0)
    captured: dict[str, Path] = {}

    monkeypatch.setattr(runner, "score_artifact_dir", lambda *args, **kwargs: result)

    def fake_write_eval_result(result, out_dir):
        captured["out_dir"] = Path(out_dir)
        return Path(out_dir) / "result.json"

    monkeypatch.setattr(runner, "write_eval_result", fake_write_eval_result)

    runner.main(["score-artifacts", "fixtures/BASELINE-0"])

    payload = json.loads(capsys.readouterr().out)
    assert captured["out_dir"] == tmp_path / "resources" / "evals"
    assert payload["result_path"] == str(captured["out_dir"] / "result.json")


def test_diff_cli_defaults_to_file_under_resolved_evals_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(REEL_AF_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "_PROJECT_ROOT", tmp_path)

    diff = SimpleNamespace(aggregate_delta=1.0)
    captured: dict[str, Path] = {}

    monkeypatch.setattr(runner, "diff_eval_runs", lambda left, right: diff)

    def fake_write_eval_diff(diff, out_path):
        captured["out_path"] = Path(out_path)
        return Path(out_path)

    monkeypatch.setattr(runner, "write_eval_diff", fake_write_eval_diff)

    runner.main(["diff", "left result.json", "right.json"])

    payload = json.loads(capsys.readouterr().out)
    assert captured["out_path"].parent == tmp_path / "resources" / "evals"
    assert captured["out_path"].name == "left-result__right.json"
    assert payload["diff_path"] == str(captured["out_path"])
