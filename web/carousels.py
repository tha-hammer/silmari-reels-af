"""Carousel route domain helpers and storage-backed slide resolution."""

from __future__ import annotations


class HqRecreateCapError(Exception):
    """Raised when the durable HQ recreate cap has already been consumed."""


class CarouselSlideRefResolver:
    """Real Plan-3 slide resolver backed by the carousel read model."""

    def __init__(self, repo):
        self._repo = repo

    def resolve(self, ctx, carousel_id: str, slide_idx: int) -> str:
        return self._repo.slide_ref(ctx, carousel_id, slide_idx)
