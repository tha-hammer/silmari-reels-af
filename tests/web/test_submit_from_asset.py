"""AF-4pz.2 — produce multiple reels from one uploaded source.

A composite file-mode submit may reference an already-persisted
``source_asset_id`` instead of a fresh upload handle: the server resolves the
asset org-scoped (foreign/absent/soft-deleted concealed as 404, BEFORE any
row/presign/CP), presigns the STORED bucket key for the node, and each
submission remains its own owner-stamped reel_job — so N reels can be produced
from one upload without re-uploading.
"""

from __future__ import annotations

import uuid

import server
from conftest import (
    FakeControlPlane,
    FakeIdentity,
    FakeReelJobRepo,
    FakeSourceAssetRepo,
    FakeUploadStore,
    make_ctx,
    make_deps,
)
from source_assets import SourceAssetRef

COMPOSITE_URL = "/api/v1/execute/async/reel-af.reel_composite_to_reel"
ORG_ID = make_ctx().org_id
USER_ID = make_ctx().user_id
ASSET_ID = uuid.UUID("a5a5a5a5-0000-4000-8000-00000000c1e0")
BUCKET_KEY = f"{ORG_ID}/{ASSET_ID.hex}-clip.mp4"
PRESIGNED = "https://bucket.example/signed/reused-clip.mp4?sig=xyz"


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _asset(asset_id=ASSET_ID, org_id=ORG_ID):
    return SourceAssetRef(
        asset_id=asset_id, org_id=org_id, created_by=USER_ID,
        bucket_key=BUCKET_KEY, original_filename="clip.mp4",
        content_type="video/mp4", size_bytes=21, checksum="sha256:abc",
        status="stored",
    )


def _deps(*, assets=None, repo=None, cp=None, uploads=None):
    return make_deps(
        identity=FakeIdentity(make_ctx("member")),
        reel_jobs=repo or FakeReelJobRepo(),
        control_plane=cp or FakeControlPlane(response=(202, {"execution_id": "exec_a"}, {})),
        uploads=uploads or FakeUploadStore(presigned=PRESIGNED),
        source_assets=FakeSourceAssetRepo(assets=assets or []),
    )


def _submit(deps, input_body, headers=None):
    return _client(deps).post(COMPOSITE_URL, json={"input": input_body}, headers=headers or {})


# ── Behavior 1: submit by asset id presigns the stored key ───────────


def test_submit_by_asset_id_presigns_stored_key_and_dispatches():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_a"}, {}))
    uploads = FakeUploadStore(presigned=PRESIGNED)
    deps = _deps(assets=[_asset()], repo=repo, cp=cp, uploads=uploads)

    resp = _submit(deps, {"source_asset_id": str(ASSET_ID), "preset": "middle-third-dynamic"})

    assert resp.status_code == 202
    # The STORED bucket key was presigned with the caller's ctx.
    assert uploads.presign_calls == [(ORG_ID, BUCKET_KEY)]
    # Asset resolved org-scoped through the repo.
    assert [str(a) for _, a in deps.source_assets.get_calls] == [str(ASSET_ID)]
    # Dispatched body: presigned url + preset; never the raw key or the asset id.
    _target, dispatched = cp.dispatch_calls[0]
    assert dispatched["input"]["url"] == PRESIGNED
    assert dispatched["input"]["preset"] == "middle-third-dynamic"
    assert "source" not in dispatched["input"]
    assert "source_asset_id" not in dispatched["input"]
    # The job row records the asset-mode provenance.
    _ctx, submission, _job, _now, _crid = repo.inserted[0]
    assert submission.source_asset_id == ASSET_ID
    assert submission.params["source_mode"] == "asset"
    assert submission.params["source_asset_id"] == str(ASSET_ID)


# ── Behaviors 2-3: concealment before any side effect ────────────────


def test_foreign_or_absent_asset_is_404_before_row_presign_cp():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane()
    uploads = FakeUploadStore(presigned=PRESIGNED)
    deps = _deps(assets=[], repo=repo, cp=cp, uploads=uploads)  # nothing owned

    resp = _submit(deps, {"source_asset_id": str(ASSET_ID), "preset": "middle-third-dynamic"})

    assert resp.status_code == 404
    assert repo.inserted == []
    assert cp.dispatch_calls == []
    assert uploads.presign_calls == []


def test_soft_deleted_asset_is_404():
    gone = _asset()
    deps = _deps(assets=[gone])
    deps.source_assets.deleted.add(str(ASSET_ID))

    resp = _submit(deps, {"source_asset_id": str(ASSET_ID), "preset": "middle-third-dynamic"})

    assert resp.status_code == 404


# ── Behaviors 4-6: validation ────────────────────────────────────────


def test_both_source_and_asset_id_is_400_conflicting_sources():
    deps = _deps(assets=[_asset()])
    resp = _submit(deps, {
        "source": BUCKET_KEY, "source_asset_id": str(ASSET_ID),
        "preset": "middle-third-dynamic",
    })
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "conflicting_sources"


def test_non_uuid_asset_id_is_400():
    deps = _deps(assets=[_asset()])
    resp = _submit(deps, {"source_asset_id": "not-a-uuid", "preset": "middle-third-dynamic"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_source_asset_id"


def test_url_mode_rejects_source_asset_id():
    deps = _deps(assets=[_asset()])
    resp = _submit(deps, {
        "url": "https://youtube.com/watch?v=abc",
        "source_asset_id": str(ASSET_ID),
        "preset": "middle-third-dynamic",
    })
    assert resp.status_code == 400


# ── Behavior 7: one asset → many reels ───────────────────────────────


def test_two_submits_from_same_asset_yield_two_jobs():
    repo = FakeReelJobRepo()
    cp = FakeControlPlane(response=(202, {"execution_id": "exec_a"}, {}))
    deps = _deps(assets=[_asset()], repo=repo, cp=cp)

    first = _submit(deps, {"source_asset_id": str(ASSET_ID), "preset": "middle-third-dynamic"},
                    headers={"Idempotency-Key": "reel-1"})
    second = _submit(deps, {"source_asset_id": str(ASSET_ID), "preset": "lower-third-locked"},
                     headers={"Idempotency-Key": "reel-2"})

    assert first.status_code == 202 and second.status_code == 202
    assert len(repo.inserted) == 2                       # own reel_job per submission
    assert len(cp.dispatch_calls) == 2                   # no re-upload, straight reuse
    presets = [s.params["preset"] for _, s, _, _, _ in repo.inserted]
    assert presets == ["middle-third-dynamic", "lower-third-locked"]
