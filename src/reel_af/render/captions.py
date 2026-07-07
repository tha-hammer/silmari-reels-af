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

# Banner two-line box-fit defaults (used when cfg lacks the fields / cfg is None).
DEFAULT_BANNER_REF_FS = 100
DEFAULT_BANNER_MAX_FS = 200
DEFAULT_BANNER_MAX_LINES = 3
DEFAULT_BANNER_BOX_H = 210
DEFAULT_BANNER_SIDE_MARGIN = 40
DEFAULT_BANNER_PAD_X = 40
DEFAULT_BANNER_PAD_Y = 22
DEFAULT_BANNER_LINE_SPACING = 0.98
DEFAULT_BANNER_MAX_BLOCK_H = 250
DEFAULT_BANNER_TEXT_OUTLINE = 0
DEFAULT_BANNER_FULL_WIDTH = True
DEFAULT_BANNER_BOX_MARGIN_X = 0

# Legacy char-ratio fit defaults (fallback ONLY when the real font can't be
# measured — e.g. Pillow or the font file is missing in a bare unit env).
DEFAULT_BANNER_FIT_MAX_FS = 110
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
BANNER_BOX_STYLE_NAME = "BannerBox"

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


def _banner_style_field(cfg: Any, name: str, default: Any) -> Any:
    return _style_field(getattr(cfg, "banner_style", None), name, default)


def _banner_style_lines(cfg: Any) -> list[str]:
    r"""Two styles for the two-line boxed banner: the white BannerBox rectangle
    and the purple Banner text.

    The box fill reuses ``banner_style.back`` (white); the text colour reuses
    ``banner_style.primary`` (purple). The box is a filled ``\p`` drawing
    (BorderStyle=1, no outline) anchored top-left (Alignment 7); the text is
    BorderStyle=1 with a configurable (default 0) outline, centred (Alignment 5).
    """
    fontname = _banner_style_field(cfg, "fontname", _BANNER_STYLE_DEFAULTS["fontname"])
    box_fill = _banner_style_field(cfg, "back", _BANNER_STYLE_DEFAULTS["back"])
    text_fill = _banner_style_field(cfg, "primary", _BANNER_STYLE_DEFAULTS["primary"])
    bold = int(bool(_banner_style_field(cfg, "bold", _BANNER_STYLE_DEFAULTS["bold"])))
    text_outline = int(_cfg(cfg, "banner_text_outline", DEFAULT_BANNER_TEXT_OUTLINE))
    outline_col = _banner_style_field(cfg, "outline_color", "&H00FFFFFF")
    fs = int(_banner_style_field(cfg, "fontsize", _BANNER_STYLE_DEFAULTS["fontsize"]))
    box = (
        f"Style: {BANNER_BOX_STYLE_NAME},{fontname},{fs},{box_fill},&H000000FF,"
        f"{box_fill},&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
    )
    text = (
        f"Style: {BANNER_STYLE_NAME},{fontname},{fs},{text_fill},&H000000FF,"
        f"{outline_col},&H00000000,{bold},0,0,0,100,100,0,0,1,{text_outline},0,5,0,0,0,1"
    )
    return [box, text]


def _dialogue(
    start: float, end: float, style: str, x: int, y: int, text: str, extra_tags: str = ""
) -> str:
    return (
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,"
        f"{{\\pos({x},{y}){extra_tags}}}{_ass_escape(text)}"
    )


# ───── Banner text-fit: MEASURE the real font, don't guess ───────────

import functools


@functools.lru_cache(maxsize=16)
def _resolve_font_file(fontname: str, bold: bool) -> Optional[str]:
    """fc-match the banner font name to the actual TTF libass will render.

    Measuring the SAME file freetype/libass uses is what makes the fit exact
    (e.g. "Arial" → LiberationSans-Bold.ttf on Linux). Returns None if fc-match
    or the file is unavailable, so callers fall back to a char-ratio estimate.
    """
    query = f"{fontname}:bold" if bold else fontname
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", query], capture_output=True, text=True
        )
        p = out.stdout.strip()
        return p if p and Path(p).exists() else None
    except Exception:
        return None


def _banner_font_file(cfg: Any) -> Optional[str]:
    fontname = _banner_style_field(cfg, "fontname", _BANNER_STYLE_DEFAULTS["fontname"])
    bold = bool(_banner_style_field(cfg, "bold", _BANNER_STYLE_DEFAULTS["bold"]))
    return _resolve_font_file(str(fontname), bold)


def _ink_bbox(text: str, font_file: str, fs: int) -> tuple[int, int, int, int]:
    """Freetype ink bounding box (x0,y0,x1,y1) for ``text`` at size ``fs``."""
    from PIL import ImageFont

    return ImageFont.truetype(font_file, fs).getbbox(text)


def _font_ascent_descent(font_file: str, fs: int) -> tuple[int, int]:
    from PIL import ImageFont

    return ImageFont.truetype(font_file, fs).getmetrics()


