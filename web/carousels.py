"""Carousel route domain helpers and storage-backed slide resolution."""

from __future__ import annotations

from dataclasses import dataclass

from deps import BadRequest
from reel_jobs import _reject_forbidden_identity


class HqRecreateCapError(Exception):
    """Raised when the durable HQ recreate cap has already been consumed."""


@dataclass(frozen=True)
class CarouselCreate:
    source_text: str
    preset: str
    source_research_run_id: object | None = None

    def cp_input(self) -> dict:
        return {
            "source_text": self.source_text,
            "preset": self.preset,
        }


def build_carousel_create(body: dict | None) -> CarouselCreate:
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object", code="invalid_json")
    _reject_forbidden_identity(body)
    nested = body.get("input")
    if isinstance(nested, dict):
        _reject_forbidden_identity(nested)

    source_text = str(body.get("source_text") or "").strip()
    if not source_text:
        raise BadRequest("source_text must be non-empty", code="invalid_source_text")
    preset = body.get("preset")
    return CarouselCreate(
        source_text=source_text,
        preset=preset.strip() if isinstance(preset, str) and preset.strip() else "carousel-default",
    )


class CarouselSlideRefResolver:
    """Real Plan-3 slide resolver backed by the carousel read model."""

    def __init__(self, repo):
        self._repo = repo

    def resolve(self, ctx, carousel_id: str, slide_idx: int) -> str:
        return self._repo.slide_ref(ctx, carousel_id, slide_idx)
