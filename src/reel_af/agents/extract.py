"""Article URL → Essence (one harness call: fetch + distill)."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from readability import Document

from reel_af.models import Essence

# Hard caps so a hostile URL can't blow up the pipeline.
FETCH_TIMEOUT_S = 30.0
MAX_BODY_CHARS = 50_000
PROMPT_BODY_CHARS = 14_000
USER_AGENT = "reel-af/0.2 (+https://github.com/Agent-Field/agentfield)"

# --- YouTube intake ----------------------------------------------------------
# A YouTube watch page has no readable article body, so readability yields
# nothing. Instead, when the URL is a YouTube link, pull the caption transcript
# and treat it as the body. Optional per-clip scoping travels in the query:
#   ?t=<sec>         start of the moment
#   &reel_end=<sec>  end of the moment
# so one video can seed N reels, one per moment, by varying the range.
_YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be")
_YT_TRANSCRIPT_LANGS = ("en", "en-US", "en-GB")


_SYSTEM = """You are reading an article and extracting ITS ESSENCE for a short-form vertical video reel. ONE shot. The hook is the single most surprising or counter-intuitive thing in the piece — NOT the article's overall topic, NOT a tidy summary. The buzz-worthy claim that makes a thumb stop scrolling.

Rules:
  - core_claim: the ONE most surprising/counter-intuitive sentence the author would recognize. ≤25 words. This is the hook's raw material.
  - mechanism: 1-2 sentences explaining WHY the claim is true / HOW it works. The payoff to the hook.
  - evidence: 1-3 concrete grounding items — numbers, named entities, specific examples — verbatim or near-verbatim from the article. NOT paraphrases. NOT your own analysis.
  - content_mode: "scientific" ONLY if the source is a research paper / preprint / technical write-up with method + result + baseline shape (a Medium post explaining a paper still counts). Otherwise "general".
  - domain: one word for the subject area (e.g. "technology", "biology", "finance", "philosophy", "health", "design").

Stay faithful. Don't invent examples. Don't soften surprising claims. Pick the one thing in this article that, stated as a thumbnail, would make a stranger tap."""


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
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS]
    return title, text


def _essence_user_prompt(*, final_url: str, title: str, body: str) -> str:
    return (
        f"SOURCE\n"
        f"  url   : {final_url}\n"
        f"  title : {title or '(no title)'}\n\n"
        f"FULL BODY (cleaned, truncated to fit context):\n{body[:PROMPT_BODY_CHARS]}"
    )


def _fit_text_body(cleaned: str) -> str:
    if len(cleaned) <= PROMPT_BODY_CHARS:
        return cleaned
    return cleaned[:PROMPT_BODY_CHARS]


def _prepare_text_body(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("essence_from_text: text is empty or whitespace-only")
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
    if len(body) > MAX_BODY_CHARS:
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
    title: str = "(provided text)",
) -> Essence:
    """Build an Essence from provided text without fetching a URL."""
    body = _prepare_text_body(text)
    user = _essence_user_prompt(
        final_url="(none - provided text)",
        title=title,
        body=body,
    )
    return await app.ai(system=_SYSTEM, user=user, schema=Essence)