def _ref_text_width(text: str, font_file: Optional[str], ref_fs: int) -> float:
    """Text advance width at the reference size — measured if possible, else estimated."""
    if font_file is not None:
        x0, _, x1, _ = _ink_bbox(text, font_file, ref_fs)
        return float(x1 - x0)
    ratio = DEFAULT_BANNER_FIT_CHAR_RATIO
    return max(1.0, len(text) * ratio * ref_fs)


def _banner_box_dims(cfg: Any) -> tuple[int, int]:
    """The FIXED banner box (width × height) in canvas px: full-width band."""
    canvas_w = int(_cfg(cfg, "canvas_w", DEFAULT_CANVAS_W))
    margin_x = int(_cfg(cfg, "banner_box_margin_x", DEFAULT_BANNER_BOX_MARGIN_X))
    box_w = canvas_w - 2 * margin_x
    box_h = int(_cfg(cfg, "banner_box_h", DEFAULT_BANNER_BOX_H))
    return box_w, box_h


def _wrap_into(words: list[str], n: int, font_file: Optional[str], ref_fs: int) -> list[str]:
    """Partition ``words`` into exactly ``n`` contiguous lines minimising the widest.

    Brute force over break positions — hooks are ≤ ~8 words so this is trivial.
    """
    from itertools import combinations

    if n <= 1 or len(words) <= 1:
        return [" ".join(words)]
    if n >= len(words):
        return list(words)  # one word per line; can't make more
    best: Optional[tuple[float, list[str]]] = None
    for breaks in combinations(range(1, len(words)), n - 1):
        cuts = [0, *breaks, len(words)]
        lines = [" ".join(words[cuts[i]:cuts[i + 1]]) for i in range(n)]
        widest = max(_ref_text_width(ln, font_file, ref_fs) for ln in lines)
        if best is None or widest < best[0]:
            best = (widest, lines)
    return best[1] if best else [" ".join(words)]


def _line_metrics_ref(cfg: Any, font_file: Optional[str], ref_fs: int) -> tuple[float, float]:
    """(single-line ink cap height, line advance) at the reference size."""
    spacing = float(_cfg(cfg, "banner_line_spacing", DEFAULT_BANNER_LINE_SPACING))
    if font_file is not None:
        asc, desc = _font_ascent_descent(font_file, ref_fs)
        # cap ink height of an all-caps sample (fill the box with ink, not leading)
        x0, y0, x1, y1 = _ink_bbox("ABCDEFGHIJKMNPQRSTUVWXYZ", font_file, ref_fs)
        cap_h = y1 - y0
        line_box = asc + desc
    else:
        cap_h = ref_fs * 0.72
        line_box = ref_fs * 1.16
    return cap_h, line_box * spacing


def banner_layout(hook: str, cfg: Any = None, font_file: Optional[str] = None) -> tuple[list[str], int]:
    """Choose (lines, font size) that fills the FIXED banner box as large as possible.

    For each line count 1..``banner_max_lines`` the largest font that fits BOTH the
    box width (widest line) and the box height (n-line ink block) is computed; the
    line count giving the biggest font wins. Maximising the font maximises fill.
    """
    ref_fs = int(_cfg(cfg, "banner_font_ref_fs", DEFAULT_BANNER_REF_FS))
    max_fs = int(_cfg(cfg, "banner_max_fs", DEFAULT_BANNER_MAX_FS))
    max_lines = int(_cfg(cfg, "banner_max_lines", DEFAULT_BANNER_MAX_LINES))
    pad_x = int(_cfg(cfg, "banner_pad_x", DEFAULT_BANNER_PAD_X))
    pad_y = int(_cfg(cfg, "banner_pad_y", DEFAULT_BANNER_PAD_Y))

    box_w, box_h = _banner_box_dims(cfg)
    avail_w = max(1, box_w - 2 * pad_x)
    avail_h = max(1, box_h - 2 * pad_y)
    cap_ref, line_adv_ref = _line_metrics_ref(cfg, font_file, ref_fs)

    words = hook.split()
    best: Optional[tuple[float, list[str]]] = None
    for n in range(1, min(max_lines, max(1, len(words))) + 1):
        lines = _wrap_into(words, n, font_file, ref_fs)
        widest = max(_ref_text_width(ln, font_file, ref_fs) for ln in lines)
        fs_w = ref_fs * avail_w / widest
        block_ref = (n - 1) * line_adv_ref + cap_ref
        fs_h = ref_fs * avail_h / block_ref
        fs = min(fs_w, fs_h)
        if best is None or fs > best[0]:
            best = (fs, lines)
    fs_val = max(8, min(max_fs, int(best[0]))) if best else 8
    return (best[1] if best else [hook]), fs_val


def balanced_wrap(hook: str, cfg: Any = None, font_file: Optional[str] = None) -> list[str]:
    """The line breakdown the fixed-box fill chooses for ``hook``."""
    return banner_layout(hook, cfg, font_file)[0]


def compute_banner_fontsize(hook_text: str, cfg: Any = None) -> int:
    """Font size the fixed-box fill chooses for ``hook_text`` (measured, not guessed)."""
    return banner_layout(hook_text, cfg, _banner_font_file(cfg))[1]


