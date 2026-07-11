"""Browser contract checks for failure/result display behavior."""

from __future__ import annotations

import json
import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[2] / "web" / "index.html"
RENDER_CONFIG = INDEX_HTML.parents[1] / "src" / "reel_af" / "render" / "config"


def _config(html: str) -> dict:
    """Parse the inline ``#config`` JSON block out of index.html."""
    m = re.search(
        r'<script type="application/json" id="config">(.*?)</script>', html, re.DOTALL
    )
    assert m, "inline #config block not found"
    return json.loads(m.group(1))


def _expected_preset_details(raw: dict) -> dict:
    """Map a ``presets.json`` (snake_case) entry to the UI details schema. Fields
    not present for a preset are omitted (plan §UI Config Mapping)."""
    details: dict = {
        "description": raw["description"],
        "reelSeconds": raw["reel_seconds"],
        "canvas": {"w": raw["canvas_w"], "h": raw["canvas_h"]},
        "overlay": raw["overlay"],
        "remotionComposition": raw["remotion_composition"],
        "overlayAccent": raw["overlay_accent"],
    }
    phrase = {}
    if "phrase_max_words" in raw:
        phrase["maxWords"] = raw["phrase_max_words"]
    if "phrase_max_dur_s" in raw:
        phrase["maxDurationS"] = raw["phrase_max_dur_s"]
    if "phrase_gap_s" in raw:
        phrase["gapS"] = raw["phrase_gap_s"]
    if "phrase_hold_s" in raw:
        phrase["holdS"] = raw["phrase_hold_s"]
    if "phrase_uppercase" in raw:
        phrase["uppercase"] = raw["phrase_uppercase"]
    if "overlay_vertical_anchor" in raw:
        phrase["verticalAnchor"] = raw["overlay_vertical_anchor"]
    if "captions_burned_source" in raw:
        phrase["captionsBurnedSource"] = raw["captions_burned_source"]
    if phrase:
        details["phrase"] = phrase
    lower = {}
    if "lower_third_duration_s" in raw:
        lower["durationS"] = raw["lower_third_duration_s"]
    if lower:
        details["lowerThird"] = lower
    images = {}
    if "image_placement" in raw:
        images["placement"] = raw["image_placement"]
    if "image_count" in raw:
        images["count"] = raw["image_count"]
    if "image_every_s" in raw:
        images["everyS"] = raw["image_every_s"]
    if "zoom" in raw:
        images["zoom"] = raw["zoom"]
    if images:
        details["images"] = images
    return details


def _expected_finish_defaults(src: dict) -> dict:
    """Map ``finish.json`` to the UI global-finish-defaults schema (plan §UI Config Mapping)."""
    return {
        "readOnly": True,
        "scope": "global_finish_stage_defaults",
        "geometry": {
            "canvas": {"w": src["canvas_w"], "h": src["canvas_h"]},
            "centerX": src["center_x"],
            "captionSafeY": src["caption_safe_y"],
            "dividerY": src["divider_y"],
            "imageRegion": {
                "x": src["image_region"]["x"],
                "y": src["image_region"]["y"],
                "w": src["image_region"]["w"],
                "h": src["image_region"]["h"],
            },
        },
        "captions": {
            "maxWords": src["caption_max_words"],
            "maxDurationS": src["caption_max_dur_s"],
            "gapS": src["caption_gap_s"],
            "uppercase": src["caption_uppercase"],
        },
        "images": {
            "count": src["image_count"],
            "placement": src["image_placement"],
            "minDurationS": src["image_min_dur_s"],
            "maxDurationS": src["image_max_dur_s"],
            "edgeGuardS": src["image_edge_guard_s"],
        },
        "encode": {
            "crf": src["encode_crf"],
            "preset": src["encode_preset"],
            "contextOnly": True,
        },
    }


def test_succeeded_error_payload_is_not_finished_as_downloadable_result():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'const errorMessage = resultErrorMessage(j.result) || resultErrorMessage(j);' in html
    assert "if (errorMessage) throw new Error(`execution failed: ${errorMessage}`);" in html
    assert "function resultErrorMessage(result)" in html
    assert 'const resultError = resultErrorMessage(result);' in html
    assert "if (resultError) throw new Error(`execution failed: ${resultError}`);" in html


# ─────────────────── Behavior 1: UI count bounds match backend ───────────────────
def test_ui_count_bounds_match_backend_constants():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    from reel_jobs import COMPOSITE_COUNT_DEFAULT, COMPOSITE_COUNT_MAX, COMPOSITE_COUNT_MIN

    assert cfg["ui"]["countDefault"] == COMPOSITE_COUNT_DEFAULT
    assert cfg["ui"]["countMin"] == COMPOSITE_COUNT_MIN
    assert cfg["ui"]["countMax"] == COMPOSITE_COUNT_MAX


