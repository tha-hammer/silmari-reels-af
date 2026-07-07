"""Caption timings + caption/banner ASS builders for the reel finish stage.

This productionises the delivered, proven ``enhance_reel.py`` driver (whisper
the FINAL stitched reel → group words into short phrases → burn a grouped
``Cap`` caption track + a full-duration boxed ``Banner`` at the divider). The
exact ffmpeg/whisper/ASS recipe is preserved; the only change is that every
tunable now comes from a ``ReelFinishConfig`` (B0) instead of a magic literal.

Behaviours:
  • B2 ``caption_words``  — whisper-on-final-reel → word timings in REEL time.
  • B3 ``build_caption_ass`` — group words (≤max_words / ≤max_dur / gap-split)
    into ASS ``Cap`` dialogues at ``\\pos(center_x, caption_safe_y)``.
  • B4 ``build_banner_ass`` — one full-duration ``Banner`` dialogue at
    ``\\pos(center_x, divider_y)``.
  • ``build_finish_ass`` — the single combined ASS that ``finish_reel`` burns.

``cfg`` is duck-typed: we read named attributes off ``ReelFinishConfig`` with
proven defaults as the fallback, so these builders work with the real config,
a partial override, or ``None``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

# ───── proven defaults (from the delivered enhance_reel.py) ──────────

DEFAULT_CANVAS_W = 1080
DEFAULT_CANVAS_H = 1920
DEFAULT_CENTER_X = 540
DEFAULT_CAPTION_SAFE_Y = 1344          # int(0.70·canvas_h) — clears IG/Meta + YT UI
DEFAULT_DIVIDER_Y = 772                # fallback when the divider bar isn't detected
DEFAULT_MAX_WORDS = 4
DEFAULT_MAX_DUR_S = 1.8
DEFAULT_GAP_S = 0.35                   # silence gap that forces a new phrase

# Banner font-fit defaults (used when cfg lacks the fields / cfg is None).
DEFAULT_BANNER_FIT_MIN_FS = 30
DEFAULT_BANNER_FIT_MAX_FS = 58
DEFAULT_BANNER_FIT_EDGE_MARGIN_PX = 90
DEFAULT_BANNER_FIT_CHAR_RATIO = 0.52

# Divider-detection defaults.
DEFAULT_DIVIDER_PROBE_T_S = 3.0
DEFAULT_DIVIDER_BAND_LO_PCT = 0.28
DEFAULT_DIVIDER_BAND_HI_PCT = 0.58
DEFAULT_DIVIDER_SAMPLE_STEP_PX = 8
DEFAULT_DIVIDER_DARK_ROWS = 24
DEFAULT_DIVIDER_MIN_CONTRAST = 12.0

CAPTION_STYLE_NAME = "Cap"
BANNER_STYLE_NAME = "Banner"

WHISPER_MODEL = "base.en"

# Style field defaults — one dict per named ASS style. A ``cfg.<x>_style``
# object overrides any of these via getattr; missing attrs fall through.
# High-contrast defaults, validated visually as the reel finish default.
_CAPTION_STYLE_DEFAULTS: dict[str, Any] = {
    "fontname": "Arial",
    "fontsize": 62,
    "primary": "&H00FFFFFF",           # white fill
    "secondary": "&H000000FF",
    "outline_color": "&H00000000",
    "back": "&HB0000000",              # semi-opaque dark box (alpha B0)
    "bold": 1,
    "border_style": 3,                 # opaque box behind text
    "outline": 4,
    "shadow": 0,
    "alignment": 5,                    # middle-center anchor (pos overrides)
}
_BANNER_STYLE_DEFAULTS: dict[str, Any] = {
    "fontname": "Arial",
    "fontsize": 58,
    "primary": "&H00CE227E",           # purple #7E22CE
    "secondary": "&H000000FF",
    "outline_color": "&H00FFFFFF",     # white — blends into the box edge
    "back": "&H00FFFFFF",              # opaque white box
    "bold": 1,
    "border_style": 3,                 # opaque box behind text
    "outline": 6,
    "shadow": 0,
    "alignment": 5,
}


# ───── config access (duck-typed) ───────────────────────────────────


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    val = getattr(cfg, name, None) if cfg is not None else None
    return default if val is None else val


def _style_field(style_obj: Any, name: str, default: Any) -> Any:
    val = getattr(style_obj, name, None) if style_obj is not None else None
    return default if val is None else val


def _center_x(cfg: Any) -> int:
    cx = getattr(cfg, "center_x", None) if cfg is not None else None
    if cx is not None:
        return int(cx)
    return int(_cfg(cfg, "canvas_w", DEFAULT_CANVAS_W)) // 2


# ───── B2: caption timings from the final reel ───────────────────────


def _reel_duration(reel_path: Path) -> float:
    """Container duration in seconds via ffprobe (fail-closed)."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(reel_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {reel_path}: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