def _caption_events(words: list[tuple[float, float, str]], cfg: Any) -> list[str]:
    x = _center_x(cfg)
    y = int(_cfg(cfg, "caption_safe_y", DEFAULT_CAPTION_SAFE_Y))
    upper = bool(_cfg(cfg, "caption_uppercase", True))
    events: list[str] = []
    for st, en, txt in group_captions(words, cfg):
        events.append(_dialogue(st, en, CAPTION_STYLE_NAME, x, y, txt.upper() if upper else txt))
    return events


def _banner_geometry(
    lines: list[str], fs: int, cfg: Any, font_file: Optional[str], cx: int, cy: int,
) -> dict[str, int]:
    """The FIXED box rectangle (bw,bh,bx0,by0) and the text y that centres the
    ink block on ``cy``. The box does not depend on the text — only its size and
    position; the text is fit to fill it (``banner_layout``)."""
    n = len(lines)
    spacing = float(_cfg(cfg, "banner_line_spacing", DEFAULT_BANNER_LINE_SPACING))
    box_w, box_h = _banner_box_dims(cfg)
    bx0, by0 = cx - box_w // 2, cy - box_h // 2

    if font_file is not None:
        boxes = [_ink_bbox(ln, font_file, fs) for ln in lines]
        y0 = min(b[1] for b in boxes)
        ink_h_one = max(b[3] - b[1] for b in boxes)
        asc, desc = _font_ascent_descent(font_file, fs)
    else:
        ink_h_one = int(fs * 0.72)
        asc, desc = int(fs * 0.90), int(fs * 0.25)
        y0 = asc - ink_h_one
    line_adv = int((asc + desc) * spacing)
    block_h = (n - 1) * line_adv + ink_h_one
    total_line_box = (n - 1) * line_adv + (asc + desc)
    ink_center_from_block_top = y0 + block_h / 2
    ty = cy + int(total_line_box / 2 - ink_center_from_block_top)
    return {"bw": box_w, "bh": box_h, "bx0": bx0, "by0": by0, "ty": ty}


def _banner_events(hook: str, dur: float, cfg: Any) -> list[str]:
    """A white fixed-size box + the purple hook fit to fill it, ink centred on the divider."""
    cx = _center_x(cfg)
    cy = int(_cfg(cfg, "divider_y", DEFAULT_DIVIDER_Y))
    upper = bool(_cfg(cfg, "banner_uppercase", True))
    text = hook.upper() if upper else hook

    font_file = _banner_font_file(cfg)
    lines, fs = banner_layout(text, cfg, font_file)
    g = _banner_geometry(lines, fs, cfg, font_file, cx, cy)
    body = "\\N".join(_ass_escape(ln) for ln in lines)
    box_ev = (
        f"Dialogue: 0,{_ass_time(0.0)},{_ass_time(dur)},{BANNER_BOX_STYLE_NAME},,0,0,0,,"
        f"{{\\pos({g['bx0']},{g['by0']})\\p1}}m 0 0 l {g['bw']} 0 "
        f"{g['bw']} {g['bh']} 0 {g['bh']}{{\\p0}}"
    )
    text_ev = (
        f"Dialogue: 1,{_ass_time(0.0)},{_ass_time(dur)},{BANNER_STYLE_NAME},,0,0,0,,"
        f"{{\\pos({cx},{g['ty']})\\fs{fs}}}{body}"
    )
    return [box_ev, text_ev]


def _assemble(cfg: Any, styles: list[str], events: list[str]) -> str:
    lines = _script_info(cfg)
    lines += ["[V4+ Styles]", _STYLE_FORMAT, *styles, ""]
    lines += ["[Events]", _EVENTS_FORMAT, *events]
    return "\n".join(lines) + "\n"


def build_caption_ass(words: list[tuple[float, float, str]], cfg: Any = None) -> str:
    """B3 — grouped caption ASS at the safe zone. Standalone (Cap style only)."""
    return _assemble(cfg, [_caption_style_line(cfg)], _caption_events(words, cfg))


def build_banner_ass(hook: str, dur: float, cfg: Any = None) -> str:
    """B4 — full-duration two-line boxed banner ASS at the divider."""
    return _assemble(cfg, _banner_style_lines(cfg), _banner_events(hook, dur, cfg))


def build_finish_ass(
    words: list[tuple[float, float, str]], hook: str, dur: float, cfg: Any = None
) -> str:
    """Combined ASS burned by ``finish_reel`` (B9): banner + grouped captions."""
    styles = [_caption_style_line(cfg), *_banner_style_lines(cfg)]
    events = [*_banner_events(hook, dur, cfg), *_caption_events(words, cfg)]
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
    "balanced_wrap",
    "banner_layout",
    "group_captions",
    "build_caption_ass",
    "build_banner_ass",
    "build_finish_ass",
    "write_ass",
    "CAPTION_STYLE_NAME",
    "BANNER_STYLE_NAME",
    "BANNER_BOX_STYLE_NAME",
]
