"""Four-way tunable parity (plan Behavior 9).

The tunable contract has one source of truth (``config/tunables.json``, read by the
reasoner) mirrored into three places: the web boundary (``web/tunables.py``), the
browser UI (``index.html`` ``#config``), and the Remotion Zod schema. This test
pins that they agree — keys + bounds across the first three, and that every
camelCase prop the Python render modules emit exists in the Remotion schema.

``web/`` is import-isolated (it cannot import ``reel_af`` and is only placed on
``sys.path`` inside the web conftest), so ``web/tunables.py`` is read by **AST**,
never imported — importing it would collide the two package roots on ``sys.path``.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from reel_af.render.tunables import load_tunables

ROOT = Path(__file__).resolve().parents[1]
WEB_TUNABLES = ROOT / "web" / "tunables.py"
INDEX_HTML = ROOT / "web" / "index.html"
REMOTION_SRC = ROOT / "remotion" / "src"


def _web_tunables() -> dict:
    """Extract the ``TUNABLES`` literal from web/tunables.py without importing it."""
    tree = ast.parse(WEB_TUNABLES.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "TUNABLES":
                return ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "TUNABLES" in names:
                return ast.literal_eval(node.value)
    raise AssertionError("TUNABLES literal not found in web/tunables.py")


def _ui_tunables() -> dict:
    html = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(
        r'<script type="application/json" id="config">(.*?)</script>', html, re.DOTALL
    )
    assert m, "inline #config block not found"
    return json.loads(m.group(1))["tunables"]


def test_reasoner_web_ui_tunables_are_identical():
    source = load_tunables()
    web = _web_tunables()
    ui = _ui_tunables()

    assert set(source) == set(web) == set(ui)
    for key in source:
        assert source[key] == web[key], f"web mirror drifted for {key}"
        assert source[key] == ui[key], f"UI mirror drifted for {key}"


def test_every_python_emitted_remotion_prop_exists_in_schema():
    """Every camelCase prop the render modules write must be declared in a Remotion
    Zod schema (effectSchema.ts + the two component schemas)."""
    from reel_af.render.lower_third import _EFFECT_PROP_BY_KEY

    schema_text = "".join(
        (REMOTION_SRC / name).read_text(encoding="utf-8")
        for name in ("effectSchema.ts", "MiddleThird.tsx", "LowerThird.tsx")
    )

    shared_props = {prop for prop, _cast in _EFFECT_PROP_BY_KEY.values()}
    middle_extra = {"segments", "totalFrames", "verticalAnchor", "cardOpacity", "textTransform"}
    lower_extra = {"title", "boxOpacity"}
    emitted = shared_props | middle_extra | lower_extra | {"accent"}

    for prop in emitted:
        assert re.search(rf"\b{re.escape(prop)}\b", schema_text), (
            f"Remotion schema is missing prop {prop!r} that the Python side emits"
        )


def test_remotion_anim_enum_matches_tunable_values():
    anim_values = load_tunables()["anim_style"]["values"]
    effect_schema = (REMOTION_SRC / "effectSchema.ts").read_text(encoding="utf-8")
    for value in anim_values:
        assert f'"{value}"' in effect_schema, f"anim enum missing {value!r} in Remotion schema"
