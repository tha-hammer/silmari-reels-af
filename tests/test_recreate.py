"""Plan 2 — Recreate loop + cost guard (backend policy layer).

Tests the pure policy layer in ``reel_af.recreate``: prompt+note composition,
HQ model selection, single-slide replace, sibling-safety, premium-ack guard,
the None-dep fail-clean guard, and the per-carousel HQ-recreate cap. Built on
Plan 1's ``regenerate_slide`` primitive (injected as a spy here) and a fake
``StoragePort``.

Behaviors that only exercise ``recreate_slide``'s precondition guards or an
injected ``_regenerate`` spy run now. The four *real-provider* end-to-end asserts
(B2 provider model-call, B3 replace-one fresh-ref, B4 sibling-safety, B6 real-regen
cap boundary) are DEFERRED until Plan 1 lands ``reel_af.app.regenerate_slide`` — see
the `# DEFERRED (Plan 1 seam)` block at the bottom.
"""

import copy

import pytest
from util import make_fake_provider, square_png_bytes

from reel_af.recreate import (
    HQ_RECREATE_CAP,
    HqRecreateCapError,
    PremiumNotAcknowledgedError,
    RecreateDepsUnresolvedError,
    RecreateInputError,
    apply_recreate,
    compose_recreate_prompt,
    recreate_slide,
    resolve_hq_model,
)
from reel_af.render import images

# ───── shared fakes (Plan 1 patterns, copied) ────────────────────────


class _FakeStoragePort:
    def __init__(self):
        self.saved = []

    async def put(self, *, run_id, idx, path):
        self.saved.append((run_id, idx, path))
        return f"stub://{run_id}/{idx}"


class _MemGuard:
    def __init__(self, cap):
        self.cap = cap
        self.counts = {}

    def register(self, carousel_id):
        n = self.counts.get(carousel_id, 0)
        if n >= self.cap:
            raise HqRecreateCapError(carousel_id, self.cap)
        self.counts[carousel_id] = n + 1

    def count(self, carousel_id):
        return self.counts.get(carousel_id, 0)


def _carousel(run_id="run1", carousel_id="car1"):
    return {
        "carousel_id": carousel_id,
        "run_id": run_id,
        "slides": [
            {"idx": 0, "image_prompt": "p0", "image_ref": "stub://run1/0", "status": "ok"},
            {"idx": 1, "image_prompt": "p1", "image_ref": "stub://run1/1", "status": "ok"},
            {"idx": 2, "image_prompt": "p2", "image_ref": "stub://run1/2", "status": "ok"},
        ],
    }


async def _ok_regen(**kw):
    """Injected spy standing in for Plan 1's regenerate_slide: a success record."""
    return {
        "idx": kw["idx"],
        "image_prompt": kw["image_prompt"],
        "image_ref": f"stub://{kw['run_id']}/{kw['idx']}",
        "status": "ok",
    }


# ───── Behavior 1: compose prompt + note (ISC-17, ISC-18) ─────────────


def test_compose_puts_original_then_note():
    composed = compose_recreate_prompt("a quiet lab bench", "make it night, add neon")
    assert "a quiet lab bench" in composed
    assert "make it night, add neon" in composed
    assert composed.index("a quiet lab bench") < composed.index("make it night, add neon")


@pytest.mark.parametrize(
    "original,note",
    [
        ("orig", "note"),
        ("café ☕ scene", "add lumière"),
        ("line one\nline two", "note\nwith newline"),
        ("short", "n " * 5000),
    ],
)
def test_compose_preserves_both_substrings_in_order(original, note):
    composed = compose_recreate_prompt(original, note)
    assert original in composed and note in composed
    assert composed.index(original) < composed.index(note)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_compose_rejects_blank_note(bad):
    with pytest.raises(ValueError, match="note"):
        compose_recreate_prompt("orig", bad)


# ───── Behavior 2: HQ model select (ISC-19) — fallback (no regen) ─────


def test_hq_model_falls_back_to_standard_when_unset(monkeypatch):
    monkeypatch.delenv("REEL_AF_IMAGE_MODEL_HQ", raising=False)
    assert resolve_hq_model() == images.IMAGE_MODEL


