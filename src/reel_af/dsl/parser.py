"""Composite Transcript DSL v2 — marker parser and canonical serializer."""

from __future__ import annotations

import re
from typing import Any

from reel_af.dsl.ast import (
    Extend,
    Find,
    Hole,
    Insert,
    Join,
    Marker,
    SourceLocus,
    Trans,
)
from reel_af.dsl.models import XfadeEffect

XFADE_EFFECTS: frozenset[str] = frozenset({
    "dissolve", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "hblur", "circleopen", "radial", "pixelize",
    "fadeblack", "fadewhite", "fade", "none",
})


class MarkerError(ValueError):
    def __init__(self, message: str, *, source: SourceLocus | None = None):
        self.source = source
        super().__init__(message)


_BRACKET_RE = re.compile(r"^\[(.+)\]$", re.DOTALL)


def parse_marker(line: str, *, source: SourceLocus | None = None) -> Marker:
    line = line.strip()
    m = _BRACKET_RE.match(line)
    if not m:
        raise MarkerError(f"not a bracketed marker: {line!r}", source=source)
    inner = m.group(1).strip()

    resolution = None
    if "=>" in inner:
        parts = inner.split("=>", 1)
        inner = parts[0].strip()
        resolution = _parse_resolution(parts[1].strip())

    exclude: tuple[str, ...] = ()
    exclude_match = re.search(r"\bexclude=(\S+)", inner)
    if exclude_match:
        exclude = tuple(exclude_match.group(1).split(","))
        inner = inner[:exclude_match.start()].strip() + " " + inner[exclude_match.end():].strip()
        inner = inner.strip()

    tokens = inner.split()
    if not tokens:
        raise MarkerError("empty marker", source=source)

    verb = tokens[0].lower()
    args = tokens[1:]

    if verb == "insert":
        return _parse_insert(args, exclude, resolution, source)
    if verb == "find":
        return _parse_find(args, exclude, resolution, source)
    if verb == "extend":
        return _parse_extend(args, exclude, resolution, source)
    if verb == "join":
        return _parse_join(args, source)
    if verb == "trans":
        return _parse_trans(args, exclude, resolution, source)

    raise MarkerError(f"unknown verb: {verb!r}", source=source)


def _parse_insert(
    args: list[str],
    exclude: tuple[str, ...],
    resolution: str | float | int | None,
    source: SourceLocus | None,
) -> Insert:
    if not args:
        raise MarkerError("insert requires a selector", source=source)

    selector: str | Hole
    if args[0] == "?":
        selector = Hole(exclude=exclude, resolution=resolution)
        rest = args[1:]
    elif args[0] in ("relevant", "black", "file"):
        selector = args[0]
        rest = args[1:]
    else:
        raise MarkerError(f"unknown insert selector: {args[0]!r}", source=source)

    duration_s: float | Hole | None = None
    file_stem: str | Hole | None = None

    if rest:
        if selector == "file":
            if rest[0] == "?":
                file_stem = Hole(exclude=exclude, resolution=resolution)
            else:
                file_stem = rest[0]
        else:
            if rest[0] == "?":
                duration_s = Hole(exclude=exclude, resolution=resolution)
            else:
                try:
                    duration_s = float(rest[0])
                except ValueError:
                    raise MarkerError(
                        f"insert duration must be numeric, got {rest[0]!r}",
                        source=source,
                    )

    return Insert(
        selector=selector,
        duration_s=duration_s,
        file_stem=file_stem,
        source=source,
    )


def _parse_find(
    args: list[str],
    exclude: tuple[str, ...],
    resolution: str | float | int | None,
    source: SourceLocus | None,
) -> Find:
    if len(args) < 3:
        raise MarkerError("find requires selector, duration, and count", source=source)

    selector: str | Hole
    if args[0] == "?":
        selector = Hole(exclude=exclude, resolution=resolution)
    elif args[0] == "relevant":
        selector = args[0]
    else:
        raise MarkerError(f"unknown find selector: {args[0]!r}", source=source)

    if args[1] == "?":
        duration_s: float | Hole = Hole(exclude=exclude, resolution=resolution)
    else:
        try:
            duration_s = float(args[1])
        except ValueError:
            raise MarkerError(f"find duration must be numeric: {args[1]!r}", source=source)

    count_str = args[2]
    if count_str == "?":
        count: int | Hole = Hole(exclude=exclude, resolution=resolution)
    elif count_str.startswith("x"):
        try:
            count = int(count_str[1:])
        except ValueError:
            raise MarkerError(f"find count must be x<int>: {count_str!r}", source=source)
    else:
        raise MarkerError(f"find count must start with 'x': {count_str!r}", source=source)

    return Find(selector=selector, duration_s=duration_s, count=count, source=source)


def _parse_extend(
    args: list[str],
    exclude: tuple[str, ...],
    resolution: str | float | int | None,
    source: SourceLocus | None,
) -> Extend:
    if len(args) < 2:
        raise MarkerError("extend requires edge and duration", source=source)

    edge: str | Hole
    if args[0] == "?":
        edge = Hole(exclude=exclude, resolution=resolution)
    elif args[0] in ("head", "tail"):
        edge = args[0]
    else:
        raise MarkerError(f"unknown extend edge: {args[0]!r}", source=source)

    if args[1] == "?":
        duration_s: float | Hole = Hole(exclude=exclude, resolution=resolution)
    else:
        try:
            duration_s = float(args[1])
        except ValueError:
            raise MarkerError(f"extend duration must be numeric: {args[1]!r}", source=source)

    return Extend(edge=edge, duration_s=duration_s, source=source)