def _extract_wav(reel_path: Path, wav_path: Path) -> Path:
    """Downmix to mono 16 kHz WAV — the format whisper expects."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(reel_path), "-ac", "1", "-ar", "16000", str(wav_path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg wav extract failed: {proc.stderr[-400:]}")
    return wav_path


def _whisper_transcribe(reel_path: Path, *, model: str, workdir: Optional[Path]) -> dict:
    """Run whisper-ctranslate2 on the reel's audio → parsed word-timestamp JSON.

    This is the exact recipe from the delivered driver: base.en, cpu, int8,
    word timestamps, JSON out. Isolated behind an injectable seam so the parse
    path can be unit-tested without the (heavy) whisper subprocess.
    """
    reel_path = Path(reel_path)
    stem = reel_path.stem
    workdir = Path(workdir) if workdir is not None else reel_path.parent / f".whisper_{stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    wav = _extract_wav(reel_path, workdir / f"{stem}.wav")
    proc = subprocess.run(
        ["uvx", "whisper-ctranslate2", "--model", model, "--device", "cpu",
         "--compute_type", "int8", "--word_timestamps", "True",
         "--output_format", "json", "--output_dir", str(workdir), str(wav)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"whisper failed: {proc.stderr[-400:]}")
    out_json = workdir / f"{wav.stem}.json"
    return json.loads(out_json.read_text())


def _parse_whisper_words(
    data: dict, duration: Optional[float]
) -> list[tuple[float, float, str]]:
    """Flatten whisper segments → ordered ``(start, end, text)`` word tuples.

    Empty tokens are dropped and ends are clamped to the reel duration so no
    caption can render past the final frame.
    """
    words: list[tuple[float, float, str]] = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            txt = str(w.get("word", "")).strip()
            if not txt:
                continue
            start = max(0.0, float(w["start"]))
            end = float(w["end"])
            if duration is not None:
                end = min(end, duration)
                start = min(start, end)
            if end <= start:
                continue
            words.append((start, end, txt))
    return words


def caption_words(
    reel_path: Path,
    cfg: Any = None,
    *,
    model: Optional[str] = None,
    workdir: Optional[Path] = None,
    transcribe: Optional[Callable[..., dict]] = None,
) -> list[tuple[float, float, str]]:
    """Word-level ``(start, end, text)`` timings in REEL time for a stitched reel.

    Whispering the FINAL reel means timings are already on the reel timeline —
    no source→reel mapping needed. ``cfg`` is optional (B9 passes the
    ``ReelFinishConfig``); the whisper model is taken from ``model`` >
    ``cfg.whisper_model`` > the proven default. ``transcribe`` is injectable
    for tests.
    """
    reel_path = Path(reel_path)
    duration = _reel_duration(reel_path)
    resolved_model = model or _cfg(cfg, "whisper_model", WHISPER_MODEL)
    run = transcribe if transcribe is not None else _whisper_transcribe
    data = run(reel_path, model=resolved_model, workdir=workdir)
    return _parse_whisper_words(data, duration)


# ───── Divider detection — where the banner sits ─────────────────────


def _extract_divider_frame(base_reel_path: Path, cfg: Any) -> Any:
    """Grab one frame at ``divider_probe_t_s`` and return it as a grayscale PIL image."""
    from PIL import Image  # lazy — only the divider path needs Pillow

    probe_t = float(_cfg(cfg, "divider_probe_t_s", DEFAULT_DIVIDER_PROBE_T_S))
    tmp_dir = Path(tempfile.mkdtemp(prefix="divider_"))
    frame = tmp_dir / "frame.png"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{probe_t:.3f}",
         "-i", str(base_reel_path), "-frames:v", "1", str(frame)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not frame.exists():
        raise RuntimeError(f"divider frame extract failed: {proc.stderr[-300:]}")
    return Image.open(frame).convert("L")


def _divider_from_gray(gray: Any, cfg: Any, fallback: int) -> int:
    """Find the darkest full-width horizontal band = the divider bar.

    Returns ``fallback`` when the darkest band isn't meaningfully darker than
    the median row (i.e. there's no distinct dark bar — detection failed).
    """
    lo_pct = float(_cfg(cfg, "divider_band_lo_pct", DEFAULT_DIVIDER_BAND_LO_PCT))
    hi_pct = float(_cfg(cfg, "divider_band_hi_pct", DEFAULT_DIVIDER_BAND_HI_PCT))
    step = int(_cfg(cfg, "divider_sample_step_px", DEFAULT_DIVIDER_SAMPLE_STEP_PX))
    n_dark = int(_cfg(cfg, "divider_dark_rows", DEFAULT_DIVIDER_DARK_ROWS))
    min_contrast = float(_cfg(cfg, "divider_min_contrast", DEFAULT_DIVIDER_MIN_CONTRAST))

    w, h = gray.size
    px = gray.load()
    lo, hi = int(h * lo_pct), int(h * hi_pct)
    step = max(1, step)
    xs = range(0, w, step)
    n_x = max(1, len(xs))
    rows = [(sum(px[x, y] for x in xs) / n_x, y) for y in range(lo, hi)]
    if not rows:
        return fallback
    rows.sort()
    dark = rows[: max(1, min(n_dark, len(rows)))]
    dark_y = int(sum(y for _, y in dark) / len(dark))
    dark_lum = sum(lum for lum, _ in dark) / len(dark)
    median_lum = sorted(lum for lum, _ in rows)[len(rows) // 2]
    if median_lum - dark_lum < min_contrast:
        return fallback  # no distinct dark bar — detection failed
    return dark_y


def compute_divider_y(
    base_reel_path: Path, cfg: Any = None, *, extract_frame: Optional[Callable[..., Any]] = None
) -> int:
    """Detect the banner's Y (the black divider bar) from the base reel.

    Extracts a frame, scans rows in y∈[lo·H, hi·H] for the darkest full-width
    band, and returns its center. Falls back to ``cfg.divider_y`` if the frame
    can't be read or no distinct dark bar is found. ``extract_frame`` is an
    injectable seam (``(base, cfg) -> grayscale PIL image``) for tests.
    """
    fallback = int(_cfg(cfg, "divider_y", DEFAULT_DIVIDER_Y))
    extract = extract_frame or _extract_divider_frame
    try:
        gray = extract(Path(base_reel_path), cfg)
        return _divider_from_gray(gray, cfg, fallback)
    except Exception:
        return fallback


# ───── B3: phrase grouping ───────────────────────────────────────────


def group_captions(
    words: list[tuple[float, float, str]], cfg: Any = None
) -> list[tuple[float, float, str]]:
    """Group words into short caption phrases (the proven driver heuristic).

    A phrase closes before appending a word when any is true:
      • it already holds ``caption_max_words`` words, or
      • adding the word would span more than ``caption_max_dur_s``, or
      • the gap since the last word exceeds ``caption_gap_s``.
    """
    max_words = int(_cfg(cfg, "caption_max_words", DEFAULT_MAX_WORDS))
    max_dur = float(_cfg(cfg, "caption_max_dur_s", DEFAULT_MAX_DUR_S))
    max_gap = float(_cfg(cfg, "caption_gap_s", DEFAULT_GAP_S))

    phrases: list[tuple[float, float, str]] = []
    cur: list[tuple[float, float, str]] = []
    for st, en, w in words:
        if cur and (
            len(cur) >= max_words
            or en - cur[0][0] > max_dur
            or st - cur[-1][1] > max_gap
        ):
            phrases.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
        cur.append((st, en, w))
    if cur:
        phrases.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    return phrases


# ───── ASS emission ───────────────────────────────────────────────────


def _ass_time(seconds: float) -> str:
    """ASS ``h:mm:ss.cc`` timestamp (centisecond precision)."""
    s = max(0.0, float(seconds))
    h = int(s // 3600)
    m = int(s % 3600 // 60)
    return f"{h}:{m:02d}:{s % 60:05.2f}"


def _ass_escape(text: str) -> str:
    """Neutralise characters that would corrupt the ASS override parser."""
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("{", "(").replace("}", ")")
    return text.strip()


def _script_info(cfg: Any) -> list[str]:
    return [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(_cfg(cfg, 'canvas_w', DEFAULT_CANVAS_W))}",
        f"PlayResY: {int(_cfg(cfg, 'canvas_h', DEFAULT_CANVAS_H))}",
        "WrapStyle: 2",
        "",
    ]


_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding"
)
_EVENTS_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)


def _style_line(name: str, style_obj: Any, defaults: dict[str, Any]) -> str:
    def f(key: str, *alts: str) -> Any:
        for attr in (key, *alts):
            val = getattr(style_obj, attr, None) if style_obj is not None else None
            if val is not None:
                return val
        return defaults[key]

    return (
        f"Style: {name},{f('fontname')},{int(f('fontsize'))},{f('primary')},"
        f"{f('secondary')},{f('outline_color', 'outline_colour')},{f('back')},{int(f('bold'))},"
        f"0,0,0,100,100,0,0,{int(f('border_style'))},{int(f('outline'))},"
        f"{int(f('shadow'))},{int(f('alignment'))},0,0,0,1"
    )


def _caption_style_line(cfg: Any) -> str:
    return _style_line(CAPTION_STYLE_NAME, getattr(cfg, "caption_style", None), _CAPTION_STYLE_DEFAULTS)


def _banner_style_line(cfg: Any) -> str:
    return _style_line(BANNER_STYLE_NAME, getattr(cfg, "banner_style", None), _BANNER_STYLE_DEFAULTS)


def _dialogue(
    start: float, end: float, style: str, x: int, y: int, text: str, extra_tags: str = ""
) -> str:
    return (
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,"
        f"{{\\pos({x},{y}){extra_tags}}}{_ass_escape(text)}"
    )


def compute_banner_fontsize(hook_text: str, cfg: Any = None) -> int:
    """Fit the banner font to the frame width so long hooks don't overflow.

    ``fs = max(min_fs, min(max_fs, int((canvas_w - edge_margin) /
    (len(text) * char_ratio))))`` — the proven ``banner_fix_proto.py`` formula,
    every constant a config tunable.
    """
    canvas_w = int(_cfg(cfg, "canvas_w", DEFAULT_CANVAS_W))
    min_fs = int(_cfg(cfg, "banner_fit_min_fs", DEFAULT_BANNER_FIT_MIN_FS))
    max_fs = int(_cfg(cfg, "banner_fit_max_fs", DEFAULT_BANNER_FIT_MAX_FS))
    margin = int(_cfg(cfg, "banner_fit_edge_margin_px", DEFAULT_BANNER_FIT_EDGE_MARGIN_PX))
    ratio = float(_cfg(cfg, "banner_fit_char_width_ratio", DEFAULT_BANNER_FIT_CHAR_RATIO))
    usable = canvas_w - margin
    n = max(1, len(hook_text))
    return max(min_fs, min(max_fs, int(usable / (n * ratio))))


def _caption_events(words: list[tuple[float, float, str]], cfg: Any) -> list[str]:
    x = _center_x(cfg)
    y = int(_cfg(cfg, "caption_safe_y", DEFAULT_CAPTION_SAFE_Y))
    upper = bool(_cfg(cfg, "caption_uppercase", True))
    events: list[str] = []
    for st, en, txt in group_captions(words, cfg):
        events.append(_dialogue(st, en, CAPTION_STYLE_NAME, x, y, txt.upper() if upper else txt))
    return events


def _banner_event(hook: str, dur: float, cfg: Any) -> str:
    x = _center_x(cfg)
    y = int(_cfg(cfg, "divider_y", DEFAULT_DIVIDER_Y))
    upper = bool(_cfg(cfg, "banner_uppercase", True))
    text = hook.upper() if upper else hook
    fs = compute_banner_fontsize(text, cfg)
    return _dialogue(0.0, dur, BANNER_STYLE_NAME, x, y, text, extra_tags=f"\\fs{fs}")


def _assemble(cfg: Any, styles: list[str], events: list[str]) -> str:
    lines = _script_info(cfg)
    lines += ["[V4+ Styles]", _STYLE_FORMAT, *styles, ""]
    lines += ["[Events]", _EVENTS_FORMAT, *events]
    return "\n".join(lines) + "\n"


def build_caption_ass(words: list[tuple[float, float, str]], cfg: Any = None) -> str:
    """B3 — grouped caption ASS at the safe zone. Standalone (Cap style only)."""
    return _assemble(cfg, [_caption_style_line(cfg)], _caption_events(words, cfg))


def build_banner_ass(hook: str, dur: float, cfg: Any = None) -> str:
    """B4 — one full-duration boxed banner ASS at the divider."""
    return _assemble(cfg, [_banner_style_line(cfg)], [_banner_event(hook, dur, cfg)])


def build_finish_ass(
    words: list[tuple[float, float, str]], hook: str, dur: float, cfg: Any = None
) -> str:
    """Combined ASS burned by ``finish_reel`` (B9): banner + grouped captions."""
    styles = [_caption_style_line(cfg), _banner_style_line(cfg)]
    events = [_banner_event(hook, dur, cfg), *_caption_events(words, cfg)]
    return _assemble(cfg, styles, events)


def write_ass(ass_text: str, out_path: Path) -> Path:
    """Write an ASS document to disk and return its path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(ass_text)
    return out_path


__all__ = [
    "caption_words",
    "compute_divider_y",
    "compute_banner_fontsize",
    "group_captions",
    "build_caption_ass",
    "build_banner_ass",
    "build_finish_ass",
    "write_ass",
    "CAPTION_STYLE_NAME",
    "BANNER_STYLE_NAME",
]