def test_hq_model_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    assert resolve_hq_model() == "premium/hq-image-x"


# ───── Behavior 2b: None deps fail cleanly (typed, no cap charge) ─────


@pytest.mark.parametrize("provider,storage", [(None, "S"), ("P", None), (None, None)])
async def test_recreate_none_deps_raise_typed_not_attributeerror(
    tmp_path, provider, storage, monkeypatch
):
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    guard = _MemGuard(cap=5)
    fake = make_fake_provider(image_data=square_png_bytes(300))
    prov = fake() if provider == "P" else None
    stor = _FakeStoragePort() if storage == "S" else None

    with pytest.raises(RecreateDepsUnresolvedError):
        await recreate_slide(
            carousel=_carousel(), idx=1, note="x", out_dir=str(tmp_path),
            provider=prov, storage=stor, guard=guard, acknowledge_premium=True,
        )
    assert guard.count("car1") == 0  # a None-dep call charges no cap


# ───── Behavior 3: bounds + apply_recreate (pure) ─────────────────────


@pytest.mark.parametrize("bad_idx", [-1, 3, 99])
async def test_recreate_out_of_range_idx_raises(tmp_path, bad_idx, monkeypatch):
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    guard = _MemGuard(cap=5)
    with pytest.raises((ValueError, IndexError)):
        await recreate_slide(
            carousel=_carousel(), idx=bad_idx, note="x", out_dir=str(tmp_path),
            provider=make_fake_provider(image_data=square_png_bytes(300))(),
            storage=_FakeStoragePort(), guard=guard, acknowledge_premium=True,
        )
    assert guard.count("car1") == 0  # no cap consumed on a rejected recreate


def test_apply_recreate_replaces_by_idx_preserving_order_length_and_purity():
    manifest = _carousel(run_id="runA")
    before = copy.deepcopy(manifest["slides"])
    new_record = {"idx": 1, "image_prompt": "p1\n\nbrighter",
                  "image_ref": "stub://runA/1", "status": "ok"}

    out = apply_recreate(manifest, new_record)

    assert [s["idx"] for s in out["slides"]] == [0, 1, 2]
    assert len(out["slides"]) == 3
    assert out["slides"][1] == new_record
    assert out["slides"][0]["image_ref"] == before[0]["image_ref"]
    assert out["slides"][2]["image_ref"] == before[2]["image_ref"]
    # purity: the INPUT manifest's slide list is not mutated in place
    assert manifest["slides"][1] == before[1]


def test_apply_recreate_rejects_out_of_range_record_idx():
    manifest = _carousel()
    with pytest.raises((ValueError, IndexError)):
        apply_recreate(manifest, {"idx": 9, "image_prompt": "x",
                                  "image_ref": "r", "status": "ok"})


# ───── Behavior 5: premium acknowledgment required (ISC-53) ───────────


@pytest.mark.parametrize("ack", [False, 0, "", None])
async def test_recreate_rejected_without_premium_ack(tmp_path, ack, monkeypatch):
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    storage = _FakeStoragePort()
    fake = make_fake_provider(image_data=square_png_bytes(300))
    guard = _MemGuard(cap=5)

    with pytest.raises(PremiumNotAcknowledgedError):
        await recreate_slide(
            carousel=_carousel(), idx=1, note="x", out_dir=str(tmp_path),
            provider=fake(), storage=storage, guard=guard, acknowledge_premium=ack,
        )

    # nothing generated, stored, or counted
    assert storage.saved == []
    assert not any(m == "image" for m, _ in fake.calls)
    assert guard.count("car1") == 0


# ───── Behavior 6: per-carousel HQ cap (ISC-54) ───────────────────────


def test_hq_cap_default_is_configurable():
    assert isinstance(HQ_RECREATE_CAP, int) and HQ_RECREATE_CAP >= 0