# ─────────────────── Behavior 4: UI renders safe preset metadata ───────────────────
def test_composite_preset_details_match_render_config_exactly():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)
    source = json.loads((RENDER_CONFIG / "presets.json").read_text(encoding="utf-8"))

    by_id = {p["id"]: p for p in cfg["presets"]}
    for preset_id, raw in source.items():
        if raw.get("kind") == "carousel":
            assert preset_id not in by_id
            continue
        assert by_id[preset_id]["details"] == _expected_preset_details(raw)


def test_composite_preset_details_omit_operator_and_local_paths():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)
    serialized = json.dumps(cfg["presets"])
    for unsafe in ("remotion_project_dir", "lower_third_project_dir", "out_dir",
                   "whisper", "encode_"):
        assert unsafe not in serialized


def test_preset_renderer_uses_text_content_for_detail_rows():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "function renderPresetDetails" in html
    details_fn = html.split("function renderPresetDetails", 1)[1].split("function", 1)[0]
    assert ".textContent" in details_fn
    assert ".innerHTML" not in details_fn


# ─────────── Behavior 5: count control is composite-only + in browser input ───────────
def test_count_control_contract_exists_and_is_composite_only():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    assert cfg["ui"]["countDefault"] == 1
    assert cfg["ui"]["countMin"] == 1
    assert cfg["ui"]["countMax"] == 12
    assert 'id="countInput"' in html
    assert "function selectedCount" in html
    assert "function setCount" in html
    assert "function renderJobSettings" in html
    assert "KIND_TOPIC" in html
    assert "state.preset.kind === KIND_TOPIC" in html
    assert "count: selectedCount()" in html


def test_build_input_does_not_send_legacy_url_source_or_finish_defaults():
    html = INDEX_HTML.read_text(encoding="utf-8")

    build_input = html.split("function buildInput", 1)[1].split("function goToLogin", 1)[0]
    assert "source: u" not in build_input
    assert "finishDefaults" not in build_input
    assert "finish_config" not in build_input
    assert "canvas_w" not in build_input
    assert "caption_safe_y" not in build_input


# ─────────────────── Behavior 6: global finish defaults are read-only ───────────────────
def test_finish_defaults_match_render_config_exactly():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)
    source = json.loads((RENDER_CONFIG / "finish.json").read_text(encoding="utf-8"))

    assert cfg["finishDefaults"] == _expected_finish_defaults(source)
    assert cfg["finishDefaults"]["readOnly"] is True
    assert cfg["finishDefaults"]["scope"] == "global_finish_stage_defaults"


def test_finish_defaults_are_not_submitted():
    html = INDEX_HTML.read_text(encoding="utf-8")

    build_input = html.split("function buildInput", 1)[1].split("function goToLogin", 1)[0]
    assert "finishDefaults" not in build_input
    assert "finish_config" not in build_input
    assert "canvas_w" not in build_input
    assert "caption_safe_y" not in build_input


# ─────────────────── Behavior 7: browser async promises are bounded ───────────────────
def test_execute_pending_retries_are_bounded():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    assert cfg["api"]["submitPendingTimeoutMs"] > 0
    execute_fn = html.split("async function execute", 1)[1].split("async function poll", 1)[0]
    assert "submitPendingStartedAt" in execute_fn
    assert "CFG.api.submitPendingTimeoutMs" in execute_fn
    assert "Idempotency-Key" in execute_fn
    assert "CFG.ui.idempotentPendingCode" in execute_fn


def test_poll_handles_retry_after_for_transient_non_ok_statuses():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "function retryAfterMs" in html
    poll_fn = html.split("async function poll", 1)[1].split("function mapStatus", 1)[0]
    assert "pollResponse.status === STATUS_UNAUTHORIZED" in poll_fn
    assert "isTransientPollStatus" in poll_fn
    assert "Retry-After" in poll_fn


# ─────────────────── Behavior 8: backend/UI contract parity ───────────────────
def test_visible_preset_targets_match_backend_allowlist():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    from reel_jobs import (
        ALLOWLISTED_TARGETS,
        TARGET_COMPOSITE,
        TARGET_TEXT_CAROUSEL,
        TARGET_TEXT_REEL,
        TARGET_TOPIC,
    )

    targets = {p["target"] for p in cfg["presets"]}
    assert targets == {TARGET_COMPOSITE, TARGET_TOPIC}
    assert set(ALLOWLISTED_TARGETS) == {
        TARGET_COMPOSITE,
        TARGET_TOPIC,
        TARGET_TEXT_CAROUSEL,
        TARGET_TEXT_REEL,
    }


def test_ui_status_aliases_are_known_by_backend_normalizer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    cfg = _config(html)

    from reel_jobs import normalize_reel_status

    for status in cfg["ui"]["statusStageByExecutionStatus"]:
        assert normalize_reel_status(status) in {"queued", "producing", "succeeded"}
    for status in cfg["ui"]["terminalFailureStatuses"]:
        assert normalize_reel_status(status) in {"failed", "cancelled"}
