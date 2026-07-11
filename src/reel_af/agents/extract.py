"""Article URL → Essence (one harness call: fetch + distill)."""

from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from readability import Document

from reel_af.models import Essence

_CONFIG_PATH = Path(__file__).parent / "config" / "extract.json"


@lru_cache(maxsize=1)
def _extract_config() -> dict[str, Any]:
    return json.loads(_CONFIG_PATH.read_text())


_EXTRACT_CFG = _extract_config()

# Hard caps so a hostile URL can't blow up the pipeline.
FETCH_TIMEOUT_S = float(_EXTRACT_CFG["fetch_timeout_s"])
MAX_BODY_CHARS = int(_EXTRACT_CFG["max_body_chars"])
PROMPT_BODY_CHARS = int(_EXTRACT_CFG["prompt_body_chars"])
USER_AGENT = str(_EXTRACT_CFG["user_agent"])

# --- YouTube intake ----------------------------------------------------------
# A YouTube watch page has no readable article body, so readability yields
# nothing. Instead, when the URL is a YouTube link, pull the caption transcript
# and treat it as the body. Optional per-clip scoping travels in the query:
#   ?t=<sec>         start of the moment
#   &reel_end=<sec>  end of the moment
# so one video can seed N reels, one per moment, by varying the range.
_YT_HOSTS = tuple(_EXTRACT_CFG["youtube_hosts"])
_YT_TRANSCRIPT_LANGS = tuple(_EXTRACT_CFG["youtube_transcript_langs"])
_SYSTEM = str(_EXTRACT_CFG["essence_system_prompt"])
_ESSENCE_USER_TEMPLATE = str(_EXTRACT_CFG["essence_user_template"])
_EMPTY_TEXT_ERROR = str(_EXTRACT_CFG["empty_text_error"])
_NO_TITLE_LABEL = str(_EXTRACT_CFG["no_title_label"])
_PROVIDED_TEXT_TITLE = str(_EXTRACT_CFG["provided_text_title"])
_PROVIDED_TEXT_URL = str(_EXTRACT_CFG["provided_text_url"])


async def _fetch(url: str) -> tuple[str, str]:
    """Fetch URL, return (raw_html, final_url)."""
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
            resp.raise_for_status()
            text = await resp.text(errors="replace")
            return text, str(resp.url)


def _clean(html: str) -> tuple[str, str]:
    """Run readability for clean title + body. Pure CPU."""
    doc = Document(html)
    title = (doc.short_title() or doc.title() or "").strip()
    content_html = doc.summary(html_partial=True)
    text = re.sub(r"<[^>]+>", " ", content_html)
    text = re.sub(r"\s+", " ", text).strip()
    text_chars = len(text)
    if text_chars > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS]
    return title, text


def _essence_user_prompt(*, final_url: str, title: str, body: str) -> str:
    title_for_prompt = title or _NO_TITLE_LABEL
    body_for_prompt = body[:PROMPT_BODY_CHARS]
    return _ESSENCE_USER_TEMPLATE.format(
        final_url=final_url,
        title=title_for_prompt,
        body=body_for_prompt,
    )


def _fit_text_body(cleaned: str) -> str:
    cleaned_chars = len(cleaned)
    if cleaned_chars <= PROMPT_BODY_CHARS:
        return cleaned
    return cleaned[:PROMPT_BODY_CHARS]


def _prepare_text_body(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError(_EMPTY_TEXT_ERROR)
    return _fit_text_body(cleaned)


def _youtube_ref(url: str) -> tuple[str, float | None, float | None] | None:
    """Return (video_id, t_start, t_end) if `url` is a YouTube link, else None.
    t_start/t_end come from `?t=` and `?reel_end=` (seconds) when present."""
    try:
        u = urlparse(url)
    except (ValueError, TypeError):
        return None
    host = (u.hostname or "").lower()
    if host not in _YT_HOSTS:
        return None
    q = parse_qs(u.query)
    if host in ("youtu.be", "www.youtu.be"):
        vid = u.path.lstrip("/").split("/")[0]
    elif u.path.startswith(("/shorts/", "/embed/")):
        parts = u.path.split("/")
        vid = parts[2] if len(parts) > 2 else ""
    else:
        vid = (q.get("v") or [""])[0]
    if not vid:
        return None

    def _sec(name: str) -> float | None:
        raw = (q.get(name) or [None])[0]
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    return vid, _sec("t"), _sec("reel_end")


def _youtube_segments(video_id: str) -> list[dict]:
    """Caption segments as [{text,start,duration}], across library versions."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "YouTube intake needs the `youtube-transcript-api` package"
        ) from exc
    langs = list(_YT_TRANSCRIPT_LANGS)
    if hasattr(YouTubeTranscriptApi, "get_transcript"):  # 0.6.x classmethod API
        raw = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
        return [{"text": r["text"], "start": r["start"], "duration": r["duration"]} for r in raw]
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=langs)  # 1.x instance API
    return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]


def _youtube_body(video_id: str, t_start: float | None, t_end: float | None) -> tuple[str, str]:
    """(title, body) for a YouTube video, optionally scoped to [t_start, t_end]."""
    segs = _youtube_segments(video_id)
    if t_start is not None or t_end is not None:
        lo = t_start if t_start is not None else float("-inf")
        hi = t_end if t_end is not None else float("inf")
        segs = [s for s in segs if lo <= s["start"] <= hi]
    body = re.sub(r"\s+", " ", " ".join(s["text"] for s in segs)).strip()
    body_chars = len(body)
    if body_chars > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
    end_label = int(t_end) if t_end is not None else "end"
    scope = f" [{int(t_start or 0)}s-{end_label}]" if (t_start is not None or t_end is not None) else ""
    return f"YouTube {video_id}{scope}", body


async def extract_essence(app: Any, url: str) -> Essence:
    """Single harness: fetch the source (article URL or YouTube video) and
    extract the most surprising claim + mechanism + evidence + content_mode +
    domain. A YouTube link is read from its caption transcript, optionally
    scoped to a moment via `?t=` / `?reel_end=`; anything else goes through
    readability as before."""
    loop = asyncio.get_event_loop()
    yt = _youtube_ref(url)
    if yt is not None:
        vid, t_start, t_end = yt
        title, body = await loop.run_in_executor(None, _youtube_body, vid, t_start, t_end)
        final_url = url
    else:
        html, final_url = await _fetch(url)
        title, body = await loop.run_in_executor(None, _clean, html)

    if not body:
        raise RuntimeError(f"extract_essence: could not extract readable text from {url}")

    user = _essence_user_prompt(final_url=final_url, title=title, body=body)

    return await app.ai(system=_SYSTEM, user=user, schema=Essence)


async def essence_from_text(
    app: Any,
    text: str | None,
    *,
    title: str = _PROVIDED_TEXT_TITLE,
) -> Essence:
    """Build an Essence from provided text without fetching a URL."""
    body = _prepare_text_body(text)
    user = _essence_user_prompt(
        final_url=_PROVIDED_TEXT_URL,
        title=title,
        body=body,
    )
    return await app.ai(system=_SYSTEM, user=user, schema=Essence)