@pytest.mark.parametrize("cap", [0, 1, 3, 5])
async def test_hq_cap_boundary_via_spy(tmp_path, cap, monkeypatch):
    """ISC-54 cap boundary, driven through an injected ok-returning _regenerate spy
    (real-provider variant deferred until Plan 1's regenerate_slide lands)."""
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    guard = _MemGuard(cap=cap)
    fake = make_fake_provider(image_data=square_png_bytes(300))

    async def one():
        return await recreate_slide(
            carousel=_carousel(), idx=1, note="x", out_dir=str(tmp_path),
            provider=fake(), storage=_FakeStoragePort(), guard=guard,
            acknowledge_premium=True, _regenerate=_ok_regen,
        )

    for _ in range(cap):
        await one()
    with pytest.raises(HqRecreateCapError):
        await one()
    assert guard.count("car1") == cap  # rejected call did not increment past cap


async def test_hq_cap_is_per_carousel_via_spy(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    guard = _MemGuard(cap=1)
    fake = make_fake_provider(image_data=square_png_bytes(300))

    await recreate_slide(carousel=_carousel(carousel_id="A", run_id="rA"), idx=0, note="x",
                         out_dir=str(tmp_path), provider=fake(), storage=_FakeStoragePort(),
                         guard=guard, acknowledge_premium=True, _regenerate=_ok_regen)
    await recreate_slide(carousel=_carousel(carousel_id="B", run_id="rB"), idx=0, note="x",
                         out_dir=str(tmp_path), provider=fake(), storage=_FakeStoragePort(),
                         guard=guard, acknowledge_premium=True, _regenerate=_ok_regen)
    assert guard.count("A") == 1 and guard.count("B") == 1


async def test_failed_generation_does_not_consume_cap(tmp_path, monkeypatch):
    """Register-after-success: a regenerate that FAILS charges no premium slot."""
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    guard = _MemGuard(cap=5)

    async def _fail_regen(**kw):
        return {"idx": kw["idx"], "image_prompt": kw["image_prompt"],
                "image_ref": None, "status": "failed", "error": "provider boom"}

    record = await recreate_slide(
        carousel=_carousel(), idx=1, note="x", out_dir=str(tmp_path),
        provider=make_fake_provider(image_data=square_png_bytes(300))(),
        storage=_FakeStoragePort(), guard=guard, acknowledge_premium=True,
        _regenerate=_fail_regen,
    )
    assert record["status"] == "failed"
    assert guard.count("car1") == 0  # failed HQ generation consumed no cap slot


async def test_raising_generation_does_not_consume_cap(tmp_path, monkeypatch):
    """A regenerate that RAISES (Plan 1's standalone path) also charges no slot."""
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    guard = _MemGuard(cap=5)

    async def _boom_regen(**kw):
        raise RuntimeError("provider boom")

    with pytest.raises(RuntimeError):
        await recreate_slide(
            carousel=_carousel(), idx=1, note="x", out_dir=str(tmp_path),
            provider=make_fake_provider(image_data=square_png_bytes(300))(),
            storage=_FakeStoragePort(), guard=guard, acknowledge_premium=True,
            _regenerate=_boom_regen,
        )
    assert guard.count("car1") == 0  # a raising HQ generation consumed no cap slot


async def test_missing_carousel_id_raises_typed_before_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_AF_IMAGE_MODEL_HQ", "premium/hq-image-x")
    manifest = {"run_id": "r", "slides": _carousel()["slides"]}  # no carousel_id (PRD §6.3 shape)
    with pytest.raises(RecreateInputError):
        await recreate_slide(
            carousel=manifest, idx=1, note="x", out_dir=str(tmp_path),
            provider=make_fake_provider(image_data=square_png_bytes(300))(),
            storage=_FakeStoragePort(), guard=_MemGuard(cap=5), acknowledge_premium=True,
        )


# ───── DEFERRED (Plan 1 seam) ─────────────────────────────────────────
# These four assert on the REAL reel_af.app.regenerate_slide path (fake provider +
# fake StoragePort) and are added the moment SapphireDune lands Behavior 12:
#   - test_recreate_uses_hq_model            (B2: provider.calls[0]["model"] == HQ id)
#   - test_recreate_returns_one_replaced_slide_with_fresh_ref  (B3: fresh ref, only idx stored)
#   - test_recreate_leaves_siblings_untouched (B4/ISC-A1: siblings byte-identical, one put/one image)
#   - test_hq_cap_boundary / _is_per_carousel real-regen variants (B6 integration)
# The cap SEMANTICS (ISC-54) are already covered above via the injected _ok_regen spy.
