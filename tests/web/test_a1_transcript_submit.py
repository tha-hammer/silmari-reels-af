"""AF-a8o → TARGET_TRANSCRIPT allowlist, canonicalization, and presign-key wiring.

The A1 UI preset chains two browser-visible legs:
  transcript_to_plan (this target)  →  dsl_hooks_to_reels (existing target).
Leg 1 takes EITHER a public ``source_url`` (URL mode) OR an org-owned upload
``source`` handle (DROP FILE mode) — exactly one. File-mode handles are presigned
by ``_resolve_cp_input`` into the reasoner's ``source_url`` param (NOT ``url``,
which is the composite reasoner's param name).

Rejections land BEFORE any DB row or CP dispatch (same proof pattern as
tests/web/test_submit.py: ``repo.inserted == []`` and ``cp.dispatch_calls == []``).
"""

from __future__ import annotations

import uuid

import pytest
import server
from conftest import (
    ORG_ID,
    FakeControlPlane,
    FakeIdentity,
    FakeReelJobRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from deps import BadRequest
from reel_jobs import (
    ALLOWLISTED_TARGETS,
    TARGET_DSL_HOOKS,
    TARGET_TRANSCRIPT,
    TRANSCRIPT_SOURCE_MODE,
    build_submission,
)

SOURCE_URL = "https://www.youtube.com/watch?v=abc123"
TRANSCRIPT_URL = f"/api/v1/execute/async/{TARGET_TRANSCRIPT}"
DSL_HOOKS_URL = f"/api/v1/execute/async/{TARGET_DSL_HOOKS}"
PRESIGNED = "https://bucket.example/signed/source.mp4?sig=xyz"

A1_REFS = {
    "composite_ref": "a1://runs/x/composite.ts.md",
    "words_ref": "a1://runs/x/transcript.words.json",
    "hook_ref": "a1://runs/x/hook-plan.json",
}


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _deps(**kw):
    return make_deps(identity=FakeIdentity(make_ctx()), **kw)


def _assert_no_row_no_cp(repo, cp):
    assert repo.inserted == []
    assert cp.dispatch_calls == []


# ── B1: URL-mode canonicalization ──────────────────────────────────


def test_transcript_target_is_allowlisted():
    assert TARGET_TRANSCRIPT in ALLOWLISTED_TARGETS
    assert TARGET_TRANSCRIPT == "reel-af.reel_transcript_to_plan"


def test_transcript_url_mode_is_canonicalized():
    sub = build_submission(TARGET_TRANSCRIPT, {"input": {"source_url": SOURCE_URL}})

    assert sub.target == TARGET_TRANSCRIPT
    assert sub.source_url == SOURCE_URL
    assert sub.topic is None
    assert sub.source_handle is None
    assert sub.cp_input == {"source_url": SOURCE_URL}
    assert sub.params == {"target": TARGET_TRANSCRIPT,
                          "source_mode": TRANSCRIPT_SOURCE_MODE, "source_input": "url"}


# ── B2: FILE-mode canonicalization ─────────────────────────────────


def test_transcript_file_mode_sets_source_handle_and_defers_url():
    handle = f"{ORG_ID}/abc-clip.mp4"
    sub = build_submission(TARGET_TRANSCRIPT, {"input": {"source": handle}})

    assert sub.source_handle == handle
    assert sub.source_url is None
    # The presign happens in _resolve_cp_input; the raw handle never rides cp_input.
    assert sub.cp_input == {}
    assert sub.params == {"target": TARGET_TRANSCRIPT,
                          "source_mode": TRANSCRIPT_SOURCE_MODE, "source_input": "file"}


# ── B3: fail-closed rejects, before any row or CP call ─────────────


@pytest.mark.parametrize(
    "extra",
    [
        {"topic": "black holes"},
        {"url": SOURCE_URL},
        {"text": "some research text"},
        {"preset": "middle-third-dynamic"},
        {"count": 3},
        {"overrides": {"font_scale": 1.2}},
        {"clip_count": 2},
        {"composite_ref": "a1://x/composite.ts.md"},
    ],
)
def test_transcript_unsupported_input_field_rejected(extra):
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        TRANSCRIPT_URL, json={"input": {"source_url": SOURCE_URL, **extra}}
    )

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "unsupported_input_field"
    _assert_no_row_no_cp(repo, cp)


@pytest.mark.parametrize(
    "bad", ["/etc/passwd", "file:///x", "s3://bucket/key", "", "   ", "not-a-url", 7]
)
def test_transcript_invalid_source_url_rejected(bad):
    with pytest.raises(BadRequest) as exc:
        build_submission(TARGET_TRANSCRIPT, {"input": {"source_url": bad}})
    assert exc.value.code == "invalid_url"


def test_transcript_explicit_none_source_url_falls_to_missing_source():
    """Mirror the composite precedent: an explicit ``null`` means absent."""
    with pytest.raises(BadRequest) as exc:
        build_submission(TARGET_TRANSCRIPT, {"input": {"source_url": None}})
    assert exc.value.code == "missing_source"


