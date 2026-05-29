"""Generate ASS (Advanced SubStation Alpha) subtitle files for karaoke-style
word-by-word captions, the industry-standard approach.

Why ASS instead of per-word ffmpeg drawtext:
  • libass uses real font metrics → no alignment bugs between background and
    highlight layers (the drawtext approach had ~10-20px drift because the
    estimated per-word x positions disagreed with ffmpeg's text_w centering).
  • libass renders the same way as VLC, mpv, every karaoke tool and fansub
    player. The visual matches what people expect.
  • One small .ass file per shot replaces N drawtext filter chains. Easier to
    debug, easier to extend (fades, slides, glow effects are one-liners).
  • Native support for font kerning, complex scripts, RTL — things drawtext
    fakes badly.

Visual model:
  • Layer 0 — full card text in WHITE, visible for the card's whole window.
  • Layer 1 — same card text BUT with one word recolored YELLOW, visible only
    during that one word's timing window. Multiple Layer 1 events per card
    (one per word). libass composites them on top of Layer 0; outside any
    word's window only Layer 0 is visible.

This produces the Hormozi / MrBeast effect: white card text, current word
turns yellow, returns to white when the next word takes over.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2

from reel_af.v2.models import Card, Shot, WordTiming
from reel_af.v2.planning.safe_zone import (
    CANVAS_H,
    CANVAS_W,
    SUBTITLE_FILL,
    SUBTITLE_FONT_PX,
    SUBTITLE_HIGHLIGHT,
    SUBTITLE_STROKE,
    SUBTITLE_STROKE_PX,
    SUBTITLE_Y_PCT,
)

# Map our safe_zone color names to ASS BGR hex (ASS uses BGR, not RGB).
_NAMED_BGR: dict[str, str] = {
    "white":   "FFFFFF",
    "black":   "000000",
    "yellow":  "00FFFF",   # R=255, G=255, B=0  -> BGR 00 FF FF
    "green":   "00FF00",
    "red":     "0000FF",
    "blue":    "FF0000",
}


def _to_pysubs2_color(name: str) -> pysubs2.Color:
    """Resolve a safe_zone color name to a pysubs2.Color (RGB internally)."""
    bgr = _NAMED_BGR.get(name.lower(), "FFFFFF")
    b, g, r = int(bgr[0:2], 16), int(bgr[2:4], 16), int(bgr[4:6], 16)
    return pysubs2.Color(r, g, b)


def _inline_color_tag(name: str) -> str:
    """ASS inline color override, e.g. \\c&H00FFFF& for yellow.

    Inline tags carry BGR hex like the style colors do, but without alpha.
    """
    return r"{\c&H" + _NAMED_BGR.get(name.lower(), "FFFFFF") + "&}"


def _build_highlight_text(words: list[WordTiming], highlight_idx: int) -> str:
    """Rebuild the card text with only ``words[highlight_idx]`` recolored to
    the highlight color. Other words use the style's default (white).

    The result is the SAME logical text as the white background layer, so
    libass lays it out at identical positions — the highlighted word lands
    pixel-perfect on top of its white counterpart.
    """
    hl_open = _inline_color_tag(SUBTITLE_HIGHLIGHT)
    hl_close = _inline_color_tag(SUBTITLE_FILL)
    parts: list[str] = []
    for i, w in enumerate(words):
        token = w.word
        if i == highlight_idx:
            parts.append(f"{hl_open}{token}{hl_close}")
        else:
            parts.append(token)
    return " ".join(parts)


def _build_ass_file(
    shot: Shot,
    font_name: str = "Montserrat",
) -> pysubs2.SSAFile:
    """Build an in-memory ASS file for one shot's karaoke subtitles.

    Times are SHOT-LOCAL (ms from shot start), matching what ffmpeg sees
    when we mux this against the trimmed per-shot video. The full TTS audio
    is muxed in only at the final concat step.
    """
    ssa = pysubs2.SSAFile()

    # Canvas — libass scales positions against PlayResX/Y, so we set them
    # to match the actual render target (1080×1920). This keeps the
    # safe_zone constants directly meaningful.
    ssa.info["PlayResX"] = str(CANVAS_W)
    ssa.info["PlayResY"] = str(CANVAS_H)
    # WrapStyle=0: libass auto-wraps when a line exceeds the safe stage width.
    # We initially used WrapStyle=2 (no wrap) — long cards bled off both edges
    # of the canvas. The shot planner targets one line per card but the
    # heuristic char width underestimates wide letters; letting libass break
    # to a 2nd line is the safer fallback.
    ssa.info["WrapStyle"] = "0"

    # Style: bold sans-serif, white fill, black stroke, top-center anchored.
    # MarginV is measured from the top edge when Alignment is "top" (7,8,9).
    # We compute MarginV so the text top sits at SUBTITLE_Y_PCT * CANVAS_H.
    margin_top = int(SUBTITLE_Y_PCT * CANVAS_H)
    style = pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=SUBTITLE_FONT_PX,
        primarycolor=_to_pysubs2_color(SUBTITLE_FILL),
        outlinecolor=_to_pysubs2_color(SUBTITLE_STROKE),
        backcolor=_to_pysubs2_color("black"),
        bold=True,
        outline=SUBTITLE_STROKE_PX,
        shadow=0,
        alignment=pysubs2.Alignment.TOP_CENTER,  # \an8
        marginl=20,
        marginr=20,
        marginv=margin_top,
    )
    ssa.styles["Sub"] = style

    for card in shot.cards:
        c_start_ms = int(max(0.0, card.start_s - shot.start_s) * 1000)
        c_end_ms = int(max(c_start_ms / 1000, card.end_s - shot.start_s) * 1000)
        if c_end_ms <= c_start_ms:
            continue

        card_text = " ".join(w.word for w in card.words)

        # Layer 0 — white background for the whole card window.
        ssa.events.append(
            pysubs2.SSAEvent(
                start=c_start_ms,
                end=c_end_ms,
                style="Sub",
                text=card_text,
                layer=0,
            )
        )

        # Layer 1 — one event per word, showing the SAME card text but with
        # that word colored yellow. Active only during the word's window.
        for i, word in enumerate(card.words):
            w_start_ms = max(
                c_start_ms, int((word.start_s - shot.start_s) * 1000)
            )
            w_end_ms = min(
                c_end_ms, int((word.end_s - shot.start_s) * 1000)
            )
            if w_end_ms <= w_start_ms:
                continue
            ssa.events.append(
                pysubs2.SSAEvent(
                    start=w_start_ms,
                    end=w_end_ms,
                    style="Sub",
                    text=_build_highlight_text(card.words, i),
                    layer=1,
                )
            )

    return ssa


def write_shot_ass(shot: Shot, out_path: Path, font_name: str = "Montserrat") -> Path:
    """Write the shot's karaoke ASS file to disk. Returns the path written.

    Kept for backward compatibility / debugging; the single-pass renderer uses
    :func:`write_reel_ass` instead.
    """
    ssa = _build_ass_file(shot, font_name=font_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ssa.save(str(out_path), format_="ass")
    return out_path


# ───── Global / reel-wide ASS (single-pass renderer) ────────────────


def _build_reel_ass_file(
    shots: list[Shot],
    font_name: str = "Montserrat",
) -> pysubs2.SSAFile:
    """Build ONE ASS file spanning every shot. Timings are REEL-GLOBAL —
    they use ``card.start_s`` / ``word.start_s`` directly (already in reel
    coordinates per :mod:`reel_af.v2.models`), with no shot-local offset.

    The single-pass renderer concats the silent clips inline via ffmpeg's
    concat filter, which is sample-accurate; libass then burns the karaoke
    on the unified timeline in one encode. This eliminates the per-shot
    clock translation that the old multi-step path required.
    """
    ssa = pysubs2.SSAFile()

    ssa.info["PlayResX"] = str(CANVAS_W)
    ssa.info["PlayResY"] = str(CANVAS_H)
    # WrapStyle=0: let libass break long cards to a 2nd line if the heuristic
    # char-width planner underestimates wide letters. Same rationale as the
    # per-shot variant.
    ssa.info["WrapStyle"] = "0"

    margin_top = int(SUBTITLE_Y_PCT * CANVAS_H)
    style = pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=SUBTITLE_FONT_PX,
        primarycolor=_to_pysubs2_color(SUBTITLE_FILL),
        outlinecolor=_to_pysubs2_color(SUBTITLE_STROKE),
        backcolor=_to_pysubs2_color("black"),
        bold=True,
        outline=SUBTITLE_STROKE_PX,
        shadow=0,
        alignment=pysubs2.Alignment.TOP_CENTER,  # \an8
        marginl=20,
        marginr=20,
        marginv=margin_top,
    )
    ssa.styles["Sub"] = style

    for shot in shots:
        for card in shot.cards:
            c_start_ms = int(max(0.0, card.start_s) * 1000)
            c_end_ms = int(max(card.start_s, card.end_s) * 1000)
            if c_end_ms <= c_start_ms:
                continue

            card_text = " ".join(w.word for w in card.words)

            # Layer 0 — white background for the whole card window.
            ssa.events.append(
                pysubs2.SSAEvent(
                    start=c_start_ms,
                    end=c_end_ms,
                    style="Sub",
                    text=card_text,
                    layer=0,
                )
            )

            # Layer 1 — one event per word, recoloring just that word yellow
            # during its [start_s, end_s] window. Same card text otherwise so
            # libass lays out at identical positions.
            for i, word in enumerate(card.words):
                w_start_ms = max(c_start_ms, int(word.start_s * 1000))
                w_end_ms = min(c_end_ms, int(word.end_s * 1000))
                if w_end_ms <= w_start_ms:
                    continue
                ssa.events.append(
                    pysubs2.SSAEvent(
                        start=w_start_ms,
                        end=w_end_ms,
                        style="Sub",
                        text=_build_highlight_text(card.words, i),
                        layer=1,
                    )
                )

    return ssa


def write_reel_ass(
    shots: list[Shot],
    out_path: Path,
    font_name: str = "Montserrat",
) -> Path:
    """Write a single reel-wide karaoke ASS file covering every shot's cards.

    All timings are in reel-global seconds. This is what the single-pass
    stitch invocation feeds into the ``subtitles`` filter after concat.
    """
    ssa = _build_reel_ass_file(shots, font_name=font_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ssa.save(str(out_path), format_="ass")
    return out_path


# Re-exported for tests / debugging.
__all__ = [
    "write_shot_ass",
    "write_reel_ass",
    "_build_ass_file",
    "_build_reel_ass_file",
    "_build_highlight_text",
    "_to_pysubs2_color",
    "_inline_color_tag",
]


# Surface unused-import guards (these are intentional re-exports for
# downstream test mocking).
_ = Card
_ = WordTiming
