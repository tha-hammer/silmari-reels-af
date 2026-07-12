"""B5: the production consumer driver / cursor-read lifecycle.

Without a production caller the consumer never runs (`default_deps()` builds request-time
adapters only). The driver is the workflow-closure boundary: it reads from the durable
cursor, processes, sleeps, repeats — with graceful start/stop and bounded retry/backoff —
and NEVER advances the cursor on a failed read (C-AtLeastOnce). reel-af is Flask/WSGI, so
the driver is a synchronous loop with an injected ``stop_event`` + ``sleep`` (thread-driven
in prod, called directly here).
"""

from __future__ import annotations

import threading
import time

from conftest import FakeEventReader, make_deps, make_event
from events import LOG_READ_ERROR, run_research_consumer_loop, start_research_consumer

CONSUMER = "reel-af"


class _Recorder:
    """Minimal logger capturing formatted lines (structured-log counters)."""

    def __init__(self):
        self.lines: list[str] = []

    def info(self, msg, *args):
        self.lines.append(msg % args if args else msg)


def _stop_after(n: int, stop: threading.Event):
    """A sleep that sets ``stop`` after ``n`` calls, so the synchronous loop is bounded."""
    calls = {"n": 0}

    def _sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= n:
            stop.set()

    return _sleep, calls


# ─────────────────────────── clean start / consume / stop ───────────────────────────


def test_driver_runs_consume_cycles_then_stops():
    reader = FakeEventReader([make_event(1, id="a", subject="exec-a")])
    deps = make_deps(events=reader)
    stop = threading.Event()
    sleep, calls = _stop_after(3, stop)
    run_research_consumer_loop(
        deps, stop_event=stop, sleep=sleep, poll_interval_seconds=0, consumer=CONSUMER
    )
    assert len(reader.read_calls) >= 1          # consumed at least once
    assert stop.is_set()                        # loop exited on the stop signal
    assert calls["n"] >= 3


def test_driver_stops_promptly_when_already_signalled():
    reader = FakeEventReader([])
    deps = make_deps(events=reader)
    stop = threading.Event()
    stop.set()                                  # pre-cancelled
    run_research_consumer_loop(deps, stop_event=stop, sleep=lambda _s: None)
    assert reader.read_calls == []              # never entered the body


# ─────────────────────────── bounded backoff on read error ───────────────────────────


class _BoomReader:
    def __init__(self):
        self.read_calls: list = []

    def read_since(self, *args):
        self.read_calls.append(args)
        raise RuntimeError("control plane down")


def test_driver_backs_off_on_read_error_and_leaves_cursor_unmoved():
    reader = _BoomReader()
    deps = make_deps(events=reader)
    start_cursor = deps.cursor.get(CONSUMER)
    stop = threading.Event()
    rec = _Recorder()
    sleep, _calls = _stop_after(2, stop)        # stop during the backoff window
    run_research_consumer_loop(
        deps, stop_event=stop, sleep=sleep, logger=rec,
        max_retries=5, backoff_seconds=0, poll_interval_seconds=0, consumer=CONSUMER,
    )
    assert any(LOG_READ_ERROR in line for line in rec.lines)     # loud log, not a silent death
    assert deps.cursor.get(CONSUMER) == start_cursor            # cursor NOT advanced on failure


# ─────────────────────────── thread start/stop lifecycle ───────────────────────────


def test_start_research_consumer_spawns_and_stops_cleanly():
    reader = FakeEventReader([])                 # idle loop
    deps = make_deps(events=reader)
    handle = start_research_consumer(
        deps, poll_interval_seconds=0.01, backoff_seconds=0.01, consumer=CONSUMER
    )
    time.sleep(0.05)
    assert handle.is_alive()
    handle.stop(timeout=2)
    assert not handle.is_alive()                 # graceful cancel + join


def test_create_app_does_not_start_consumer_by_default(monkeypatch):
    monkeypatch.delenv("REEL_CONSUMER_ENABLED", raising=False)
    from server import _maybe_start_consumer

    assert _maybe_start_consumer(make_deps()) is None    # off by default → suite stays clean


def test_maybe_start_consumer_starts_when_enabled(monkeypatch):
    monkeypatch.setenv("REEL_CONSUMER_ENABLED", "1")
    from server import _maybe_start_consumer

    deps = make_deps(events=FakeEventReader([]))
    handle = _maybe_start_consumer(deps)
    assert handle is not None
    handle.stop(timeout=2)
    assert not handle.is_alive()
