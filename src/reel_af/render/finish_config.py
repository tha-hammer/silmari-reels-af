"""B0 — ``ReelFinishConfig``: the single, no-literal home for finish tunables.

Every number the finish stage burns into a reel — caption safe-zone Y, banner
divider Y, grouping thresholds, ASS styles, image cut-in count / region /
duration — lives here so the render code carries no magic literals. Defaults
are the exact values proven on the ppWtqV0auok crisp renders
(``enhance_reel.py``): caption ``\\pos(540,1330)``, banner ``\\pos(540,772)``,
Cap/Banner ASS styles, ≤4-word / ≤1.8s caption phrases.

Kept as a dependency-light module (pydantic only, no ffmpeg/whisper imports) so
every sibling module — ``captions.py`` (B3/B4), ``hooks.py`` (B6),
``image_cutins.py`` (B8) and ``finish.py`` (B9) — can import the config without
dragging heavy render deps into unit tests.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AssStyle(BaseModel):
    """One ASS ``[V4+ Styles]`` row's tunable fields.

    Colours are ASS ``&HAABBGGRR`` strings (alpha+BGR); ``outline`` and
    ``shadow`` are widths in px; ``border_style`` 1 = outline+shadow, 3 =
    opaque box (used by the banner).
    """

    model_config = ConfigDict(extra="forbid")

    fontname: str = "Arial"
    fontsize: int = 58
    primary: str = "&H00FFFFFF"          # PrimaryColour (fill)
    secondary: str = "&H000000FF"        # SecondaryColour (unused, karaoke)
    outline_colour: str = "&H00000000"   # OutlineColour
    back: str = "&H00000000"             # BackColour (box fill when border_style=3)
    bold: bool = True
    border_style: int = 1
    outline: int = 5                     # outline width px
    shadow: int = 2                      # shadow depth px


def _caption_style() -> AssStyle:
    """Caption "Cap" style — high contrast: white text in a semi-opaque dark box.

    Validated visually as the default: white fill on a translucent dark card
    (``BorderStyle=3`` + ``BackColour=&HB0000000``) reads on any footage.
    """
    return AssStyle(
        fontname="Arial",
        fontsize=62,
        primary="&H00FFFFFF",     # white fill
        outline_colour="&H00000000",
        back="&HB0000000",        # semi-opaque dark box (alpha B0)
        bold=True,
        border_style=3,           # opaque box behind text
        outline=4,
        shadow=0,
    )


def _banner_style() -> AssStyle:
    """Banner style — high contrast: PURPLE text on an OPAQUE WHITE box.

    Chosen by the user over the earlier lime-on-dark look. Sits on the divider
    bar; the per-hook font size is computed at render time (``banner_fit_*``).
    """
    return AssStyle(
        fontname="Arial",
        fontsize=58,
        primary="&H00CE227E",     # purple #7E22CE (ASS is &HAABBGGRR)
        outline_colour="&H00FFFFFF",  # white — blends into the box edge
        back="&H00FFFFFF",        # opaque white box
        bold=True,
        border_style=3,           # opaque box
        outline=6,
        shadow=0,
    )


class ImageRegion(BaseModel):
    """Rectangle (px, canvas coords) that image cut-ins are scaled/cropped into.

    Defaults to the screenshare pane below the divider (y≈800..1920).
    """

    model_config = ConfigDict(extra="forbid")

    x: int = 0
    y: int = 800
    w: int = 1080
    h: int = 1120


class ReelFinishConfig(BaseModel):
    """All finish-stage tunables, one config, no literals in the render code."""

    model_config = ConfigDict(extra="forbid")

    # ── Geometry ──────────────────────────────────────────────────────
    canvas_w: int = 1080
    canvas_h: int = 1920
    center_x: int = 540
    caption_safe_y: int = 1344   # int(0.70·canvas_h) — clears IG/Meta + YT UI
    divider_y: int = 772         # fallback when compute_divider_y can't detect the bar

    # ── Caption grouping (B3) ─────────────────────────────────────────
    caption_max_words: int = 4
    caption_max_dur_s: float = 1.8
    caption_gap_s: float = 0.35  # silence gap that forces a new phrase
    caption_uppercase: bool = True
    banner_uppercase: bool = True

    # ── Styles (B3/B4) ────────────────────────────────────────────────
    caption_style: AssStyle = Field(default_factory=_caption_style)
    banner_style: AssStyle = Field(default_factory=_banner_style)

    # ── Banner font-fit (B4) — shrink long hooks to fit the frame width ─
    banner_fit_min_fs: int = 30
    banner_fit_max_fs: int = 58
    banner_fit_edge_margin_px: int = 90      # usable width = canvas_w - this
    banner_fit_char_width_ratio: float = 0.52  # avg glyph width ÷ fontsize

    # ── Divider detection (finish.py computes divider_y per reel) ──────
    divider_probe_t_s: float = 3.0           # frame timestamp to sample
    divider_band_lo_pct: float = 0.28        # search y ∈ [lo·H, hi·H]
    divider_band_hi_pct: float = 0.58
    divider_sample_step_px: int = 8          # x-sampling stride per row
    divider_dark_rows: int = 24              # darkest N rows → band center
    divider_min_contrast: float = 12.0       # median−dark luminance to trust it

    # ── Image cut-ins (B6/B7/B8) ──────────────────────────────────────
    image_count: int = 3               # 2-3 per reel over the screenshare pane
    image_region: ImageRegion = Field(default_factory=ImageRegion)
    image_min_dur_s: float = 2.0
    image_max_dur_s: float = 3.0
    image_edge_guard_s: float = 2.0    # no cut-in in first/last N seconds

    # ── Whisper / encode (B2, B9) ─────────────────────────────────────
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    encode_crf: int = 19
    encode_preset: str = "fast"


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
]
