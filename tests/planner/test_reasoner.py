from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from reel_af import app as app_mod
from reel_af import storage as storage_mod
from reel_af.app import transcript_to_plan
from reel_af.dsl.compile import load_words
from reel_af.planner.models import (
    Beat,
    BeatRole,
    CandidateSpan,
    CtaHardness,
    CtaPlan,
    EngagementKind,
    Hook,
    HookType,
    Interrupt,
    InterruptKind,
    LoopPlan,
    ReelBlueprint,
    ScriptCoherenceFixAction,
    ScriptCoherenceReport,
    ScriptTransitionReview,
    ScriptTransitionVerdict,
    Template,
    XfadeEffect,
)
from tests.planner.factories import arc_plan, duration_policy, duration_range

SRC = "https://www.youtube.com/watch?v=abc123"
BUCKET = "reel-uploads-test"
RUN_ID = "abc123456789"
FIXTURES = Path(__file__).resolve().parents[1] / "dsl" / "fixtures"
SOURCE_QUOTE = (
    "They don't reason. They pattern-match at a scale that feels like reasoning. Right. "
    "And the moment you trust the feeling, you ship the bug. Anyway, "
    "So the fix isn't a smarter model. It's a tighter loop. "
    "A loop you can actually see closing."
)


class _FakePlannerLLM:
    async def mine(self, transcript, register):
        return [
            CandidateSpan(
                quote=SOURCE_QUOTE,
                approx_start_s=4.12,
                approx_end_s=81.16,
                value_score=0.9,
                emotion="skepticism",
                is_claim=True,
                payoff_worthy=True,
            )
        ]

    async def strategize(self, transcript, candidates, policy):
        return None

    async def arrange(self, candidates, strategy, *, candidate_contexts=None, repair_hint=None):
        return ReelBlueprint(
            template_=Template.HookContextValuePayoffCta,
            duration_range_s=duration_range(min_s=18.0, max_s=42.0),
            duration_policy=duration_policy(),
            arc=arc_plan(required_candidate_ids=("c001",)),
            hook=Hook(
                type=HookType.CuriosityGap,
                banner_line="They don't reason.",
                span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
                candidate_id="c001",
                occurrence_index=0,
            ),
            beats=[
                Beat(
                    role=BeatRole.Hook,
                    span_quote="They don't reason. They pattern-match at a scale that feels like reasoning.",
                    candidate_id="c001",
                    occurrence_index=0,
                    max_len_s=4.5,
                    rationale="the hook establishes the premise before the payoff",
                    interrupt_out=Interrupt(
                        kind=InterruptKind.Trans,
                        effect=XfadeEffect.Dissolve,
                        dur_s=0.8,
                    ),
                ),
                Beat(
                    role=BeatRole.Payoff,
                    span_quote="A loop you can actually see closing.",
                    candidate_id="c001",
                    occurrence_index=0,
                    max_len_s=3.0,
                    rationale="the payoff closes the loop promised by the hook",
                ),
            ],
            loop=LoopPlan(
                strategy="tie_final_to_hook",
                final_span_quote="A loop you can actually see closing.",
                candidate_id="c001",
                occurrence_index=0,
            ),
            engagement_primary=EngagementKind.Send,
            cta=CtaPlan(hardness=CtaHardness.Soft, placements=["end"]),
            completion_rationale=(
                "The hook establishes the promise, proof explains the mechanism, "
                "payoff resolves the hook, and loop echoes it."
            ),
            rationale="the short script moves from premise to payoff on one thread",
        )

    async def check_script_coherence(
        self,
        blueprint,
        script_beats,
        transitions,
        strategy,
        candidate_contexts,
        *,
        repair_hint=None,
    ):
        return ScriptCoherenceReport(
            coherent=True,
            transitions=[
                ScriptTransitionReview(
                    transition_index=transition.index,
                    from_beat_index=transition.from_beat_index,
                    to_beat_index=transition.to_beat_index,
                    verdict=ScriptTransitionVerdict.Coherent,
                    fix_action=ScriptCoherenceFixAction.Keep,
                    why_present=True,
                    rationale="the payoff follows from the hook in the resolved script",
                    missing_why=None,
                    suggested_bridge_candidate_ids=[],
                    suggested_repair=None,
                )
                for transition in transitions
            ],
            overall_rationale="the assembled script reads as one coherent local thread",
            repair_hint=None,
        )


def _fake_transcribe(source):
    return load_words(FIXTURES / "source.words.json")


