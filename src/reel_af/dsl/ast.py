"""Composite Transcript DSL v2 — marker AST, source loci, typed holes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from reel_af.dsl.models import XfadeEffect


class SourceLocus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path | None = None
    line: int
    col: int
    raw: str


class Hole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Literal["?"] = "?"
    resolution: str | float | int | None = None
    exclude: tuple[str, ...] = ()


class Insert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["insert"] = "insert"
    selector: Literal["relevant", "black", "file"] | Hole
    duration_s: float | Hole | None = None
    file_stem: str | Hole | None = None
    source: SourceLocus | None = None


class Find(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["find"] = "find"
    selector: Literal["relevant"] | Hole
    duration_s: float | Hole
    count: int | Hole
    source: SourceLocus | None = None


class Extend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["extend"] = "extend"
    edge: Literal["head", "tail"] | Hole
    duration_s: float | Hole
    source: SourceLocus | None = None


class Join(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["join"] = "join"
    mode: Literal["normal", "confirmed", "force"] = "normal"
    source: SourceLocus | None = None


class Trans(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["trans"] = "trans"
    primitive: XfadeEffect | Hole
    duration_s: float | Hole = 1.0
    audio: Literal["fade", "cut"] = "fade"
    all: bool = False
    source: SourceLocus | None = None


Marker = Annotated[
    Insert | Find | Extend | Join | Trans,
    Field(discriminator="kind"),
]

__all__ = [
    "Extend",
    "Find",
    "Hole",
    "Insert",
    "Join",
    "Marker",
    "SourceLocus",
    "Trans",
]
