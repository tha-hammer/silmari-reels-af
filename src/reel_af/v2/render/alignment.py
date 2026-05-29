"""Real forced alignment for v2 TTS — replaces Path A's length-proportional
guess (see v2/render/tts.py) with timestamps recovered from the actual
synthesized audio.

Path A distributed total duration across words by `len(word)` weight, which
ignores the 6-8 seconds of silent pauses Gemini Flash TTS inserts between
clauses. Result: subtitle cards drift up to ~1 second ahead of speech by the
back half of a 36-second reel. This module transcribes the TTS WAV with
whisper-cli (tiny.en), gets per-token millisecond timestamps, and maps each
Whisper token span back onto our ORIGINAL written tokens (so "43%" stays
"43%" even though Whisper writes it as " 43" + "%"). Falls back to a
silence-aware ffmpeg approximation when whisper-cli is missing.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import string
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from reel_af.v2.models import WordTiming
from reel_af.v2.render.tts import _strip_tts_tags

__all__ = ["align_audio"]


# ───── Constants ────────────────────────────────────────────────────


_WHISPER_BIN = "whisper-cli"
_WHISPER_MODEL = Path.home() / ".cache" / "whisper-cpp" / "ggml-tiny.en.bin"

# Fuzzy-match threshold for mapping a span of Whisper tokens onto one of our
# original tokens. 0.7 tolerates Whisper splits like "AIME" → "A"+"IM"+"E"
# and homophone collapses like "Codeforces" → "code"+"forces" while still
# rejecting genuinely unrelated tokens.
_FUZZY_THRESHOLD = 0.7

# How many forward Whisper tokens to greedily try to absorb into one
# original-token span before giving up. Caps the inner loop in `_align`.
_MAX_SPAN = 6

_PUNCT_TBL = str.maketrans("", "", string.punctuation + "—–…“”‘’")


# ───── Public entrypoint ───────────────────────────────────────────


async def align_audio(
    audio_path: Path,
    text: str,
) -> list[WordTiming]:
    """Forced-align `text` against `audio_path`.

    Returns one WordTiming per original token (whitespace-split, TTS tags
    stripped). Timestamps come from whisper-cli per-token output; the
    mapping back to our tokens uses a greedy fuzzy two-pointer walk so the
    returned `word` is always our original text (e.g. "43%", "Codeforces")
    not Whisper's rewrite (" 43"+"%", " code"+" forces").

    Falls back to silence-aware ffmpeg approximation if whisper-cli or its
    model isn't available, or if the subprocess fails.
    """
    cleaned = _strip_tts_tags(text or "")
    if not cleaned.strip():
        return []

    words = cleaned.split()
    # Filter punctuation-only tokens defensively (rare; e.g. lone "—").
    words = [w for w in words if _normalize(w)]
    if not words:
        return []

    total_dur = _probe_duration(audio_path)

    whisper_available = (
        shutil.which(_WHISPER_BIN) is not None and _WHISPER_MODEL.exists()
    )
    if whisper_available:
        try:
            wh_tokens = await _run_whisper(audio_path)
            if wh_tokens:
                return _align(words, wh_tokens, total_dur)
        except Exception:
            # Fall through to silence-aware fallback.
            pass

    return await _align_silence_aware(audio_path, words, total_dur)


# ───── Whisper subprocess ──────────────────────────────────────────


async def _run_whisper(audio_path: Path) -> list[tuple[str, float, float]]:
    """Invoke whisper-cli with `-ml 1` to get per-token timings.

    Returns list of (text, start_s, end_s) tuples. `text` keeps Whisper's
    own casing/punctuation; the alignment step normalizes for matching.
    """
    with tempfile.TemporaryDirectory(prefix="reelaf_whisper_") as tmpdir:
        out_prefix = Path(tmpdir) / "out"
        cmd = [
            _WHISPER_BIN,
            "-m", str(_WHISPER_MODEL),
            "-f", str(audio_path),
            "-oj",
            "-ml", "1",
            "-of", str(out_prefix),
            "-np",
            "-nt",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"whisper-cli failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')[:500]}"
            )

        json_path = out_prefix.with_suffix(".json")
        if not json_path.exists():
            # Some whisper-cli builds append .json directly to the prefix
            # without dropping the extension. Handle both.
            alt = Path(str(out_prefix) + ".json")
            if alt.exists():
                json_path = alt
            else:
                raise RuntimeError("whisper-cli produced no JSON output")

        with json_path.open() as f:
            data = json.load(f)

    tokens: list[tuple[str, float, float]] = []
    for entry in data.get("transcription", []):
        raw = entry.get("text", "")
        if not raw:
            continue
        # Strip leading space (artifact of whisper's BPE detokenization)
        # but preserve internal characters incl. punctuation/digits.
        txt = raw.lstrip()
        offsets = entry.get("offsets") or {}
        start_ms = offsets.get("from")
        end_ms = offsets.get("to")
        if start_ms is None or end_ms is None:
            continue
        tokens.append((txt, start_ms / 1000.0, end_ms / 1000.0))
    return tokens


# ───── Alignment ───────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Lowercase + strip all punctuation. Used for fuzzy comparison only;
    the returned WordTiming.word always holds the ORIGINAL token."""
    return s.translate(_PUNCT_TBL).lower().strip()