class _FakeA1S3:
    def __init__(
        self,
        *,
        fail_put_key: str | None = None,
        fail_presign_key: str | None = None,
        url_by_key: dict[str, str] | None = None,
    ):
        self.fail_put_key = fail_put_key
        self.fail_presign_key = fail_presign_key
        self.url_by_key = url_by_key or {}
        self.puts: list[dict] = []
        self.bodies_by_key: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803
        if Key == self.fail_put_key:
            raise OSError("put_object failed")
        body = bytes(Body)
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": body})
        self.bodies_by_key[Key] = body

    def generate_presigned_url(self, operation, Params, ExpiresIn):  # noqa: N803
        assert operation == "get_object"
        key = Params["Key"]
        if key == self.fail_presign_key:
            raise OSError("presign failed")
        return self.url_by_key.get(
            key,
            f"https://s3.example/{Params['Bucket']}/{key}?X-Amz-Expires={ExpiresIn}",
        )


def _write_fake_artifacts(
    out_dir: Path,
    *,
    missing: str | None = None,
    clip_count: int = 1,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    composite = out_dir / "composite.ts.md"
    composite2 = out_dir / "clips" / "clip-002" / "composite.ts.md"
    words = out_dir / "transcript.words.json"
    hook = out_dir / "hook-plan.json"
    if missing != "composite_ref":
        composite.write_text("00:00:04.120  They don't reason.\n", encoding="utf-8")
        if clip_count > 1:
            composite2.parent.mkdir(parents=True)
            composite2.write_text(
                "00:01:12.300  So the fix is a tighter loop.\n",
                encoding="utf-8",
            )
    if missing != "words_ref":
        words.write_text('{"schema_version":"1","words":[]}', encoding="utf-8")
    if missing != "hook_ref":
        clips = [
            {
                "idx": 1,
                "composite_ref": str(composite),
                "idempotency_key": "immutable-key-1" if clip_count > 1 else "immutable-key",
            }
        ]
        if clip_count > 1:
            clips.append(
                {
                    "idx": 2,
                    "composite_ref": str(composite2),
                    "idempotency_key": "immutable-key-2",
                }
            )
        hook.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "clips": clips,
                }
            ),
            encoding="utf-8",
        )
    sidecar = out_dir / "strategy.json"
    sidecar.write_text('{"debug":true}', encoding="utf-8")
    return {
        "composite_ref": str(composite),
        "words_ref": str(words),
        "hook_ref": str(hook),
        "strategy_ref": str(sidecar),
        "clip_count": clip_count,
    }


def _hosted(ref: str) -> bool:
    parsed = urlparse(ref)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def test_transcript_to_plan_returns_triple(tmp_path, monkeypatch):
    monkeypatch.delenv("REEL_BUCKET_NAME", raising=False)
    out = await transcript_to_plan(
        SRC,
        register="educational",
        llm=_FakePlannerLLM(),
        transcribe=_fake_transcribe,
        out_dir=str(tmp_path),
    )

    assert set(out) >= {"composite_ref", "words_ref", "hook_ref"}


async def test_transcript_to_plan_default_writer_publishes_when_bucket_configured(
    tmp_path, monkeypatch
):
    from reel_af.planner import pipeline as pipeline_mod

    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(app_mod.uuid, "uuid4", lambda: SimpleNamespace(hex=f"{RUN_ID}ffff"))
    s3 = _FakeA1S3()
    monkeypatch.setattr(storage_mod, "_client", lambda client_factory=None: s3)

    async def fake_plan(source_url, *, words, register, bounds, llm, out_dir, clip_count):
        assert clip_count == 1
        return _write_fake_artifacts(Path(out_dir))

    monkeypatch.setattr(pipeline_mod, "plan", fake_plan)

    out = await transcript_to_plan(
        SRC,
        transcribe=lambda source: object(),
        out_dir=str(tmp_path / "producer"),
    )

    assert _hosted(out["composite_ref"])
    assert _hosted(out["words_ref"])
    assert _hosted(out["hook_ref"])
    assert [put["Key"] for put in s3.puts] == [
        f"plans/{RUN_ID}/composite.ts.md",
        f"plans/{RUN_ID}/transcript.words.json",
        f"plans/{RUN_ID}/hook-plan.json",
    ]
    assert "strategy_ref" not in out
    assert out["clip_count"] == 1
    assert str(tmp_path) not in json.dumps(out)
    uploaded_hook = json.loads(s3.bodies_by_key[f"plans/{RUN_ID}/hook-plan.json"].decode())
    assert uploaded_hook["clips"][0]["composite_ref"] == out["composite_ref"]
    assert uploaded_hook["clips"][0]["idempotency_key"] == "immutable-key"
    assert str(tmp_path) not in json.dumps(uploaded_hook)


