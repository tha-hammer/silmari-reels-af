"""AF-ohu / B1 — DSL cut-ins DRAW through ONE subsystem (no double-composite).

Closure: "a cut-in renders in its window, once."
- SOURCE (seed only): the A1 fixture triple (composite.ts.md + words.json +
  hook-plan.json with a zoom + a visual cut-in) over a real lavfi source mp4.
- TRIGGER: dsl_hooks_to_reels — the worker call that maps AND draws cut-ins.
- OBSERVABLE: frame probes — the frame inside the zoom window differs from the
  no-cut-in render at the same timestamp; the frame outside the window does
  not; canvas preserved (1080x1920 / 30fps).
- FORBIDDEN SPAN (real, never mocked): map_cut_ins, apply_overlays, the
  overlay filtergraph + ffmpeg exec, footage stitch, finish.
- EXECUTION: requires ffmpeg/ffprobe; fails loudly if absent (never skips
  green).

Single-subsystem rule: cut-ins draw via render/overlay_stitch (overlays.py);
finish's LLM-picked image_cutins are gated off for this workflow so exactly
one subsystem composites overlays.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from reel_af import app as app_mod
from reel_af.app import _finish_config_for, _overlay_plan_for, dsl_hooks_to_reels
from reel_af.dsl.models import (
    BlackSegment,
    FootageReel,
    SourceSegment,
    Transition,
)
from reel_af.render.overlays import CutInOverlay

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

FIXTURES = Path(__file__).resolve().parent.parent / "dsl" / "fixtures"
A1_SOURCE_URL = "https://www.youtube.com/watch?v=abc123"

# Fixture clip 1: segment 1 covers source [4.12, ~22.1); its zoom cut-in is
# source [5.34, 7.0) -> reel-relative ≈ [1.22, 2.88).
REEL_T_IN_ZOOM_WINDOW = 2.0
REEL_T_OUTSIDE_WINDOW = 10.0


def _require_ffmpeg() -> None:
    """Fail-closed: a BLOCKING closure test must never skip to green."""
    if not FFMPEG or not FFPROBE:
        pytest.fail("B1 closure requires ffmpeg + ffprobe on PATH (fail-closed)")


# ── Harness (mirrors tests/test_dsl_hooks_worker_closure.py) ─────────


class _FakeTextProvider:
    """Stands in for the LLM only — the render mechanics stay REAL."""

    def ai(self, system=None, user=None, schema=None, **kw):
        if schema is not None:
            return {"hook": "THEY DON'T REASON", "moments": []}
        return "THEY DON'T REASON"


class _FakeImageProvider:
    """Stands in for image GENERATION only — returns a real PNG so the ffmpeg
    overlay burn that consumes it is real."""

    async def generate_image(self, *, prompt=None, model=None, n=1, **kw):
        import base64
        import io

        buf = io.BytesIO()
        Image.new("RGB", (1024, 1024), (200, 30, 30)).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        class _Img:
            b64_json = b64
            url = None

        class _Resp:
            images = [_Img()]

        return _Resp()


class _CapturingUploader:
    """Delivers a URL and keeps the local reel path for frame probing."""

    def __init__(self):
        self.local_path: str | None = None

    def __call__(self, local_path, *, run_id, filename=None, **kw):
        self.local_path = str(local_path)
        return f"https://bucket.example.com/outputs/{run_id}/{filename or 'reel.mp4'}"


@pytest.fixture(scope="module")
def lavfi_source(tmp_path_factory) -> Path:
    """A real synthetic source mp4 — stands in for the downloaded segment."""
    _require_ffmpeg()
    out = tmp_path_factory.mktemp("cutin-src") / "source.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", "90", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(out)],
        capture_output=True, text=True, timeout=120, check=True,
    )
    return out


def _refs(hook_ref: Path | str) -> dict:
    return {
        "source_url": A1_SOURCE_URL,
        "composite_ref": str(FIXTURES / "a1_composite.ts.md"),
        "words_ref": str(FIXTURES / "source.words.json"),
        "hook_ref": str(hook_ref),
        "clip_idx": 1,
    }


def _hook_plan_without_cutins(tmp_path: Path) -> Path:
    plan = json.loads((FIXTURES / "a1_hook_plan.json").read_text())
    for clip in plan["clips"]:
        clip.pop("cut_ins", None)
    out = tmp_path / "hook_plan_no_cutins.json"
    out.write_text(json.dumps(plan))
    return out


def _run_worker(refs: dict, lavfi_source: Path, out_dir: Path) -> tuple[dict, str]:
    uploader = _CapturingUploader()
    result = asyncio.run(
        dsl_hooks_to_reels(
            **refs,
            out_dir=str(out_dir),
            fetch_segment=lambda req: lavfi_source,
            uploader=uploader,
            text_provider=_FakeTextProvider(),
            image_provider=_FakeImageProvider(),
        )
    )
    assert "error" not in result, f"worker failed: {result}"
    assert uploader.local_path, "uploader never received the rendered reel"
    return result, uploader.local_path


def _frame_at(video: str, t_s: float, out_png: Path) -> Image.Image:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-ss", f"{t_s:.3f}", "-i", video,
         "-frames:v", "1", str(out_png)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return Image.open(out_png).convert("RGB")


def _changed_fraction(a: Image.Image, b: Image.Image, threshold: int = 40) -> float:
    """Fraction of pixels whose max channel delta exceeds ``threshold``.

    Mean-abs-diff is too weak here: a 1.5x zoom on testsrc2 keeps the flat
    color quadrants aligned and only erases fine features, so the mean stays
    low while a large pixel population visibly changes.
    """
    red, green, blue = ImageChops.difference(a, b).split()
    max_delta = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = max_delta.histogram()
    changed = sum(count for value, count in enumerate(histogram) if value > threshold)
    return changed / sum(histogram)


def _video_props(video: str) -> tuple[int, int, float]:
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate", "-of", "json", video],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(proc.stdout)["streams"][0]
    num, den = stream["avg_frame_rate"].split("/")
    return stream["width"], stream["height"], float(num) / float(den)


# ── Behavior 1 (CLOSURE, BLOCKING): draws in-window, canvas preserved ─


def test_cutin_draws_within_window_and_preserves_canvas(lavfi_source, tmp_path):
    _require_ffmpeg()

    result, with_cutins = _run_worker(
        _refs(FIXTURES / "a1_hook_plan.json"), lavfi_source, tmp_path / "with"
    )
    assert result["cut_in_count"] == 2

    _, without_cutins = _run_worker(
        _refs(_hook_plan_without_cutins(tmp_path)), lavfi_source, tmp_path / "without"
    )

    width, height, fps = _video_props(with_cutins)
    assert (width, height) == (1080, 1920)
    assert fps == pytest.approx(30, abs=0.5)

    in_frac = _changed_fraction(
        _frame_at(with_cutins, REEL_T_IN_ZOOM_WINDOW, tmp_path / "in_a.png"),
        _frame_at(without_cutins, REEL_T_IN_ZOOM_WINDOW, tmp_path / "in_b.png"),
    )
    out_frac = _changed_fraction(
        _frame_at(with_cutins, REEL_T_OUTSIDE_WINDOW, tmp_path / "out_a.png"),
        _frame_at(without_cutins, REEL_T_OUTSIDE_WINDOW, tmp_path / "out_b.png"),
    )
    # Inside the window the zoomed frame differs across a visible pixel
    # population; outside it only re-encode noise remains. The relative gap
    # proves window-scoped drawing.
    assert in_frac > 0.02, f"no visible cut-in inside window (frac={in_frac:.4f})"
    assert in_frac > 4 * out_frac, (
        f"cut-in not scoped to its window (in={in_frac:.4f}, out={out_frac:.4f})"
    )


# ── Behavior 2: overlay plan grouping ────────────────────────────────


def _two_segment_reel() -> FootageReel:
    return FootageReel(
        source_url=A1_SOURCE_URL,
        segments=[
            SourceSegment(segment_id="seg-a", source_url=A1_SOURCE_URL,
                          start_s=0.0, end_s=5.0, text="a"),
            BlackSegment(duration_s=1.0),
            SourceSegment(segment_id="seg-b", source_url=A1_SOURCE_URL,
                          start_s=10.0, end_s=15.0, text="b"),
        ],
        transitions=[
            Transition(before_index=0, after_index=1, effect="none", duration_s=0.0),
            Transition(before_index=1, after_index=2, effect="none", duration_s=0.0),
        ],
        duration_s=11.0,
    )


def test_overlay_plan_groups_cutins_by_intersecting_source_segment():
    reel = _two_segment_reel()
    zoom_a = CutInOverlay(type="zoom", at_s=1.0, until_s=2.0)
    spanning = CutInOverlay(
        type="visual", at_s=4.5, until_s=11.0, image_prompt="pop-in"
    )
    plan = _overlay_plan_for([zoom_a, spanning], reel)

    assert set(plan) == {"seg-a", "seg-b"}
    assert plan["seg-a"] == [zoom_a, spanning]  # spanning clamps into seg-a
    assert plan["seg-b"] == [spanning]  # ...and into seg-b; zoom_a absent
    assert all("black" not in key for key in plan)


def test_overlay_plan_empty_for_no_cutins():
    assert _overlay_plan_for([], _two_segment_reel()) == {}


# ── Behaviors 3-4: wiring — drawn exactly once / passthrough ─────────


def _patch_fast_render(monkeypatch, calls: list):
    def fake_download_segments(plan, out_dir, fetch_segment):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        calls.append("download")
        return {}

    async def fake_stitch(plan, assets, out_dir, *, run_id):
        calls.append(("stitch", assets))
        path = Path(out_dir) / "base.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"base")
        return path

    async def fake_finish(base, ctx, cfg, *, out_dir, raw):
        calls.append("finish")
        path = Path(out_dir) / "final.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"final")
        return path

    monkeypatch.setattr(app_mod, "download_segments", fake_download_segments)
    monkeypatch.setattr(app_mod, "stitch_footage_reel", fake_stitch)
    monkeypatch.setattr(app_mod, "finish_reel", fake_finish)


def test_cutins_route_through_apply_overlays_exactly_once(monkeypatch, tmp_path):
    calls: list = []
    overlay_calls: list = []
    _patch_fast_render(monkeypatch, calls)

    substituted = {"seg-1": object()}

    async def fake_apply_overlays(reel, segment_assets, overlay_plan, out_dir,
                                  run_id, *, image_provider, concurrency=None):
        overlay_calls.append(overlay_plan)
        return substituted

    monkeypatch.setattr(app_mod, "apply_overlays", fake_apply_overlays)

    result = asyncio.run(
        dsl_hooks_to_reels(
            **_refs(FIXTURES / "a1_hook_plan.json"),
            out_dir=str(tmp_path),
            fetch_segment=lambda req: tmp_path / "unused.mp4",
            uploader=lambda p, *, run_id, filename=None: f"https://b.example/{run_id}",
            text_provider=_FakeTextProvider(),
            image_provider=_FakeImageProvider(),
        )
    )
    assert "error" not in result, f"worker failed: {result}"
    assert len(overlay_calls) == 1, "cut-ins must be drawn exactly once"
    plan = overlay_calls[0]
    assert plan, "overlay plan must carry the clip's cut-ins"
    assert all(cut_ins for cut_ins in plan.values())
    # The overlaid asset map is what stitch consumes.
    stitch_assets = next(c[1] for c in calls if isinstance(c, tuple) and c[0] == "stitch")
    assert stitch_assets is substituted


def test_no_cutins_skips_overlay_stage(monkeypatch, tmp_path):
    calls: list = []
    _patch_fast_render(monkeypatch, calls)

    async def fail_apply_overlays(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("apply_overlays must not run for zero cut-ins")

    monkeypatch.setattr(app_mod, "apply_overlays", fail_apply_overlays)

    result = asyncio.run(
        dsl_hooks_to_reels(
            **_refs(_hook_plan_without_cutins(tmp_path)),
            out_dir=str(tmp_path / "run"),
            fetch_segment=lambda req: tmp_path / "unused.mp4",
            uploader=lambda p, *, run_id, filename=None: f"https://b.example/{run_id}",
            text_provider=_FakeTextProvider(),
            image_provider=_FakeImageProvider(),
        )
    )
    assert "error" not in result, f"worker failed: {result}"
    assert result["cut_in_count"] == 0


# ── Behavior 5: single-subsystem gate ────────────────────────────────


def test_finish_config_gates_image_cutins_off_for_dsl_hooks():
    """Exactly one subsystem composites overlays: DSL cut-ins draw via
    overlay_stitch, so finish's LLM-picked image cut-ins stay off even when
    an image provider is available."""
    assert _finish_config_for(_FakeImageProvider()).image_count == 0
    assert _finish_config_for(None).image_count == 0


# ── Behavior 6: fail-closed visual cut-in without image provider ─────


def test_visual_cutin_without_image_provider_fails_closed(monkeypatch, tmp_path):
    calls: list = []
    _patch_fast_render(monkeypatch, calls)
    monkeypatch.setattr(app_mod, "_media_provider", lambda: None)

    result = asyncio.run(
        dsl_hooks_to_reels(
            **_refs(FIXTURES / "a1_hook_plan.json"),
            out_dir=str(tmp_path),
            fetch_segment=lambda req: tmp_path / "unused.mp4",
            uploader=lambda p, *, run_id, filename=None: f"https://b.example/{run_id}",
            text_provider=_FakeTextProvider(),
            image_provider=None,  # falls back to (patched) production default
        )
    )
    assert result.get("error") == "dsl_cutin_image_unavailable"
    assert "download" not in calls, "must fail closed before any render side effect"
