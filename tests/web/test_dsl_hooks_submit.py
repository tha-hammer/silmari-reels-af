"""B10-B13 → TARGET_DSL_HOOKS allowlist, canonicalization, and fail-closed submit.

Every rejection must land BEFORE any DB row or CP dispatch. The proof is the
established pattern from tests/web/test_submit.py: `repo.inserted == []` and
`cp.dispatch_calls == []` after the response.

Effect ordering preserved (server.py): identity.resolve -> authorize_create ->
build_submission (forbidden identity + canonicalization) -> _resolve_cp_input ->
insert_or_get_queued -> dispatch_async -> attach_execution_id. build_submission is
called structurally before the row, so these rejects cannot leak a row.
"""

from __future__ import annotations

import pytest
import server
from conftest import FakeControlPlane, FakeIdentity, FakeReelJobRepo, make_ctx, make_deps
from deps import BadRequest, Unauthorized
from reel_jobs import (
    ALLOWLISTED_TARGETS,
    DSL_HOOKS_ALLOWED_INPUT_KEYS,
    DSL_HOOKS_FINISH_OVERRIDE_KEYS,
    DSL_HOOKS_SOURCE_MODE,
    FORBIDDEN_IDENTITY_FIELDS,
    TARGET_ARTICLE,
    TARGET_DSL_HOOKS,
    TARGET_TOPIC,
    build_submission,
)

A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"
COMPOSITE_REF = "a1://runs/20260715T093000Z-abc123-7f3a9c/composite.ts.md"
WORDS_REF = "a1://runs/20260715T093000Z-abc123-7f3a9c/transcript.words.json"
HOOK_REF = "a1://runs/20260715T093000Z-abc123-7f3a9c/hook-plan.json"

DSL_HOOKS_URL = f"/api/v1/execute/async/{TARGET_DSL_HOOKS}"

VALID_A1_INPUT = {
    "source_url": A1_SOURCE_URL,
    "composite_ref": COMPOSITE_REF,
    "words_ref": WORDS_REF,
    "hook_ref": HOOK_REF,
    "clip_idx": 1,
}


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _deps(**kw):
    return make_deps(identity=FakeIdentity(make_ctx()), **kw)


def _assert_no_row_no_cp(repo, cp):
    assert repo.inserted == []
    assert cp.dispatch_calls == []


# ── B10: allowlist + canonicalization ──────────────────────────────


def test_dsl_hooks_target_is_allowlisted():
    assert TARGET_DSL_HOOKS in ALLOWLISTED_TARGETS
    assert TARGET_DSL_HOOKS == "reel-af.reel_dsl_hooks_to_reels"


def test_dsl_hooks_target_is_canonicalized():
    sub = build_submission(TARGET_DSL_HOOKS, {"input": VALID_A1_INPUT})

    assert sub.target == TARGET_DSL_HOOKS
    assert sub.source_url == A1_SOURCE_URL
    assert sub.topic is None
    assert sub.source_handle is None
    assert sub.params == {
        "target": TARGET_DSL_HOOKS,
        "source_mode": DSL_HOOKS_SOURCE_MODE,
        "clip_idx": 1,
    }


def test_clip_idx_defaults_and_validates():
    sub = build_submission(
        TARGET_DSL_HOOKS, {"input": {k: v for k, v in VALID_A1_INPUT.items() if k != "clip_idx"}}
    )
    assert sub.params["clip_idx"] == 1

    for bad in ["x", 0, -1, 1.5, None]:
        with pytest.raises(BadRequest):
            build_submission(TARGET_DSL_HOOKS, {"input": {**VALID_A1_INPUT, "clip_idx": bad}})


@pytest.mark.parametrize("missing", ["source_url", "composite_ref", "words_ref", "hook_ref"])
def test_missing_required_artifact_ref_is_rejected(missing):
    body = {k: v for k, v in VALID_A1_INPUT.items() if k != missing}
    with pytest.raises(BadRequest):
        build_submission(TARGET_DSL_HOOKS, {"input": body})


def test_unknown_input_key_is_rejected():
    with pytest.raises(BadRequest) as exc:
        build_submission(TARGET_DSL_HOOKS, {"input": {**VALID_A1_INPUT, "bogus": "x"}})
    assert exc.value.code == "unsupported_input_field"


# ── B11: identity-free cp_input ────────────────────────────────────


def test_cp_input_carries_exactly_the_artifact_refs():
    sub = build_submission(TARGET_DSL_HOOKS, {"input": VALID_A1_INPUT})

    assert sub.cp_input == {
        "source_url": A1_SOURCE_URL,
        "composite_ref": COMPOSITE_REF,
        "words_ref": WORDS_REF,
        "hook_ref": HOOK_REF,
        "clip_idx": 1,
    }