def _score(a: str, b: str) -> float:
    """Fuzzy match score between two normalized strings.

    Accepts substring containment as a strong signal — Whisper often
    encodes "43%" as " 43" + "%" so the concatenation contains "43"
    perfectly even though length differs.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return max(0.85, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def _align(
    words: list[str],
    wh_tokens: list[tuple[str, float, float]],
    total_dur: float,
) -> list[WordTiming]:
    """Greedy two-pointer walk: for each original word, absorb 1..N forward
    Whisper tokens until the joined normalized text fuzzy-matches the
    normalized original. On failure, mark None and interpolate at the end.
    """
    spans: list[tuple[float, float] | None] = []
    j = 0  # cursor into wh_tokens
    n_wh = len(wh_tokens)

    for w in words:
        target = _normalize(w)
        if not target or j >= n_wh:
            spans.append(None)
            continue

        best_span: tuple[float, float] | None = None
        best_score = 0.0
        best_k = 0  # how many wh tokens were consumed

        # Try absorbing 1..MAX_SPAN tokens starting at j.
        max_k = min(_MAX_SPAN, n_wh - j)
        joined = ""
        for k in range(1, max_k + 1):
            joined += _normalize(wh_tokens[j + k - 1][0])
            score = _score(target, joined)
            # Prefer the smallest k that crosses the threshold — avoids
            # eating tokens that belong to the NEXT word.
            if score >= _FUZZY_THRESHOLD and score > best_score:
                best_score = score
                best_k = k
                best_span = (wh_tokens[j][1], wh_tokens[j + k - 1][2])
                # Strong match — stop expanding to leave tokens for the
                # next original word.
                if score >= 0.95:
                    break

        if best_span is not None:
            spans.append(best_span)
            j += best_k
        else:
            # No match within the window. Skip ONE whisper token (likely
            # a hallucinated artifact or punctuation) and try again later
            # via interpolation.
            spans.append(None)
            j += 1 if j < n_wh else 0

    return _fill_gaps(words, spans, total_dur)


def _fill_gaps(
    words: list[str],
    spans: list[tuple[float, float] | None],
    total_dur: float,
) -> list[WordTiming]:
    """Interpolate timings for any word that didn't get a Whisper match.

    Clamp endpoints into [0, total_dur] and force monotonic non-decreasing
    order so downstream subtitle/card packers don't crash on negative dur.
    """
    timings: list[WordTiming] = []
    n = len(words)
    if n == 0:
        return timings

    # First pass: build a working copy with clamped values.
    working: list[tuple[float, float] | None] = []
    for span in spans:
        if span is None:
            working.append(None)
            continue
        s = max(0.0, min(span[0], total_dur))
        e = max(s, min(span[1], total_dur))
        working.append((s, e))

    # Pre-compute index of previous and next anchor for each gap.
    for i in range(n):
        if working[i] is not None:
            continue
        prev_end = 0.0
        next_start = total_dur
        for back in range(i - 1, -1, -1):
            if working[back] is not None:
                prev_end = working[back][1]
                break
        for fwd in range(i + 1, n):
            if working[fwd] is not None:
                next_start = working[fwd][0]
                break
        # Count consecutive missing tokens in this run and slice the gap
        # proportionally by word length so a run of 3 misses doesn't all
        # collapse onto one frame.
        run_start = i
        run_end = i
        while run_end + 1 < n and working[run_end + 1] is None:
            run_end += 1
        gap_dur = max(0.0, next_start - prev_end)
        weights = [max(len(words[k]), 1) for k in range(run_start, run_end + 1)]
        total_w = sum(weights)
        cursor = prev_end
        for k, w in zip(range(run_start, run_end + 1), weights):
            slice_dur = gap_dur * w / total_w if total_w else 0.0
            working[k] = (cursor, cursor + slice_dur)
            cursor += slice_dur
        # Skip ahead past this run.

    # Second pass: enforce monotonic non-decreasing start/end.
    last_end = 0.0
    for w, span in zip(words, working):
        assert span is not None  # _fill_gaps invariant
        s, e = span
        s = max(s, last_end)
        e = max(e, s)
        e = min(e, total_dur)
        s = min(s, e)
        timings.append(WordTiming(word=w, start_s=s, end_s=e))
        last_end = e

    return timings


# ───── ffprobe duration helper ─────────────────────────────────────


def _probe_duration(audio_path: Path) -> float:
    """Return audio duration in seconds. ffprobe is already a hard dep of
    the v2 render stack."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ───── Silence-aware fallback ──────────────────────────────────────


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


