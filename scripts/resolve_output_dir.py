#!/usr/bin/env python3
"""Resolve reel-af output directories for local driver scripts."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from reel_af.planner.paths import runs_dir  # noqa: E402


def resolve_output_dir(workflow: str, run_id: str) -> str:
    """Return the shared-resolver run directory for script-provided jobs."""

    return str(runs_dir(workflow, run_id))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: resolve_output_dir.py <workflow> <run_id>", file=sys.stderr)
        return 2
    print(resolve_output_dir(args[0], args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
