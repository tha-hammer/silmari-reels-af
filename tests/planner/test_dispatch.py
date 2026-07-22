from __future__ import annotations

import json

import pytest

from reel_af.planner.dispatch import (
    HOOKS_TARGET,
    build_dsl_hook_dispatches,
    dispatch_dsl_hook_clips,
    load_hook_plan_for_dispatch,
)

SOURCE_URL = "https://media.example/video.mp4"
WORDS_REF = "https://cdn.example/a1/source/transcript.words.json"
HOOK_REF = "https://cdn.example/a1/source/hook-plan.json"


def _hook_plan() -> dict:
    return {
        "schema_version": "1",
        "source_url": SOURCE_URL,
        "clips": [
            {
                "idx": 1,
                "target": HOOKS_TARGET,
                "composite_ref": "https://cdn.example/a1/source/clips/clip-001/composite.ts.md",
                "idempotency_key": "clip-one-key",
            },
            {
                "idx": 2,
                "target": HOOKS_TARGET,
                "composite_ref": "https://cdn.example/a1/source/clips/clip-002/composite.ts.md",
                "idempotency_key": "clip-two-key",
            },
        ],
    }


def test_build_dsl_hook_dispatch_inputs_uses_each_clip_composite_ref():
    dispatches = build_dsl_hook_dispatches(
        source_url=SOURCE_URL,
        words_ref=WORDS_REF,
        hook_ref=HOOK_REF,
        hook_plan=_hook_plan(),
    )

    assert [item["idx"] for item in dispatches] == [1, 2]
    assert [item["idempotency_key"] for item in dispatches] == [
        "clip-one-key",
        "clip-two-key",
    ]
    assert {item["target"] for item in dispatches} == {HOOKS_TARGET}
    assert [item["cp_input"]["composite_ref"] for item in dispatches] == [
        "https://cdn.example/a1/source/clips/clip-001/composite.ts.md",
        "https://cdn.example/a1/source/clips/clip-002/composite.ts.md",
    ]
    assert [item["cp_input"]["clip_idx"] for item in dispatches] == [1, 2]
    assert [set(item["cp_input"]) for item in dispatches] == [
        {"source_url", "composite_ref", "words_ref", "hook_ref", "clip_idx"},
        {"source_url", "composite_ref", "words_ref", "hook_ref", "clip_idx"},
    ]
    assert all("input" not in item["cp_input"] for item in dispatches)
    assert all("clips" not in item["cp_input"] for item in dispatches)
    assert all("clip_indices" not in item["cp_input"] for item in dispatches)


def test_dispatch_dsl_hook_clips_wraps_cp_input_exactly_once():
    calls: list[tuple[str, dict, dict]] = []

    def dispatch_async(target: str, body: dict, metadata: dict) -> str:
        calls.append((target, body, metadata))
        return f"exec-{metadata['idx']}"

    summary = dispatch_dsl_hook_clips(
        source_url=SOURCE_URL,
        words_ref=WORDS_REF,
        hook_ref=HOOK_REF,
        hook_plan=_hook_plan(),
        dispatch_async=dispatch_async,
    )

    assert [item["execution_id"] for item in summary["clip_dispatches"]] == [
        "exec-1",
        "exec-2",
    ]
    assert [metadata["idempotency_key"] for _, _, metadata in calls] == [
        "clip-one-key",
        "clip-two-key",
    ]
    for target, body, _metadata in calls:
        assert target == HOOKS_TARGET
        assert sorted(body) == ["input"]
        assert "input" not in body["input"]
        assert body["input"]["hook_ref"] == HOOK_REF
        assert body["input"]["words_ref"] == WORDS_REF