def _parse_join(args: list[str], source: SourceLocus | None) -> Join:
    mode = "normal"
    if args:
        if args[0] in ("confirmed", "force"):
            mode = args[0]
        else:
            raise MarkerError(f"unknown join mode: {args[0]!r}", source=source)
    return Join(mode=mode, source=source)


def _parse_trans(
    args: list[str],
    exclude: tuple[str, ...],
    resolution: str | float | int | None,
    source: SourceLocus | None,
) -> Trans:
    if not args:
        raise MarkerError("trans requires a primitive", source=source)

    primitive: XfadeEffect | Hole
    if args[0] == "?":
        primitive = Hole(exclude=exclude, resolution=resolution)
    elif args[0] in XFADE_EFFECTS:
        primitive = args[0]
    else:
        raise MarkerError(f"unknown transition primitive: {args[0]!r}", source=source)

    duration_s: float | Hole = 1.0
    audio: str = "fade"
    is_all = False

    for arg in args[1:]:
        if arg == "?":
            duration_s = Hole(exclude=exclude, resolution=resolution)
        elif arg == "all":
            is_all = True
        elif arg.startswith("audio="):
            audio = arg.split("=", 1)[1]
            if audio not in ("fade", "cut"):
                raise MarkerError(f"unknown audio mode: {audio!r}", source=source)
        else:
            try:
                duration_s = float(arg)
            except ValueError:
                raise MarkerError(f"unexpected trans argument: {arg!r}", source=source)

    return Trans(
        primitive=primitive,
        duration_s=duration_s,
        audio=audio,
        all=is_all,
        source=source,
    )


def _parse_resolution(raw: str) -> str | float | int:
    try:
        val = int(raw)
        return val
    except ValueError:
        pass
    try:
        val = float(raw)
        return val
    except ValueError:
        pass
    return raw


def serialize_marker(marker: Marker) -> str:
    if isinstance(marker, Insert):
        return _serialize_insert(marker)
    if isinstance(marker, Find):
        return _serialize_find(marker)
    if isinstance(marker, Extend):
        return _serialize_extend(marker)
    if isinstance(marker, Join):
        return _serialize_join(marker)
    if isinstance(marker, Trans):
        return _serialize_trans(marker)
    raise MarkerError(f"unknown marker type: {type(marker).__name__}")


def _serialize_hole_or_value(value: Any, *, is_hole_field: bool = True) -> str:
    if isinstance(value, Hole):
        return "?"
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return str(value)
    return str(value)


def _serialize_exclude(value: Any) -> str:
    if isinstance(value, Hole) and value.exclude:
        return f" exclude={','.join(value.exclude)}"
    return ""


def _serialize_resolution(marker: Any) -> str:
    for field_name in _hole_fields(marker):
        val = getattr(marker, field_name, None)
        if isinstance(val, Hole) and val.resolution is not None:
            res = val.resolution
            if isinstance(res, float) and res == int(res):
                return f" => {int(res)}"
            return f" => {res}"
    return ""


def _hole_fields(marker: Any) -> list[str]:
    kind = getattr(marker, "kind", "")
    if kind == "insert":
        return ["selector", "duration_s", "file_stem"]
    if kind == "find":
        return ["selector", "duration_s", "count"]
    if kind == "extend":
        return ["edge", "duration_s"]
    if kind == "trans":
        return ["primitive", "duration_s"]
    return []


def _serialize_insert(m: Insert) -> str:
    parts = ["insert", _serialize_hole_or_value(m.selector)]
    if m.file_stem is not None:
        parts.append(_serialize_hole_or_value(m.file_stem))
    elif m.duration_s is not None:
        parts.append(_serialize_hole_or_value(m.duration_s))
    excl = _collect_exclude(m)
    if excl:
        parts.append(f"exclude={','.join(excl)}")
    res = _serialize_resolution(m)
    return f"[{' '.join(parts)}{res}]"


def _serialize_find(m: Find) -> str:
    count_str = _serialize_hole_or_value(m.count)
    if isinstance(m.count, int):
        count_str = f"x{m.count}"
    parts = [
        "find",
        _serialize_hole_or_value(m.selector),
        _serialize_hole_or_value(m.duration_s),
        count_str,
    ]
    excl = _collect_exclude(m)
    if excl:
        parts.append(f"exclude={','.join(excl)}")
    res = _serialize_resolution(m)
    return f"[{' '.join(parts)}{res}]"


def _serialize_extend(m: Extend) -> str:
    parts = ["extend", _serialize_hole_or_value(m.edge), _serialize_hole_or_value(m.duration_s)]
    excl = _collect_exclude(m)
    if excl:
        parts.append(f"exclude={','.join(excl)}")
    res = _serialize_resolution(m)
    return f"[{' '.join(parts)}{res}]"


def _serialize_join(m: Join) -> str:
    if m.mode == "normal":
        return "[join]"
    return f"[join {m.mode}]"


def _serialize_trans(m: Trans) -> str:
    parts = ["trans", _serialize_hole_or_value(m.primitive)]
    dur = m.duration_s
    if isinstance(dur, Hole) or dur != 1.0:
        parts.append(_serialize_hole_or_value(dur))
    if m.audio == "cut":
        parts.append("audio=cut")
    if m.all:
        parts.append("all")
    excl = _collect_exclude(m)
    if excl:
        parts.append(f"exclude={','.join(excl)}")
    res = _serialize_resolution(m)
    return f"[{' '.join(parts)}{res}]"


def _collect_exclude(marker: Any) -> tuple[str, ...]:
    for field_name in _hole_fields(marker):
        val = getattr(marker, field_name, None)
        if isinstance(val, Hole) and val.exclude:
            return val.exclude
    return ()


__all__ = [
    "MarkerError",
    "parse_marker",
    "serialize_marker",
]
