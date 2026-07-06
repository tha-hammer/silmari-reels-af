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
    """Caption "Cap" style — white, thick outline, bottom-of-safe-zone."""
    return AssStyle(
        fontname="Arial",
        fontsize=58,
        primary="&H00FFFFFF",
        outline_colour="&H00000000",
        back="&H00000000",
        bold=True,
        border_style=1,
        outline=5,
        shadow=2,
    )


def _banner_style() -> AssStyle:
    """Banner style — lime text in an opaque box, sits on the divider bar."""
    return AssStyle(
        fontname="Arial",
        fontsize=44,
        primary="&H0000FFEA",    # lime
        outline_colour="&H00000000",
        back="&HAA1A1A1A",       # semi-opaque dark box
        bold=True,
        border_style=3,          # opaque box
        outline=0,
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
    caption_safe_y: int = 1330   # ≈70% height — clears IG/Meta + YT UI
    divider_y: int = 772         # talking-head / screenshare divider bar

    # ── Caption grouping (B3) ─────────────────────────────────────────
    caption_max_words: int = 4
    caption_max_dur_s: float = 1.8
    caption_gap_s: float = 0.35  # silence gap that forces a new phrase
    caption_uppercase: bool = True
    banner_uppercase: bool = True

    # ── Styles (B3/B4) ────────────────────────────────────────────────
    caption_style: AssStyle = Field(default_factory=_caption_style)
    banner_style: AssStyle = Field(default_factory=_banner_style)

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