async def _align_silence_aware(
    audio_path: Path,
    words: list[str],
    total_dur: float,
) -> list[WordTiming]:
    """Fallback aligner that uses ffmpeg silencedetect to find speech
    regions and tiles words across them proportional to word length.

    Strictly better than Path A's "ignore silences" approximation: it gives
    nothing to the long pauses TTS inserts between clauses, so the
    subtitles stop drifting through dead air. Still far from whisper.
    """
    silences = await _detect_silences(audio_path)
    speech_regions = _invert_silences(silences, total_dur)
    if not speech_regions:
        speech_regions = [(0.0, total_dur)]

    total_speech = sum(e - s for s, e in speech_regions)
    if total_speech <= 0:
        speech_regions = [(0.0, total_dur)]
        total_speech = total_dur

    # Allocate words across regions proportional to region length so each
    # speech burst gets a share of words consistent with its airtime.
    weights = [max(len(w), 1) for w in words]
    total_w = sum(weights)

    # Greedy assignment: walk through words, fill the current region until
    # its share is exhausted, then move on.
    region_word_caps: list[int] = []
    cumulative = 0
    for idx, (rs, re_) in enumerate(speech_regions):
        share = (re_ - rs) / total_speech
        # All remaining words to the last region to avoid rounding loss.
        if idx == len(speech_regions) - 1:
            cap = len(words) - cumulative
        else:
            cap = max(1, round(len(words) * share))
            cap = min(cap, len(words) - cumulative - (len(speech_regions) - idx - 1))
        region_word_caps.append(cap)
        cumulative += cap

    timings: list[WordTiming] = []
    wi = 0
    for (rs, re_), cap in zip(speech_regions, region_word_caps):
        if cap <= 0:
            continue
        region_words = words[wi : wi + cap]
        region_weights = weights[wi : wi + cap]
        region_total_w = sum(region_weights) or 1
        region_dur = re_ - rs
        cursor = rs
        for k, (w, wt) in enumerate(zip(region_words, region_weights)):
            dur = region_dur * wt / region_total_w
            end_s = re_ if k == len(region_words) - 1 else cursor + dur
            timings.append(WordTiming(word=w, start_s=cursor, end_s=end_s))
            cursor = end_s
        wi += cap

    # Any leftovers (shouldn't happen, but defensive) get pinned to the end.
    while wi < len(words):
        timings.append(WordTiming(word=words[wi], start_s=total_dur, end_s=total_dur))
        wi += 1

    # Ignore total_w (used only as a sanity check that words are non-empty).
    _ = total_w
    return timings


async def _detect_silences(audio_path: Path) -> list[tuple[float, float]]:
    """Return list of (start_s, end_s) for detected silent regions."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(audio_path),
        "-af", "silencedetect=n=-30dB:d=0.3",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    text = stderr.decode(errors="replace")

    starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(text)]
    ends = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(text)]
    # Pair them; ffmpeg always emits start before end but the last region
    # may be missing an end if audio ends in silence.
    pairs: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        if end is None:
            pairs.append((start, start))  # treat as instantaneous
        else:
            pairs.append((start, end))
    return pairs


def _invert_silences(
    silences: list[tuple[float, float]],
    total_dur: float,
) -> list[tuple[float, float]]:
    """Convert silent intervals into speech intervals over [0, total_dur]."""
    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < total_dur:
        speech.append((cursor, total_dur))
    # Filter out zero-length intervals.
    return [(s, e) for s, e in speech if e > s]
