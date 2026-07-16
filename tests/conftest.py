"""Test bootstrap.

Adds the ``src`` layout and the ``tests`` directory to ``sys.path`` so the
``reel_af`` package and the local ``util`` helper module import cleanly
whether or not the project has been ``pip install``-ed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_TESTS = Path(__file__).resolve().parent

for _p in (str(_SRC), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_addoption(parser) -> None:
    """--regenerate-golden: rewrite generated snapshot fixtures from live output.

    Makes "regenerated, not hand-edited" a mechanical step rather than a
    discipline (B17 / R5). Without this flag the golden fixtures are read-only
    and any drift fails the parity test.
    """

    parser.addoption(
        "--regenerate-golden",
        action="store_true",
        default=False,
        help="Rewrite golden snapshot fixtures from a live invocation.",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "regenerates_golden: writes a golden fixture under --regenerate-golden"
    )
