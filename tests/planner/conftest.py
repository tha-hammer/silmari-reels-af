from __future__ import annotations

import os

import pytest


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--require-openrouter",
        action="store_true",
        default=False,
        help="Fail key-gated OpenRouter tests when OPENROUTER_API_KEY is missing.",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "requires_openrouter(reason): requires a live OpenRouter API key"
    )
    config.addinivalue_line(
        "markers", "openrouter_required(reason): requires a live OpenRouter API key"
    )


@pytest.fixture(autouse=True)
def _gate_openrouter_tests(request):
    marker = request.node.get_closest_marker("requires_openrouter")
    marker = marker or request.node.get_closest_marker("openrouter_required")
    if marker is None or os.environ.get("OPENROUTER_API_KEY"):
        return
    reason = marker.kwargs.get("reason") or (marker.args[0] if marker.args else "OpenRouter")
    if request.config.getoption("--require-openrouter"):
        pytest.fail(f"{reason}: OPENROUTER_API_KEY is required")
    pytest.xfail(f"{reason}: OPENROUTER_API_KEY is not set")
