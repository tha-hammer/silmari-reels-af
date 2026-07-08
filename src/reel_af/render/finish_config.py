"""B0 — ``ReelFinishConfig``: the typed schema over the finish-stage config.

Per ARCHITECTURE §10, **no tuning value or style dictionary is a literal here** —
every default is sourced from ``config/finish.json`` via ``load_finish_defaults``.
This module is the *schema* (field names + types + validation); the *values* live
in JSON. Access to the raw dict is a single hop
(``load_finish_defaults()["banner_pad_x"]``).

Kept dependency-light (pydantic + the stdlib loader only) so every sibling module
— ``captions.py`` (B3/B4), ``hooks.py`` (B6), ``image_cutins.py`` (B8) and
``finish.py`` (B9) — can import the config without heavy render deps.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reel_af.render.finish_defaults import load_finish_defaults

_D = load_finish_defaults()


def _v(key: str) -> Any:
    """A pydantic field default sourced from the JSON config (1-hop lookup)."""
    return Field(default_factory=lambda: _D[key])


def _obj(model: type[BaseModel], key: str) -> Any:
    """A pydantic sub-model default built from the JSON config dict at ``key``."""
    return Field(default_factory=lambda: model(**_D[key]))


def _base(key: str) -> Any:
    """An ``AssStyle`` field default sourced from ``ass_style_base`` in JSON."""
    return Field(default_factory=lambda: _D["ass_style_base"][key])


def _region(key: str) -> Any:
    """An ``ImageRegion`` field default sourced from ``image_region`` in JSON."""
    return Field(default_factory=lambda: _D["image_region"][key])


class AssStyle(BaseModel):
    """One ASS ``[V4+ Styles]`` row's tunable fields (values from JSON).

    Colours are ASS ``&HAABBGGRR`` strings (alpha+BGR); ``outline``/``shadow`` are
    px widths; ``border_style`` 1 = outline+shadow, 3 = opaque box.
    """

    model_config = ConfigDict(extra="forbid")

    fontname: str = _base("fontname")
    fontsize: int = _base("fontsize")
    primary: str = _base("primary")           # PrimaryColour (fill)
    secondary: str = _base("secondary")       # SecondaryColour (karaoke, unused)
    outline_colour: str = _base("outline_colour")
    back: str = _base("back")                 # BackColour (box fill when border_style=3)
    bold: bool = _base("bold")
    border_style: int = _base("border_style")
    outline: int = _base("outline")
    shadow: int = _base("shadow")


class ImageRegion(BaseModel):
    """Rectangle (px, canvas coords) that image cut-ins are scaled/cropped into."""

    model_config = ConfigDict(extra="forbid")

    x: int = _region("x")
    y: int = _region("y")
    w: int = _region("w")
    h: int = _region("h")


class ReelFinishConfig(BaseModel):
    """All finish-stage tunables — schema only; values come from ``finish.json``."""

    model_config = ConfigDict(extra="forbid")

    # ── Geometry ──────────────────────────────────────────────────────
    canvas_w: int = _v("canvas_w")
    canvas_h: int = _v("canvas_h")
    center_x: int = _v("center_x")
    caption_safe_y: int = _v("caption_safe_y")
    divider_y: int = _v("divider_y")

    # ── Caption grouping (B3) ─────────────────────────────────────────
    caption_max_words: int = _v("caption_max_words")
    caption_max_dur_s: float = _v("caption_max_dur_s")
    caption_gap_s: float = _v("caption_gap_s")
    caption_uppercase: bool = _v("caption_uppercase")
    banner_uppercase: bool = _v("banner_uppercase")

    # ── Styles (B3/B4) ────────────────────────────────────────────────
    caption_style: AssStyle = _obj(AssStyle, "caption_style")
    banner_style: AssStyle = _obj(AssStyle, "banner_style")

    # ── Banner box (V3): full-width box; text ALWAYS fills the width; the box
    #    HUGS the text height (so vertical padding is ~banner_pad_y), clamped to
    #    [box_min_h, box_max_h] so a single line is never a thin sliver. Line
    #    count is chosen by readability: use the fewest lines whose width-filling
    #    font is ≥ banner_min_readable_fs. A geometry fact: a fixed box can't hit
    #    the pad on BOTH axes for every hook aspect ratio, so we fix width + hug
    #    height. banner_render_*_ratio calibrate PIL measurements to libass.
    banner_font_ref_fs: int = _v("banner_font_ref_fs")
    banner_min_fs: int = _v("banner_min_fs")
    banner_max_fs: int = _v("banner_max_fs")
    banner_min_readable_fs: int = _v("banner_min_readable_fs")
    banner_max_lines: int = _v("banner_max_lines")
    banner_box_min_h: int = _v("banner_box_min_h")
    banner_box_max_h: int = _v("banner_box_max_h")
    banner_side_margin_px: int = _v("banner_side_margin_px")
    banner_pad_x: int = _v("banner_pad_x")
    banner_pad_y: int = _v("banner_pad_y")
    banner_line_spacing: float = _v("banner_line_spacing")
    banner_render_width_ratio: float = _v("banner_render_width_ratio")
    banner_render_height_ratio: float = _v("banner_render_height_ratio")
    banner_max_block_h: int = _v("banner_max_block_h")
    banner_text_outline: int = _v("banner_text_outline")
    banner_full_width: bool = _v("banner_full_width")
    banner_box_margin_x: int = _v("banner_box_margin_x")
    # Fixed banner text (e.g. a title) overrides the LLM hook when non-empty;
    # banner_duration_s > 0 shows the banner for that long then fades it out.
    banner_fixed_text: str = _v("banner_fixed_text")
    banner_duration_s: float = _v("banner_duration_s")
    banner_fade_in_ms: int = _v("banner_fade_in_ms")
    banner_fade_out_ms: int = _v("banner_fade_out_ms")
    # Optional Remotion animated lower-third title (see render/lower_third.py +
    # the remotion/ project) — an alternative to the ASS banner.
    lower_third_enabled: bool = _v("lower_third_enabled")
    lower_third_accent: str = _v("lower_third_accent")
    lower_third_duration_s: float = _v("lower_third_duration_s")
    lower_third_project_dir: str = _v("lower_third_project_dir")

    # Legacy single-line char-ratio fit fields (deprecated; kept for back-compat).
    banner_fit_min_fs: int = _v("banner_fit_min_fs")
    banner_fit_max_fs: int = _v("banner_fit_max_fs")
    banner_fit_edge_margin_px: int = _v("banner_fit_edge_margin_px")
    banner_fit_char_width_ratio: float = _v("banner_fit_char_width_ratio")

    # ── Divider detection (finish.py computes divider_y per reel) ──────
    divider_probe_t_s: float = _v("divider_probe_t_s")
    divider_band_lo_pct: float = _v("divider_band_lo_pct")
    divider_band_hi_pct: float = _v("divider_band_hi_pct")
    divider_sample_step_px: int = _v("divider_sample_step_px")
    divider_dark_rows: int = _v("divider_dark_rows")
    divider_min_contrast: float = _v("divider_min_contrast")

    # ── Image cut-ins (B6/B7/B8) ──────────────────────────────────────
    image_count: int = _v("image_count")
    # Image cut-in placement on the frame: "full" (default, B-roll cutaway),
    # "pip", "upper_third", "lower_third", "upper_half", "lower_half".
    image_placement: str = _v("image_placement")
    image_region: ImageRegion = _obj(ImageRegion, "image_region")
    image_min_dur_s: float = _v("image_min_dur_s")
    image_max_dur_s: float = _v("image_max_dur_s")
    image_edge_guard_s: float = _v("image_edge_guard_s")

    # ── Whisper / encode (B2, B9) ─────────────────────────────────────
    whisper_model: str = _v("whisper_model")
    whisper_device: str = _v("whisper_device")
    whisper_compute_type: str = _v("whisper_compute_type")
    encode_crf: int = _v("encode_crf")
    encode_preset: str = _v("encode_preset")


def caption_pos_tag(cfg: ReelFinishConfig) -> str:
    r"""ASS override for a caption's position: ``{\pos(center_x,caption_safe_y)}``."""
    return f"{{\\pos({cfg.center_x},{cfg.caption_safe_y})}}"


def banner_pos_tag(cfg: ReelFinishConfig) -> str:
    r"""ASS override for the banner's position: ``{\pos(center_x,divider_y)}``."""
    return f"{{\\pos({cfg.center_x},{cfg.divider_y})}}"


__all__ = [
    "AssStyle",
    "ImageRegion",
    "ReelFinishConfig",
    "caption_pos_tag",
    "banner_pos_tag",
    "load_finish_defaults",
]