def test_dispatch_dsl_hook_clips_calls_renderer_once_per_clip():
    calls: list[tuple[str, dict, dict]] = []

    def dispatch_async(target: str, body: dict, metadata: dict) -> str:
        calls.append((target, body, metadata))
        return f"exec-{len(calls)}"

    summary = dispatch_dsl_hook_clips(
        source_url=SOURCE_URL,
        words_ref=WORDS_REF,
        hook_ref=HOOK_REF,
        hook_plan=_hook_plan(),
        dispatch_async=dispatch_async,
    )

    assert [target for target, _body, _metadata in calls] == [HOOKS_TARGET, HOOKS_TARGET]
    assert [body["input"]["clip_idx"] for _target, body, _metadata in calls] == [1, 2]
    assert all("clips" not in body["input"] for _target, body, _metadata in calls)
    assert all("clip_indices" not in body["input"] for _target, body, _metadata in calls)
    assert summary == {
        "clip_dispatches": [
            {"idx": 1, "idempotency_key": "clip-one-key", "execution_id": "exec-1"},
            {"idx": 2, "idempotency_key": "clip-two-key", "execution_id": "exec-2"},
        ]
    }


def test_load_hook_plan_for_dispatch_accepts_dict_local_file_and_https(tmp_path):
    hook_plan = _hook_plan()
    local = tmp_path / "hook-plan.json"
    local.write_text(json.dumps(hook_plan))

    assert load_hook_plan_for_dispatch(hook_plan) is hook_plan
    assert load_hook_plan_for_dispatch(str(local)) == hook_plan

    fetched: list[str] = []

    def fetch_bytes(url: str) -> bytes:
        fetched.append(url)
        return json.dumps(hook_plan).encode("utf-8")

    assert load_hook_plan_for_dispatch(HOOK_REF, fetch_bytes=fetch_bytes) == hook_plan
    assert fetched == [HOOK_REF]


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda clip: clip.update({"idx": 0}), "idx"),
        (lambda clip: clip.update({"idx": True}), "idx"),
        (lambda clip: clip.update({"target": "reel-af.batch_render"}), "target"),
        (lambda clip: clip.update({"composite_ref": ""}), "composite_ref"),
        (lambda clip: clip.update({"composite_ref": "/tmp/local/composite.ts.md"}), "hosted"),
        (lambda clip: clip.pop("idempotency_key"), "idempotency_key"),
    ],
)
def test_build_dsl_hook_dispatches_rejects_unsafe_clip_payloads(mutator, message):
    hook_plan = _hook_plan()
    mutator(hook_plan["clips"][0])

    with pytest.raises(ValueError, match=message):
        build_dsl_hook_dispatches(
            source_url=SOURCE_URL,
            words_ref=WORDS_REF,
            hook_ref=HOOK_REF,
            hook_plan=hook_plan,
        )


def test_ingest_source_dispatches_stage_two_once_per_hook_clip(monkeypatch):
    from scripts import ingest_source

    hook_plan = _hook_plan()
    posts: list[tuple[str, dict]] = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, execution_id: str) -> None:
            self._execution_id = execution_id

        def json(self) -> dict:
            return {"execution_id": self._execution_id}

    class Client:
        def post(self, path: str, json: dict) -> Response:
            posts.append((path, json))
            return Response(f"render-{len(posts)}")

    def poll(_client, exec_id: str, label: str, _timeout_s: int) -> dict:
        return {
            "status": "succeeded",
            "result": {"download_url": f"https://cdn.example/{label}-{exec_id}.mp4"},
        }

    monkeypatch.setattr(ingest_source, "poll", poll)

    renders = ingest_source.dispatch_render_clips(
        Client(),
        source_url=SOURCE_URL,
        words_ref=WORDS_REF,
        hook_ref=HOOK_REF,
        hook_plan=hook_plan,
        clip_idx=None,
        timeout_s=10,
    )

    assert [item["idx"] for item in renders] == [1, 2]
    assert [path for path, _body in posts] == [
        f"/api/v1/execute/async/{HOOKS_TARGET}",
        f"/api/v1/execute/async/{HOOKS_TARGET}",
    ]
    assert [body["input"]["clip_idx"] for _path, body in posts] == [1, 2]
    assert [body["input"]["composite_ref"] for _path, body in posts] == [
        "https://cdn.example/a1/source/clips/clip-001/composite.ts.md",
        "https://cdn.example/a1/source/clips/clip-002/composite.ts.md",
    ]
    assert all(sorted(body) == ["input"] for _path, body in posts)
    assert all("input" not in body["input"] for _path, body in posts)
