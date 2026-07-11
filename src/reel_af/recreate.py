"""Recreate loop + cost guard — pure backend policy layer (Plan 2 of 6).

Owns the *policy* behind the carousel review "recreate-with-note" flow (PRD Flow C):
prompt+note composition, HQ model selection (``REEL_AF_IMAGE_MODEL_HQ``), the
premium-acknowledgment precondition, and the per-carousel HQ-recreate cap.

It is a thin policy layer above Plan 1's ``regenerate_slide`` generate→store→record
primitive (consumed, never reimplemented) and Plan 3's ``StoragePort`` (reached only
transitively via ``regenerate_slide``). Plan 6 mounts and authorizes the HTTP route
that calls ``recreate_slide`` and backs the cap guard with real persistence.

This module deliberately avoids importing ``reel_af.app`` at module scope until the
``regenerate_slide`` seam (Plan 1) lands, so the pure ``compose_recreate_prompt``
helper is importable independently.
"""

from __future__ import annotations


def compose_recreate_prompt(original_prompt: str, note: str) -> str:
    """Model input for a recreate = the slide's ORIGINAL prompt + the user's note.

    Order is load-bearing (ISC-18): original first so the note reads as an
    adjustment ON TOP of the established scene, not a replacement. This is the
    single place prompt+note is assembled — callers never re-concatenate.
    """
    if not (note or "").strip():
        raise ValueError("recreate: note is empty or whitespace-only")
    # Compose with the RAW note (validate on the stripped value only): ISC-18 requires
    # the note to survive verbatim as a substring, so leading/trailing whitespace the
    # user typed is preserved rather than trimmed out of the model prompt.
    return f"{original_prompt}\n\n{note}"