def test_dispatch_body_is_identity_free_and_metadata_free():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, "client_request_id": "crid-1"}}
    )

    assert resp.status_code == 202
    (call,) = cp.dispatch_calls
    target, body = call[0], call[1]
    assert target == TARGET_DSL_HOOKS
    assert set(body["input"]) == {
        "source_url", "composite_ref", "words_ref", "hook_ref", "clip_idx"
    }
    assert not (set(body["input"]) & FORBIDDEN_IDENTITY_FIELDS)
    assert "client_request_id" not in body["input"]


def test_allowlisted_finish_overrides_ride_through():
    sub = build_submission(
        TARGET_DSL_HOOKS, {"input": {**VALID_A1_INPUT, "overrides": {"font_scale": 1.2}}}
    )
    assert sub.cp_input["overrides"] == {"font_scale": 1.2}


def test_unknown_override_is_rejected():
    with pytest.raises(BadRequest):
        build_submission(
            TARGET_DSL_HOOKS, {"input": {**VALID_A1_INPUT, "overrides": {"bogus": 1}}}
        )


def test_empty_overrides_are_omitted_from_cp_input():
    sub = build_submission(TARGET_DSL_HOOKS, {"input": {**VALID_A1_INPUT, "overrides": {}}})
    assert "overrides" not in sub.cp_input


# ── B12: filesystem paths + forbidden identity, before row/CP ──────


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "file:///etc/passwd", "../../secret", "s3://bucket/key",
     "gs://bucket/key", "", "   ", "https://", "not-a-url", "ftp://host/x"],
)
def test_non_http_source_url_rejected_before_row_and_cp(bad):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, "source_url": bad}}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


@pytest.mark.parametrize("ref_key", ["composite_ref", "words_ref", "hook_ref"])
@pytest.mark.parametrize(
    "bad", ["/etc/passwd", "file:///etc/passwd", "../../secret", "/tmp/x.ts.md", "~/x.ts.md"]
)
def test_filesystem_artifact_ref_rejected_before_row_and_cp(ref_key, bad):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, ref_key: bad}}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


@pytest.mark.parametrize("field", sorted(FORBIDDEN_IDENTITY_FIELDS))
def test_forbidden_identity_rejected_top_level(field):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": VALID_A1_INPUT, field: "x"}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


@pytest.mark.parametrize("field", sorted(FORBIDDEN_IDENTITY_FIELDS))
def test_forbidden_identity_rejected_under_input(field):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, field: "x"}}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


def test_unauthenticated_dsl_hooks_submit_is_401_no_row_no_cp():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    deps = make_deps(
        identity=FakeIdentity(error=Unauthorized("no session")), reel_jobs=repo, control_plane=cp
    )
    resp = _client(deps).post(DSL_HOOKS_URL, json={"input": VALID_A1_INPUT})

    assert resp.status_code == 401
    _assert_no_row_no_cp(repo, cp)


# ── B13: fail closed against article/topic/clip-plan ───────────────


def test_article_target_is_still_not_allowlisted():
    assert TARGET_ARTICLE not in ALLOWLISTED_TARGETS


@pytest.mark.parametrize("target", ["reel-af.reel_article_to_reel", "reel-af.reel_dsl_hooks"])
def test_non_target_targets_rejected_before_row_and_cp(target):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        f"/api/v1/execute/async/{target}", json={"input": VALID_A1_INPUT}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


@pytest.mark.parametrize(
    "extra",
    [
        {"topic": "black holes"},
        {"source": "upload-handle-abc"},
        {"url": "https://example.com/article"},
        {"clip_plan": "clip-plan.json"},
        {"text": "some research text"},
        {"preset": "middle-third-dynamic"},
        {"count": 3},
    ],
)
def test_non_target_input_shapes_rejected_before_row_and_cp(extra):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, **extra}}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


@pytest.mark.parametrize(
    "seed_url",
    [
        "https://www.youtube.com/watch?v=abc123&t=90&reel_end=142",
        "https://www.youtube.com/watch?v=abc123&t=90",
        "https://youtu.be/abc123?t=90&reel_end=142",
    ],
)
def test_scoped_article_seed_url_rejected(seed_url):
    """The historical deterministic clip-plan seed shape is not a DSL-hooks input."""
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        DSL_HOOKS_URL, json={"input": {**VALID_A1_INPUT, "source_url": seed_url}}
    )

    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


def test_topic_target_still_works_unchanged():
    """The new branch must not perturb existing targets."""
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        f"/api/v1/execute/async/{TARGET_TOPIC}", json={"input": {"topic": "black holes"}}
    )

    assert resp.status_code == 202
    assert len(cp.dispatch_calls) == 1


def test_dsl_hooks_allowed_keys_exclude_non_target_shapes():
    for key in ("topic", "url", "text", "preset", "count", "source", "clip_plan"):
        assert key not in DSL_HOOKS_ALLOWED_INPUT_KEYS


def test_dsl_hooks_path_never_exposes_raw_optout():
    """No raw/fast opt-out on this workflow (research: finish runs by default)."""
    assert "raw" not in DSL_HOOKS_FINISH_OVERRIDE_KEYS
    assert "fast" not in DSL_HOOKS_FINISH_OVERRIDE_KEYS
