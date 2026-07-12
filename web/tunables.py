"""Web-boundary mirror of the reel-af tunable override contract (plan Behavior 5/9).

The deployed web image cannot import ``reel_af`` (separate image), so the tunable
spec is duplicated here as a self-contained ``TUNABLES`` literal — the exact mirror
of ``src/reel_af/render/config/tunables.json`` (four-way parity pinned by
``tests/test_tunables_parity.py``, which loads this module by file path).

Unlike the reasoner (which *clamps* defensively), the web boundary *rejects*:
- an unknown key → ``400 unsupported_override_field``
- a bad type or an out-of-range value → ``400 invalid_override``
before any DB row, presign, or control-plane dispatch. The browser only ever sends
in-bounds values, so this is authenticated UI-boundary hardening. Valid overrides
are returned canonicalized (numeric strings coerced) for the dispatched ``cp_input``.
"""

from __future__ import annotations

import re
from typing import Any

from deps import BadRequest

# Byte-for-byte mirror of config/tunables.json (parity-pinned).
TUNABLES: dict[str, dict[str, Any]] = {
    "reel_seconds": {"type": "number", "min": 15, "max": 600, "applies": "window"},
    "overlay_accent": {"type": "color", "applies": "both"},
    "overlay_vertical_anchor": {"type": "number", "min": 0, "max": 1, "applies": "middle_third"},
    "phrase_max_words": {"type": "int", "min": 1, "max": 20, "applies": "middle_third"},
    "phrase_max_dur_s": {"type": "number", "min": 0.5, "max": 10, "applies": "middle_third"},
    "phrase_gap_s": {"type": "number", "min": 0, "max": 5, "applies": "middle_third"},
    "phrase_hold_s": {"type": "number", "min": 0, "max": 5, "applies": "middle_third"},
    "phrase_uppercase": {"type": "bool", "applies": "middle_third"},
    "font_scale": {"type": "number", "min": 0.5, "max": 2.0, "applies": "both"},
    "card_opacity": {"type": "number", "min": 0, "max": 1, "applies": "middle_third"},
    "box_opacity": {"type": "number", "min": 0, "max": 1, "applies": "lower_third"},
    "accent_bar_px": {"type": "int", "min": 0, "max": 40, "applies": "both"},
    "corner_radius": {"type": "int", "min": 0, "max": 80, "applies": "both"},
    "anim_style": {"type": "enum", "values": ["spring", "fade", "slide", "none"], "applies": "both"},
    "anim_damping": {"type": "number", "min": 1, "max": 400, "applies": "both"},
    "anim_mass": {"type": "number", "min": 0.1, "max": 5, "applies": "both"},
    "lower_third_duration_s": {"type": "number", "min": 0, "max": 30, "applies": "lower_third"},
}

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _as_number(value: Any):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_int(value: Any):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        s = value.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return None


def _as_bool(value: Any):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "on"}:
            return True
        if s in {"false", "0", "no", "off"}:
            return False
    return None


def _in_range(value, spec) -> bool:
    lo, hi = spec.get("min"), spec.get("max")
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _validated_value(key: str, value: Any, spec: dict[str, Any]):
    """Return the canonicalized value or raise ``400 invalid_override``."""
    kind = spec["type"]
    if kind in ("number", "int"):
        num = _as_number(value) if kind == "number" else _as_int(value)
        if num is None or not _in_range(num, spec):
            raise BadRequest(f"invalid override: {key}", code="invalid_override")
        return num
    if kind == "bool":
        b = _as_bool(value)
        if b is None:
            raise BadRequest(f"invalid override: {key}", code="invalid_override")
        return b
    if kind == "color":
        if not (isinstance(value, str) and _HEX_COLOR.match(value.strip())):
            raise BadRequest(f"invalid override: {key}", code="invalid_override")
        return value.strip()
    if kind == "enum":
        if value not in spec.get("values", []):
            raise BadRequest(f"invalid override: {key}", code="invalid_override")
        return value
    raise BadRequest(f"invalid override: {key}", code="invalid_override")


def validate_overrides(raw: Any) -> dict[str, Any]:
    """Validate + canonicalize a submitted ``overrides`` object.

    ``None``/empty → ``{}`` (treated as no overrides). Unknown key →
    ``unsupported_override_field``; bad type / out-of-range → ``invalid_override``.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BadRequest("overrides must be an object", code="invalid_override")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        spec = TUNABLES.get(key)
        if spec is None:
            raise BadRequest(f"unsupported override field: {key}", code="unsupported_override_field")
        out[key] = _validated_value(key, value, spec)
    return out


__all__ = ["TUNABLES", "validate_overrides"]
