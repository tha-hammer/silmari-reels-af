"""Loader for the finish-stage config dictionary (ARCHITECTURE §10).

All finish/banner/caption tuning parameters and style dictionaries live in
``config/finish.json`` — domain code (``finish_config.py``, ``captions.py``)
carries no business literals, it reads from the loaded dict. Access is a single
hop: ``load_finish_defaults()["banner_pad_x"]`` or ``[...]["banner_style"]``.

Kept dependency-light (stdlib only) so ``captions.py`` can source its fallbacks
without importing the pydantic model.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).parent / "config" / "finish.json"


@lru_cache(maxsize=1)
def load_finish_defaults() -> dict[str, Any]:
    """The finish config dictionary, loaded once from ``config/finish.json``."""
    return json.loads(_CONFIG_PATH.read_text())


__all__ = ["load_finish_defaults"]
