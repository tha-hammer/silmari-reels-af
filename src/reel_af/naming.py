"""Pure, deterministic descriptive-filename derivation for delivered reels.

The reel-af node produces every reel as a local ``reel.mp4`` and delivers it to the
shared bucket, where the object-key basename becomes the browser's download name.
This module turns a per-reasoner descriptive source (article title / topic / core
claim / preset) into a human-readable, collision-safe basename:

    ``<slug>-<YYYYMMDD>-<run_id>.mp4``

No I/O and no wall-clock: the date is injected so callers stay deterministic and
testable. Reused by all four entry reasoners at their ``upload_reel`` call site.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

_SLUG_CAP = 60  # max slug chars before the -YYYYMMDD-runid.mp4 suffix
_FALLBACK = "reel"  # slug used when the source is empty/blank/non-alphanumeric


def _slug(source: str | None) -> str:
    text = unicodedata.normalize("NFKD", source or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")[:_SLUG_CAP].strip("-")
    return text or _FALLBACK


def reel_output_name(source: str | None, run_id: str, when: date) -> str:
    """Descriptive, collision-safe delivered basename ``<slug>-<YYYYMMDD>-<run_id>.mp4``.

    Deterministic and pure. ``source`` may be any text (title/topic/claim); it is
    slugged to ASCII ``[a-z0-9-]``, dash-collapsed, trimmed, and capped. Empty/blank
    sources fall back to ``"reel"``. Collision-safety comes from ``run_id``.
    """
    return f"{_slug(source)}-{when:%Y%m%d}-{run_id}.mp4"


# Exposed for tests asserting the cap interacts correctly with the suffix.
reel_output_name.CAP = _SLUG_CAP  # type: ignore[attr-defined]

__all__ = ["reel_output_name"]