async def test_transcript_to_plan_publishes_multi_clip_hook_plan(tmp_path, monkeypatch):
    from reel_af.planner import pipeline as pipeline_mod

    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(app_mod.uuid, "uuid4", lambda: SimpleNamespace(hex=f"{RUN_ID}ffff"))
    s3 = _FakeA1S3()
    monkeypatch.setattr(storage_mod, "_client", lambda client_factory=None: s3)

    async def fake_plan(source_url, *, words, register, bounds, llm, out_dir, clip_count):
        assert clip_count == 2
        return _write_fake_artifacts(Path(out_dir), clip_count=2)

    monkeypatch.setattr(pipeline_mod, "plan", fake_plan)

    out = await transcript_to_plan(
        SRC,
        clip_count=2,
        transcribe=lambda source: object(),
        out_dir=str(tmp_path / "producer"),
    )

    assert out["clip_count"] == 2
    assert _hosted(out["composite_ref"])
    assert _hosted(out["words_ref"])
    assert _hosted(out["hook_ref"])
    assert [put["Key"] for put in s3.puts] == [
        f"plans/{RUN_ID}/composite.ts.md",
        f"plans/{RUN_ID}/clips/clip-002/composite.ts.md",
        f"plans/{RUN_ID}/transcript.words.json",
        f"plans/{RUN_ID}/hook-plan.json",
    ]
    uploaded_hook = json.loads(s3.bodies_by_key[f"plans/{RUN_ID}/hook-plan.json"].decode())
    clips = uploaded_hook["clips"]
    assert [clip["idx"] for clip in clips] == [1, 2]
    assert [clip["idempotency_key"] for clip in clips] == [
        "immutable-key-1",
        "immutable-key-2",
    ]
    assert clips[0]["composite_ref"] == out["composite_ref"]
    assert clips[0]["composite_ref"] != clips[1]["composite_ref"]
    assert str(tmp_path) not in json.dumps(out)
    assert str(tmp_path) not in json.dumps(uploaded_hook)


@pytest.mark.parametrize("bad_clip_count", [0, -1, True, False, "2", 1.5, None])
async def test_transcript_to_plan_rejects_invalid_clip_count_before_planning(
    tmp_path, bad_clip_count
):
    calls = {"transcribe": 0, "writer": 0}

    def fake_transcribe(source):
        calls["transcribe"] += 1
        raise AssertionError("transcribe must not run for invalid clip_count")

    def fake_writer(result):
        calls["writer"] += 1
        raise AssertionError("writer must not run for invalid clip_count")

    out = await transcript_to_plan(
        SRC,
        clip_count=bad_clip_count,
        transcribe=fake_transcribe,
        artifact_writer=fake_writer,
        out_dir=str(tmp_path / "producer"),
    )

    assert out["error"] == "invalid_clip_count"
    assert calls == {"transcribe": 0, "writer": 0}
    assert not (tmp_path / "producer").exists()


async def test_transcript_to_plan_omitted_clip_count_defaults_to_one(tmp_path, monkeypatch):
    from reel_af.planner import pipeline as pipeline_mod

    observed = {}

    async def fake_plan(source_url, *, words, register, bounds, llm, out_dir, clip_count):
        observed["clip_count"] = clip_count
        return _write_fake_artifacts(Path(out_dir))

    monkeypatch.setattr(pipeline_mod, "plan", fake_plan)

    out = await transcript_to_plan(
        SRC,
        transcribe=lambda source: object(),
        out_dir=str(tmp_path / "producer"),
    )

    assert out["clip_count"] == 1
    assert observed["clip_count"] == 1


@pytest.mark.parametrize(
    ("missing", "fail_put_key", "fail_presign_key", "url_by_key"),
    [
        ("composite_ref", None, None, None),
        (None, f"plans/{RUN_ID}/transcript.words.json", None, None),
        (None, None, f"plans/{RUN_ID}/composite.ts.md", None),
        (None, None, None, {f"plans/{RUN_ID}/composite.ts.md": "https://"}),
    ],
)
async def test_transcript_to_plan_publication_failures_map_to_artifact_unavailable(
    tmp_path, monkeypatch, missing, fail_put_key, fail_presign_key, url_by_key
):
    from reel_af.planner import pipeline as pipeline_mod

    monkeypatch.setenv("REEL_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(app_mod.uuid, "uuid4", lambda: SimpleNamespace(hex=f"{RUN_ID}ffff"))
    s3 = _FakeA1S3(
        fail_put_key=fail_put_key,
        fail_presign_key=fail_presign_key,
        url_by_key=url_by_key,
    )
    monkeypatch.setattr(storage_mod, "_client", lambda client_factory=None: s3)

    async def fake_plan(source_url, *, words, register, bounds, llm, out_dir, clip_count):
        assert clip_count == 1
        return _write_fake_artifacts(Path(out_dir), missing=missing)

    monkeypatch.setattr(pipeline_mod, "plan", fake_plan)

    out = await transcript_to_plan(
        SRC,
        transcribe=lambda source: object(),
        out_dir=str(tmp_path / "producer"),
    )

    assert out["error"] == "dsl_artifact_unavailable"
    assert str(tmp_path) not in json.dumps(out)


async def test_transcript_to_plan_rejects_non_http():
    out = await transcript_to_plan("file:///etc/passwd", llm=None, transcribe=None)

    assert out["error"] == "invalid_source_url"


def test_transcript_to_plan_is_registered_on_served_app():
    assert "reel_transcript_to_plan" in app_mod.app._reasoner_registry
