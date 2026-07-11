from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from util import make_fake_provider, square_png_bytes

from reel_af.models import Essence
from reel_af.render.images import generate_first_frame


async def test_generate_first_frame_uses_explicit_model(tmp_path: Path):
    fake = make_fake_provider(image_data=square_png_bytes(256))
    provider = fake()

    await generate_first_frame(
        provider,
        "a quiet lab bench",
        0,
        tmp_path,
        model="premium/model-x",
    )

    image_calls = [kw for method, kw in fake.calls if method == "image"]
    assert image_calls and image_calls[0]["model"] == "premium/model-x"


async def test_generate_first_frame_defaults_to_env_model(tmp_path: Path):
    from reel_af.render import images

    fake = make_fake_provider(image_data=square_png_bytes(256))
    provider = fake()

    await generate_first_frame(provider, "a lab bench", 0, tmp_path)

    image_calls = [kw for method, kw in fake.calls if method == "image"]
    assert image_calls[0]["model"] == images.IMAGE_MODEL


@pytest.mark.parametrize("size", [256, 512, 1000])
async def test_carousel_crop_is_4x5_portrait(tmp_path: Path, size: int):
    fake = make_fake_provider(image_data=square_png_bytes(size))

    path = await generate_first_frame(fake(), "x", 0, tmp_path, crop="4x5")

    with Image.open(path) as image:
        assert image.size == (1080, 1350)


async def test_default_crop_still_9x16(tmp_path: Path):
    fake = make_fake_provider(image_data=square_png_bytes(512))

    path = await generate_first_frame(fake(), "x", 0, tmp_path)

    with Image.open(path) as image:
        assert image.size == (720, 1280)


def test_carousel_default_preset_is_4x5_portrait():
    from reel_af.render.presets import load_preset, preset_names

    assert "carousel-default" in preset_names()
    cfg = load_preset("carousel-default")
    assert (cfg["canvas_w"], cfg["canvas_h"]) == (1080, 1350)
    assert cfg["slide_count"] >= 1
    assert cfg["kind"] == "carousel"
    assert cfg.get("overlay") not in {"middle_third", "lower_third"}


class _StubApp:
    def __init__(self, essence: Essence):
        self._essence = essence
        self.ai_calls: list[dict] = []

    async def ai(self, *, system, user, schema):
        self.ai_calls.append({"system": system, "user": user, "schema": schema})
        return self._essence


class _FakeStoragePort:
    def __init__(self):
        self.saved = []

    async def put(self, *, run_id, idx, path):
        self.saved.append((run_id, idx, path))
        return f"stub://{run_id}/{idx}"


async def _fake_distiller(text):
    return Essence(
        core_claim="c",
        mechanism="m",
        evidence=["e"],
        content_mode="general",
        domain="tech",
    )


async def test_essence_from_text_bypasses_fetch(monkeypatch):
    from reel_af.agents import extract

    async def _boom(url):
        raise AssertionError("_fetch must not be called for text input")

    monkeypatch.setattr(extract, "_fetch", _boom)
    stub_essence = Essence(
        core_claim="Sleep debt compounds.",
        mechanism="Adenosine accrues.",
        evidence=["8 hours"],
        content_mode="general",
        domain="health",
    )
    app = _StubApp(stub_essence)

    result = await extract.essence_from_text(
        app,
        "A long research note about sleep and recovery.",
    )

    assert isinstance(result, Essence)
    assert app.ai_calls and app.ai_calls[0]["schema"] is Essence
    assert extract._SYSTEM == app.ai_calls[0]["system"]


@pytest.mark.parametrize("bad", ["", "   ", "\n\t ", None])
async def test_essence_from_text_rejects_empty(bad):
    from reel_af.agents import extract

    class _NeverApp:
        async def ai(self, **_):
            raise AssertionError("ai must not be called for empty text")

    with pytest.raises(ValueError, match="text"):
        await extract.essence_from_text(_NeverApp(), bad)


async def test_long_text_is_bounded_before_prompt():
    from reel_af.agents import extract

    huge = "word " * 20_000
    captured = {}

    class _CapApp:
        async def ai(self, *, system, user, schema):
            captured["user"] = user
            return Essence(
                core_claim="c",
                mechanism="m",
                evidence=["e"],
                content_mode="general",
                domain="d",
            )

    await extract.essence_from_text(_CapApp(), huge)

    prepared = extract._fit_text_body(huge.strip())
    assert 0 < len(prepared) <= extract.PROMPT_BODY_CHARS
    body = captured["user"].split("truncated to fit context):\n", 1)[1]
    assert body == prepared


