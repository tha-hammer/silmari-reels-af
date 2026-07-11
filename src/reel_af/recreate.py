"""Recreate loop + cost guard — pure backend policy layer (Plan 2 of 6).

Owns the *policy* behind the carousel review "recreate-with-note" flow (PRD Flow C):
prompt+note composition, HQ model selection (``REEL_AF_IMAGE_MODEL_HQ``), the
premium-acknowledgment precondition, the ``None``-dep fail-clean guard, and the
per-carousel HQ-recreate cap.

It is a thin policy layer above Plan 1's ``regenerate_slide`` generate→store→record
primitive (consumed, never reimplemented) and Plan 3's ``StoragePort`` (reached only
transitively via ``regenerate_slide``). Plan 6 mounts and authorizes the HTTP route
that calls ``recreate_slide`` and backs the cap guard with real persistence.

``regenerate_slide`` (``reel_af.app``, Plan 1 Behavior 12) is imported **lazily** inside
``recreate_slide`` so this module (and its pure helpers) stay importable while that
seam is still landing, and so callers/tests can inject a ``_regenerate`` spy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from reel_af.render.images import IMAGE_MODEL

# Structural literals named in code (not tunables/UI — those live in JSON config below).
_HQ_MODEL_ENV = "REEL_AF_IMAGE_MODEL_HQ"  # operator knob: premium image model id
_HQ_CAP_ENV = "REEL_AF_HQ_RECREATE_CAP"   # operator knob: per-carousel HQ cap
_PROMPT_NOTE_SEPARATOR = "\n\n"           # joins the original prompt and the note (ISC-18)
_STATUS_OK = "ok"                          # Plan 1 slide-record success status (shared enum)

# ExternalizeConfig: tunables (HQ cap default, carousel crop, content mode) and all
# user-facing copy live in a flat JSON dict, read once and reached in one jump
# (``_CFG["key"]``). Operators still override the cap/model via the env knobs above;
# the JSON supplies the defaults and the message text.
_CFG = json.loads(Path(__file__).with_name("recreate_config.json").read_text(encoding="utf-8"))
_DEFAULT_CROP = _CFG["default_carousel_crop"]
_DEFAULT_CONTENT_MODE = _CFG["default_content_mode"]

# Per-carousel HQ-recreate cap. Env knob overrides the JSON default; read once (the
# app.py:63-81 getenv-tunable convention). The real cross-request persistence of the
# count is Plan 6 (repo-backed guard); HQ_RECREATE_CAP is the shared default.
HQ_RECREATE_CAP = int(os.getenv(_HQ_CAP_ENV, str(_CFG["hq_recreate_cap_default"])))


class PremiumNotAcknowledgedError(RuntimeError):
    """Raised when an HQ recreate is requested without an explicit premium acknowledgment."""


class RecreateInputError(RuntimeError):
    """carousel arg is missing a required key (carousel_id / run_id).

    The cap can only be charged against a named carousel, so a raw PRD §6.3
    manifest ({run_id, slides:[...]}) without carousel_id is rejected before
    any generation or cap charge — a typed error, not a bare KeyError.
    """


class RecreateDepsUnresolvedError(RuntimeError):
    """provider/storage arrived None — the caller (Plan 6 route) must resolve deps
    and gate OPENROUTER_API_KEY before recreate_slide (see app.py:478-479).

    This is a fail-clean guard, NOT a resolver: recreate_slide never constructs a
    real provider or checks the key (Plan 6 owns that). It only converts the silent
    ``None.generate_image`` AttributeError (images.py:107-111 has no None guard) into
    a typed error Plan 6 maps to the system-standard error body.
    """


class HqRecreateCapError(RuntimeError):
    def __init__(self, carousel_id, cap):
        super().__init__(_CFG["msg_hq_cap_exceeded"].format(carousel_id=carousel_id, cap=cap))
        self.carousel_id = carousel_id
        self.cap = cap


class HqRecreateGuard(Protocol):
    """Per-carousel HQ-recreate cap counter.

    ``register`` MUST be atomic (check-and-increment) in any repo-backed impl so two
    concurrent recreates on one carousel cannot both pass a stale ``count < cap`` read
    and exceed the cap. The in-memory ``_MemGuard`` used in unit tests is single-
    threaded so the race is moot there; Plan 6's repo-backed guard inherits the
    atomicity contract (BLOCKING for Plan 6).
    """

    def register(self, carousel_id: str) -> None: ...  # raises HqRecreateCapError at cap+1

    def count(self, carousel_id: str) -> int: ...


def compose_recreate_prompt(original_prompt: str, note: str) -> str:
    """Model input for a recreate = the slide's ORIGINAL prompt + the user's note.

    Order is load-bearing (ISC-18): original first so the note reads as an
    adjustment ON TOP of the established scene, not a replacement. This is the
    single place prompt+note is assembled — callers never re-concatenate.
    """
    cleaned_note = (note or "").strip()
    if not cleaned_note:
        raise ValueError(_CFG["msg_blank_note"])
    # Compose with the RAW note (validate on the stripped value only): ISC-18 requires
    # the note to survive verbatim as a substring, so leading/trailing whitespace the
    # user typed is preserved rather than trimmed out of the model prompt.
    return f"{original_prompt}{_PROMPT_NOTE_SEPARATOR}{note}"


def resolve_hq_model() -> str:
    """The premium image model for recreate. Read at CALL time (per-call policy).

    Falls back to the standard IMAGE_MODEL when REEL_AF_IMAGE_MODEL_HQ is
    unset/blank so a recreate still works without a configured premium tier.
    Deliberately differs from IMAGE_MODEL's read-once-at-import: the HQ tier is a
    per-call policy an operator can change without a restart (and tests can setenv).
    """
    return (os.getenv(_HQ_MODEL_ENV) or "").strip() or IMAGE_MODEL


def _find_slide(carousel: dict, idx: int) -> dict:
    slides = carousel["slides"]
    if idx < 0 or idx >= len(slides):
        raise IndexError(_CFG["msg_slide_idx_out_of_range"].format(idx=idx, max_idx=len(slides) - 1))
    return slides[idx]


def apply_recreate(manifest: dict, record: dict) -> dict:
    """Return a NEW manifest with the slide at ``record['idx']`` replaced by record.

    Replace-by-matching-idx: ascending order and length are invariants of the
    operation (not of the caller). Does NOT mutate the input manifest/list
    (purity), so sibling-safety is structural. Used by tests and Plan 6's route.
    """
    slides = manifest["slides"]
    idx = record["idx"]
    if idx < 0 or idx >= len(slides):
        raise IndexError(_CFG["msg_apply_idx_out_of_range"].format(idx=idx, max_idx=len(slides) - 1))
    new_slides = [record if s["idx"] == idx else s for s in slides]
    return {**manifest, "slides": new_slides}


async def recreate_slide(
    *,
    carousel: dict,
    idx: int,
    note: str,
    out_dir: str,
    provider=None,
    storage=None,
    guard: HqRecreateGuard,
    acknowledge_premium: bool = False,
    content_mode: str = _DEFAULT_CONTENT_MODE,
    crop: str = _DEFAULT_CROP,
    _regenerate=None,
) -> dict:
    """Recreate a single carousel slide on the HQ image model, with a note.

    Ordering (definitive): ack guard → input-shape guard → None-dep guard → bounds
    check → compose → resolve HQ model → regenerate_slide → register-after-success →
    return. ``guard.register`` charges the cap ONLY on a ``status == "ok"`` record,
    so a failed/raising generation (and any rejected precondition) consumes no
    premium slot. Returns the single replaced slide record; never mutates ``carousel``
    (sibling-safety, ISC-A1, is structural — apply the record via ``apply_recreate``).
    """
    if not acknowledge_premium:
        raise PremiumNotAcknowledgedError(_CFG["msg_premium_not_acknowledged"])

    carousel_id = carousel.get("carousel_id")
    run_id = carousel.get("run_id")
    if not carousel_id or not run_id:
        raise RecreateInputError(_CFG["msg_missing_carousel_keys"])

    if provider is None or storage is None:
        raise RecreateDepsUnresolvedError(_CFG["msg_deps_unresolved"])

    slide = _find_slide(carousel, idx)
    composed = compose_recreate_prompt(slide["image_prompt"], note)

    if _regenerate is None:
        from reel_af.app import regenerate_slide as _regenerate  # Plan 1 seam (lazy)

    record = await _regenerate(
        run_id=run_id,
        idx=idx,
        image_prompt=composed,
        out_dir=out_dir,
        provider=provider,
        storage=storage,
        content_mode=content_mode,
        model=resolve_hq_model(),
        crop=crop,
    )

    # register-after-success: only successful premium spend consumes a cap slot.
    status = record.get("status")
    if status == _STATUS_OK:
        guard.register(carousel_id)
    return record
