"""Resolver pass for Composite Transcript DSL holes.

The resolver is deliberately a thin layer over the parser/AST contracts owned
by the DSL package. It finds unresolved ``?`` holes, asks a synchronous
``ChoiceFn`` for typed choices, validates those choices against the grammar
domain, and writes the resulting ``=> value`` text through ``serialize_marker``.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from reel_af.dsl.models import (
    Diagnostic,
    HoleChoice,
    HoleContext,
    HoleDomain,
    ResolveResult,
)
from reel_af.dsl.parser import parse_marker, serialize_marker

TRANSITION_PRIMITIVES: tuple[str, ...] = (
    "dissolve",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "hblur",
    "circleopen",
    "radial",
    "pixelize",
    "fadeblack",
    "fadewhite",
    "fade",
    "none",
)

INSERT_SELECTORS: tuple[str, ...] = ("relevant", "black", "file")
EDGE_SELECTORS: tuple[str, ...] = ("head", "tail")

_MARKER_RE = re.compile(r"\[[^\]\n]+\]")


class ResolveError(ValueError):
    """Raised when a hole cannot be resolved safely."""


ChoiceFn = Callable[[HoleContext], HoleChoice]


def resolve_text(text: str, choose: ChoiceFn) -> ResolveResult:
    """Resolve every unresolved hole in ``text`` and return ``ResolveResult``."""

    choices: list[HoleChoice] = []
    diagnostics: list[Diagnostic] = []
    parts: list[str] = []
    last_end = 0
    changed = False

    for match in _MARKER_RE.finditer(text):
        marker_text = match.group(0)
        marker = parse_marker(marker_text)
        resolved_marker = marker
        marker_changed = False

        for field_name, hole in _iter_unresolved_holes(resolved_marker):
            domain = _domain_for(resolved_marker, field_name, hole)
            context = HoleContext(
                marker=resolved_marker,
                field_name=field_name,
                domain=domain,
                source=_attr(resolved_marker, "source", None),
                before_text=_neighbor_text(text, match.start(), direction=-1),
                after_text=_neighbor_text(text, match.end(), direction=1),
            )
            raw_choice = choose(context)
            choice_value = _choice_value(raw_choice)
            _validate_choice(choice_value, domain, field_name)
            choice = raw_choice if isinstance(raw_choice, HoleChoice) else HoleChoice(value=choice_value)
            choices.append(choice)
            resolved_marker = _with_hole_resolution(resolved_marker, field_name, hole, choice_value)
            marker_changed = True

        if marker_changed:
            changed = True
            parts.append(text[last_end:match.start()])
            parts.append(
                _preserve_explicit_trans_duration(
                    marker_text,
                    serialize_marker(resolved_marker),
                    resolved_marker,
                )
            )
            last_end = match.end()

    if changed:
        parts.append(text[last_end:])
        resolved_text = "".join(parts)
    else:
        resolved_text = text

    return ResolveResult(
        text=resolved_text,
        changed=changed,
        choices=choices,
        diagnostics=diagnostics,
    )


def resolve_file(path: Path, choose: ChoiceFn) -> ResolveResult:
    """Resolve holes in ``path`` and atomically replace the file if changed."""

    path = Path(path)
    original = path.read_bytes()
    text = original.decode("utf-8")
    result = resolve_text(text, choose)
    if not _attr(result, "changed", False):
        return result

    stat = path.stat()
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(_attr(result, "text").encode("utf-8"))
        os.chmod(tmp_name, stat.st_mode)
        os.replace(tmp_name, path)
    except BaseException:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
        raise
    return result


def _iter_unresolved_holes(marker: Any) -> Iterable[tuple[str, Any]]:
    for field_name in _hole_field_names(marker):
        value = _attr(marker, field_name, None)
        if _is_hole(value) and _attr(value, "resolution", None) is None:
            yield field_name, value


def _hole_field_names(marker: Any) -> tuple[str, ...]:
    kind = str(_attr(marker, "kind", ""))
    if kind == "insert":
        return ("selector", "duration_s", "file_stem")
    if kind == "find":
        return ("selector", "duration_s", "count")
    if kind == "extend":
        return ("edge", "duration_s")
    if kind == "trans":
        return ("primitive", "duration_s")
    return ()


def _is_hole(value: Any) -> bool:
    return _attr(value, "token", None) == "?"


def _domain_for(marker: Any, field_name: str, hole: Any) -> HoleDomain:
    kind = str(_attr(marker, "kind", ""))
    excluded = tuple(_attr(hole, "exclude", ()) or ())

    if field_name == "primitive":
        return _domain("primitive", TRANSITION_PRIMITIVES, excluded=excluded)
    if field_name == "selector":
        candidates = ("relevant",) if kind == "find" else INSERT_SELECTORS
        return _domain("selector", candidates, excluded=excluded)
    if field_name == "edge":
        return _domain("edge", EDGE_SELECTORS, excluded=excluded)
    if field_name == "count":
        return _domain("count", (), min_value=1, excluded=excluded)
    if field_name == "file_stem":
        return _domain("file_stem", (), excluded=excluded)
    if field_name == "duration_s":
        min_value = 0.001 if kind in {"insert", "find", "extend", "trans"} else 0
        return _domain("duration_s", (), min_value=min_value, excluded=excluded)
    raise ResolveError(f"unsupported hole field: {kind}.{field_name}")


def _domain(
    name: str,
    candidates: Sequence[str | float | int],
    *,
    min_value: float | int | None = None,
    max_value: float | int | None = None,
    excluded: Sequence[str | float | int] = (),
) -> HoleDomain:
    filtered = tuple(candidate for candidate in candidates if not _same_choice(candidate, excluded))
    return HoleDomain(
        name=name,
        candidates=filtered,
        min_value=min_value,
        max_value=max_value,
        excluded=tuple(excluded),
    )


def _choice_value(choice: Any) -> str | float | int:
    if isinstance(choice, Mapping):
        value = choice.get("value")
    else:
        value = _attr(choice, "value", choice)
    if not isinstance(value, (str, int, float)):
        raise ResolveError(f"choice value must be str, int, or float; got {type(value).__name__}")
    return value


def _validate_choice(value: str | float | int, domain: Any, field_name: str) -> None:
    excluded = tuple(_attr(domain, "excluded", ()) or ())
    if _same_choice(value, excluded):
        raise ResolveError(f"choice {value!r} is excluded for {field_name}")

    candidates = tuple(_attr(domain, "candidates", ()) or ())
    if candidates and not _same_choice(value, candidates):
        raise ResolveError(
            f"choice {value!r} is outside domain {field_name}; "
            f"expected one of {candidates!r}"
        )

    min_value = _attr(domain, "min_value", None)
    max_value = _attr(domain, "max_value", None)
    if min_value is not None or max_value is not None:
        if not isinstance(value, (int, float)):
            raise ResolveError(f"choice {value!r} for {field_name} must be numeric")
        if min_value is not None and value < min_value:
            raise ResolveError(f"choice {value!r} is below minimum {min_value!r} for {field_name}")
        if max_value is not None and value > max_value:
            raise ResolveError(f"choice {value!r} is above maximum {max_value!r} for {field_name}")


def _with_hole_resolution(marker: Any, field_name: str, hole: Any, value: str | float | int) -> Any:
    if hasattr(hole, "model_copy"):
        resolved_hole = hole.model_copy(update={"resolution": value})
    else:
        resolved_hole = _copy_object(hole, resolution=value)

    if hasattr(marker, "model_copy"):
        return marker.model_copy(update={field_name: resolved_hole})
    return _copy_object(marker, **{field_name: resolved_hole})


def _preserve_explicit_trans_duration(original: str, serialized: str, marker: Any) -> str:
    if _attr(marker, "kind", None) != "trans":
        return serialized

    duration = _explicit_trans_duration_token(original)
    if duration is None or _serialized_trans_has_duration(serialized):
        return serialized

    inner = serialized[1:-1].strip()
    prefix, sep, resolution = inner.partition(" => ")
    tokens = prefix.split()
    if len(tokens) < 2:
        return serialized
    tokens.insert(2, duration)
    rebuilt = " ".join(tokens)
    if sep:
        rebuilt = f"{rebuilt} => {resolution}"
    return f"[{rebuilt}]"


def _explicit_trans_duration_token(marker_text: str) -> str | None:
    inner = marker_text.strip()[1:-1].strip()
    if "=>" in inner:
        inner = inner.split("=>", 1)[0].strip()
    tokens = inner.split()
    if len(tokens) < 3 or tokens[0] != "trans":
        return None
    token = tokens[2]
    try:
        float(token)
    except ValueError:
        return None
    return token


def _serialized_trans_has_duration(serialized: str) -> bool:
    inner = serialized.strip()[1:-1].strip()
    if "=>" in inner:
        inner = inner.split("=>", 1)[0].strip()
    tokens = inner.split()
    if len(tokens) < 3:
        return False
    token = tokens[2]
    if token == "?":
        return True
    try:
        float(token)
    except ValueError:
        return False
    return True


def _copy_object(value: Any, **updates: Any) -> Any:
    data = dict(getattr(value, "__dict__", {}))
    data.update(updates)
    return type(value)(**data)


def _same_choice(value: str | float | int, choices: Sequence[str | float | int]) -> bool:
    for choice in choices:
        if value == choice or str(value) == str(choice):
            return True
    return False


def _neighbor_text(text: str, offset: int, *, direction: int) -> str | None:
    if direction < 0:
        prefix = text[:offset].splitlines()
        for line in reversed(prefix):
            stripped = line.strip()
            if stripped and not _MARKER_RE.fullmatch(stripped):
                return stripped
        return None

    suffix = text[offset:].splitlines()
    for line in suffix:
        stripped = line.strip()
        if stripped and not _MARKER_RE.fullmatch(stripped):
            return stripped
    return None


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        return default
    return getattr(value, name, default)


__all__ = [
    "ChoiceFn",
    "EDGE_SELECTORS",
    "INSERT_SELECTORS",
    "ResolveError",
    "TRANSITION_PRIMITIVES",
    "resolve_file",
    "resolve_text",
]