def test_transcript_article_seed_url_rejected():
    with pytest.raises(BadRequest) as exc:
        build_submission(
            TARGET_TRANSCRIPT,
            {"input": {"source_url": f"{SOURCE_URL}&t=90&reel_end=142"}},
        )
    assert exc.value.code == "unsupported_input_field"


def test_transcript_missing_both_sources_rejected():
    with pytest.raises(BadRequest) as exc:
        build_submission(TARGET_TRANSCRIPT, {"input": {}})
    assert exc.value.code == "missing_source"


def test_transcript_both_sources_rejected():
    with pytest.raises(BadRequest) as exc:
        build_submission(
            TARGET_TRANSCRIPT,
            {"input": {"source_url": SOURCE_URL, "source": f"{ORG_ID}/x.mp4"}},
        )
    assert exc.value.code == "invalid_source"


@pytest.mark.parametrize("bad", ["", "   ", 7, True, None, {"k": "v"}])
def test_transcript_non_string_handle_rejected(bad):
    with pytest.raises(BadRequest):
        build_submission(TARGET_TRANSCRIPT, {"input": {"source": bad}})


def test_transcript_forbidden_identity_rejected_under_input():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    resp = _client(_deps(reel_jobs=repo, control_plane=cp)).post(
        TRANSCRIPT_URL, json={"input": {"source_url": SOURCE_URL, "org_id": "ATTACKER"}}
    )
    assert resp.status_code == 400
    _assert_no_row_no_cp(repo, cp)


# ── B5: presign key is target-aware (source_url, not url) ──────────


def test_transcript_file_submit_presigns_into_source_url():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_a1_plan"}, {}))
    uploads = FakeUploadStore(presigned=PRESIGNED)
    deps = make_deps(
        identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp, uploads=uploads
    )
    handle = f"{ORG_ID}/abc-clip.mp4"

    resp = _client(deps).post(TRANSCRIPT_URL, json={"input": {"source": handle}})

    assert resp.status_code == 202
    assert uploads.presign_calls == [(ORG_ID, handle)]
    (call,) = cp.dispatch_calls
    target, dispatched = call[0], call[1]
    assert target == TARGET_TRANSCRIPT
    assert dispatched["input"]["source_url"] == PRESIGNED
    assert "url" not in dispatched["input"]
    assert "source" not in dispatched["input"]


def test_dsl_hooks_file_submit_presigns_into_source_url():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_a1_render"}, {}))
    uploads = FakeUploadStore(presigned=PRESIGNED)
    deps = make_deps(
        identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp, uploads=uploads
    )
    handle = f"{ORG_ID}/abc-clip.mp4"

    resp = _client(deps).post(
        DSL_HOOKS_URL, json={"input": {"source": handle, **A1_REFS, "clip_idx": 1}}
    )

    assert resp.status_code == 202
    assert uploads.presign_calls == [(ORG_ID, handle)]
    (call,) = cp.dispatch_calls
    _target, dispatched = call[0], call[1]
    assert dispatched["input"]["source_url"] == PRESIGNED
    assert "url" not in dispatched["input"]
    assert "source" not in dispatched["input"]
    for key, ref in A1_REFS.items():
        assert dispatched["input"][key] == ref


def test_transcript_foreign_handle_404_before_presign_row_cp():
    repo, cp = FakeReelJobRepo(), FakeControlPlane()
    uploads = FakeUploadStore()
    deps = make_deps(
        identity=FakeIdentity(make_ctx()), reel_jobs=repo, control_plane=cp, uploads=uploads
    )
    foreign = f"{uuid.uuid4()}/abc-clip.mp4"

    resp = _client(deps).post(TRANSCRIPT_URL, json={"input": {"source": foreign}})

    assert resp.status_code == 404
    _assert_no_row_no_cp(repo, cp)
    assert uploads.presign_calls == []


# ── B6: submit route end-to-end + poll passes refs through ─────────


def test_transcript_url_submit_end_to_end():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_a1_plan"}, {}))
    deps = _deps(reel_jobs=repo, control_plane=cp)

    resp = _client(deps).post(TRANSCRIPT_URL, json={"input": {"source_url": SOURCE_URL}})

    assert resp.status_code == 202
    assert resp.get_json()["execution_id"] == "exec_a1_plan"
    (_ctx, submission, _job, _now, _crid), = repo.inserted
    assert submission.target == TARGET_TRANSCRIPT
    (call,) = cp.dispatch_calls
    assert call[0] == TARGET_TRANSCRIPT
    assert call[1] == {"input": {"source_url": SOURCE_URL}}


def test_transcript_is_not_delivery_required():
    """Leg 1 delivers DATA (artifact refs), not a browser-deliverable reel — it
    must never be flipped to delivery_unavailable by the poll path."""
    from reel_jobs import DELIVERY_REQUIRED_TARGETS

    assert TARGET_TRANSCRIPT not in DELIVERY_REQUIRED_TARGETS
