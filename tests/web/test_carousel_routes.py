"""Plan 6 — carousel routes and review seams."""

from __future__ import annotations

from carousels import CarouselSlideRefResolver
from conftest import FakeCarouselRepo
from deps import AppDeps, CarouselRepoPort, SlideRefResolverPort, _Unconfigured, default_deps


def test_repo_and_resolver_satisfy_ports():
    assert isinstance(FakeCarouselRepo(), CarouselRepoPort)
    assert isinstance(CarouselSlideRefResolver(FakeCarouselRepo()), SlideRefResolverPort)


def test_default_deps_wires_carousels_and_real_slide_resolver():
    deps = default_deps()
    assert isinstance(deps, AppDeps)
    assert isinstance(deps.carousels, CarouselRepoPort)
    assert isinstance(deps.slides, CarouselSlideRefResolver)
    assert not isinstance(deps.slides, _Unconfigured)
