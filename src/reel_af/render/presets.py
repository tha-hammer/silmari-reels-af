"""Named reel-format presets.

A preset is a flat dict of reel-format settings (dimensions, window length,
overlay type + params) loaded from ``config/presets.json`` — the reusable
"format" a batch driver reads to know how to cut and treat a source. Kept
separate from ``ReelFinishConfig`` (the ASS finish tunables): a preset composes
the whole format, including the Remotion overlay choice.

Access is one hop: ``load_preset("middle-third-dynamic")["overlay_accent"]``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PRESETS_PATH = Path(__file__).parent / "config" / "presets.json"


@lru_cache(maxsize=1)
def _all() -> dict[str, dict[str, Any]]:
    return json.loads(_PRESETS_PATH.read_text())


def preset_names() -> list[str]:
    return sorted(_all().keys())


def load_preset(name: str) -> dict[str, Any]:
    """The flat settings dict for a named preset (raises KeyError if unknown)."""
    presets = _all()
    if name not in presets:
        raise KeyError(f"unknown preset {name!r}; available: {preset_names()}")
    return dict(presets[name])


__all__ = ["preset_names", "load_preset"]