async def test_short_text_passes_through():
    from reel_af.agents import extract

    assert extract._fit_text_body("short note") == "short note"


def test_research_to_carousel_is_registered():
    import reel_af.app as app_module

    names = [r["wrapper"].__name__ for r in app_module.reel.reasoners]
    assert "research_to_carousel" in names


async def test_missing_api_key_returns_house_error(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    out = await app_module.research_to_carousel(
        text="doc",
        slide_count=1,
        out_dir=str(tmp_path),
    )

    assert out == {"error": "OPENROUTER_API_KEY not set in env."}


async def test_control_plane_call_resolves_real_deps(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = make_fake_provider(image_data=square_png_bytes(300))

    monkeypatch.setattr(app_module, "OpenRouterProvider", lambda *a, **k: fake(), raising=False)
    monkeypatch.setattr(app_module, "_default_storage_port", lambda: _FakeStoragePort(), raising=False)
    monkeypatch.setattr(
        app_module,
        "plan_carousel_prompts",
        lambda app, essence, n: [f"p{i}" for i in range(n)],
        raising=False,
    )
    monkeypatch.setattr(app_module, "essence_from_text", lambda app, text: _fake_distiller(text), raising=False)

    out = await app_module.research_to_carousel(
        text="doc",
        slide_count=1,
        out_dir=str(tmp_path),
    )

    assert [slide["idx"] for slide in out["slides"]] == [0]
    assert out["slides"][0]["status"] == "ok"
    assert out["slides"][0]["image_ref"] == f"stub://{out['run_id']}/0"


@pytest.mark.parametrize("n", [1, 3, 5])
async def test_carousel_returns_ordered_slides(tmp_path: Path, monkeypatch, n: int):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = make_fake_provider(image_data=square_png_bytes(300))
    storage = _FakeStoragePort()

    out = await app_module.research_to_carousel(
        text="a research doc about batteries",
        slide_count=n,
        out_dir=str(tmp_path),
        provider=fake(),
        storage=storage,
        distiller=_fake_distiller,
        prompt_planner=lambda essence, count: [f"slide prompt {i}" for i in range(count)],
    )

    slides = out["slides"]
    assert [slide["idx"] for slide in slides] == list(range(n))
    assert all(slide["image_prompt"] == f"slide prompt {slide['idx']}" for slide in slides)
    assert all(slide["image_ref"] == f"stub://{out['run_id']}/{slide['idx']}" for slide in slides)
    assert all(slide["status"] == "ok" for slide in slides)
    assert len(slides) == n
    assert out["out_dir"] == str(tmp_path)


async def test_planner_wrong_count_raises(tmp_path: Path, monkeypatch):
    import reel_af.app as app_module

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = make_fake_provider(image_data=square_png_bytes(300))

    with pytest.raises(ValueError, match="expected 3"):
        await app_module.research_to_carousel(
            text="doc",
            slide_count=3,
            out_dir=str(tmp_path),
            provider=fake(),
            storage=_FakeStoragePort(),
            distiller=_fake_distiller,
            prompt_planner=lambda essence, count: ["only", "two"],
        )


async def test_plan_carousel_prompts_returns_exactly_n():
    import reel_af.app as app_module

    class _PromptApp:
        def __init__(self):
            self.ai_calls = []

        async def ai(self, *, system, user, schema):
            self.ai_calls.append({"system": system, "user": user, "schema": schema})
            return ["prompt A", "prompt B", "prompt C", "extra"]

    essence = Essence(
        core_claim="c",
        mechanism="m",
        evidence=["e"],
        content_mode="general",
        domain="tech",
    )
    app = _PromptApp()

    prompts = await app_module.plan_carousel_prompts(app, essence, 3)

    assert prompts == ["prompt A", "prompt B", "prompt C"]
    assert app.ai_calls and app.ai_calls[0]["schema"] == list[str]


@pytest.mark.parametrize("blank", ["", "   "])
async def test_blank_model_falls_back_to_default(tmp_path: Path, blank: str):
    from reel_af.render import images

    fake = make_fake_provider(image_data=square_png_bytes(256))

    await generate_first_frame(fake(), "x", 0, tmp_path, model=blank)

    image_calls = [kw for method, kw in fake.calls if method == "image"]
    assert image_calls[0]["model"] == images.IMAGE_MODEL
