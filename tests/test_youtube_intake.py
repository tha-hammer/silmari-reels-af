"""YouTube intake adapter — offline unit tests.

These cover the two pure pieces the adapter adds to the article path:
URL recognition (+ optional per-clip time range) and transcript-body assembly
(with time scoping). Neither touches the network or a paid API; the caption
fetch is monkeypatched.
"""

from __future__ import annotations

import reel_af.agents.extract as extract
from reel_af.agents.extract import _youtube_body, _youtube_ref

# ───── URL recognition ───────────────────────────────────────────────

def test_watch_url_is_recognized():
    assert _youtube_ref("https://www.youtube.com/watch?v=abc123XYZ_-") == ("abc123XYZ_-", None, None)


def test_short_url_is_recognized():
    assert _youtube_ref("https://youtu.be/abc123XYZ_-") == ("abc123XYZ_-", None, None)


def test_shorts_and_embed_urls():
    assert _youtube_ref("https://youtube.com/shorts/vidID00001")[0] == "vidID00001"
    assert _youtube_ref("https://www.youtube.com/embed/vidID00001")[0] == "vidID00001"


def test_time_range_is_parsed_from_query():
    ref = _youtube_ref("https://www.youtube.com/watch?v=vid&t=90&reel_end=115")
    assert ref == ("vid", 90.0, 115.0)


def test_non_youtube_url_returns_none():
    assert _youtube_ref("https://maceojourdan.com/confidently-stupid.html") is None
    assert _youtube_ref("not a url at all") is None


def test_malformed_time_is_ignored_not_fatal():
    assert _youtube_ref("https://youtu.be/vid?t=oops") == ("vid", None, None)


# ───── transcript-body assembly + scoping ────────────────────────────

_SEGMENTS = [
    {"text": "intro words", "start": 0.0, "duration": 5.0},
    {"text": "the hook lands here", "start": 92.0, "duration": 4.0},
    {"text": "and the payoff", "start": 100.0, "duration": 4.0},
    {"text": "outro filler", "start": 200.0, "duration": 4.0},
]


def test_full_body_joins_all_segments(monkeypatch):
    monkeypatch.setattr(extract, "_youtube_segments", lambda _vid: _SEGMENTS)
    title, body = _youtube_body("vid", None, None)
    assert title == "YouTube vid"
    assert body == "intro words the hook lands here and the payoff outro filler"


def test_time_scoped_body_keeps_only_the_window(monkeypatch):
    monkeypatch.setattr(extract, "_youtube_segments", lambda _vid: _SEGMENTS)
    title, body = _youtube_body("vid", 90.0, 115.0)
    assert title == "YouTube vid [90s-115]"
    assert body == "the hook lands here and the payoff"


def test_open_ended_window_runs_to_the_end(monkeypatch):
    monkeypatch.setattr(extract, "_youtube_segments", lambda _vid: _SEGMENTS)
    title, body = _youtube_body("vid", 100.0, None)
    assert title == "YouTube vid [100s-end]"
    assert body == "and the payoff outro filler"
