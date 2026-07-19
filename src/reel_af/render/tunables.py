"""Per-job tunable override contract — the reasoner's source of truth.

``config/tunables.json`` is a flat map ``key -> {type, min, max, applies, values?}``
describing the whole tunable surface (window sizing + overlay effect props). It is
the SOLE definition of override key/type/bounds; the web boundary, the browser UI,
and the Remotion Zod schema all mirror it (parity-pinned by the four-way test).

``safe_overrides(raw)`` is the reasoner's defensive gate applied just before the
preset merge: unknown keys are dropped, known keys are coerced to their declared
type and *clamped* to bounds, and anything uncoercible falls back to the preset
(is dropped). The output is always a subset of the tunable keys with every value
in range — safe to spread straight onto a loaded preset dict.

Note the split of responsibility: the reasoner *clamps* (defensive, never fails);
the web boundary (``web/tunables.py``) *rejects* out-of-range values with a 400 —
the browser only ever sends in-bounds values, so a clamp here is pure belt-and-braces.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_TUNABLES_PATH = Path(__file__).parent / "config" / "tunables.json"
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


@lru_cache(maxsize=1)
def load_tunables() -> dict[str, dict[str, Any]]:
    """The parsed tunable spec (cached)."""
    return json.loads(_TUNABLES_PATH.read_text())


def tunable_keys() -> list[str]:
    return sorted(load_tunables().keys())


# ── one coercer per declared type; each returns ``None`` to mean "drop" ──


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    number: float
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _coerce_int(value: Any) -> int | None:
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


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "on"}:
            return True
        if s in {"false", "0", "no", "off"}:
            return False
    return None


def _coerce_color(value: Any) -> str | None:
    if isinstance(value, str) and _HEX_COLOR.match(value.strip()):
        return value.strip()
    return None


def _clamp(value: float, spec: dict[str, Any]) -> float:
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value


def _coerce_and_clamp(value: Any, spec: dict[str, Any]) -> Any:
    """Coerce ``value`` to ``spec['type']`` and clamp to bounds; ``None`` = drop."""
    kind = spec["type"]
    if kind == "number":
        num = _coerce_number(value)
        return None if num is None else _clamp(num, spec)
    if kind == "int":
        num = _coerce_int(value)
        return None if num is None else int(_clamp(num, spec))
    if kind == "bool":
        return _coerce_bool(value)
    if kind == "color":
        return _coerce_color(value)
    if kind == "enum":
        return value if value in spec.get("values", []) else None
    return None


def safe_overrides(raw: Any, kind: str | None = None) -> dict[str, Any]:
    """Validate a raw overrides dict against ``tunables.json``.

    Drops unknown keys, coerces known keys to their declared type, and clamps
    numeric values to bounds. When ``kind`` is given (``window``/``middle_third``/
    ``lower_third``), only keys whose ``applies`` is that kind or ``both`` are kept;
    with ``kind=None`` every valid key survives (the composite merge wants all).
    """
    if not isinstance(raw, dict):
        return {}
    tun = load_tunables()
    out: dict[str, Any] = {}
    for key, value in raw.items():
        spec = tun.get(key)
        if spec is None:
            continue
        if kind is not None and not _applies(spec, kind):
            continue
        coerced = _coerce_and_clamp(value, spec)
        if coerced is None:
            continue
        out[key] = coerced
    return out


def _applies(spec: dict[str, Any], kind: str) -> bool:
    applies = spec.get("applies")
    return applies == "both" or applies == kind


__all__ = ["load_tunables", "tunable_keys", "safe_overrides"]
