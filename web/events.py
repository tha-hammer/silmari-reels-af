"""INT-02 durable-cursor consumer of ``research.completed`` (reel-af CONSUMER side).

reel-af learns a deep-research run finished WITHOUT a client opening the research
view, and without missing events across downtime, by reading the DURABLE bus:
``research.completed`` CloudEvents from its own cursor (``last_event_sequence``),
type-filtered, deduped on the CloudEvents ``id``, then — on a fresh event — fetches
the document BY REFERENCE (never ``notes``) and idempotently stamps provenance in ONE
transaction with the dedup insert and cursor advance.

Contracts (``specs/cross-app-handoff.pattern.md`` §4):
- C-AtLeastOnce — read from the durable cursor; advance ONLY after the effect commits.
- C-Idempotent — dedup on the CloudEvents ``id`` before side effects; replay = one effect.
- C-Own — stamp only reel-af's own tables; the value is the resolved LOCAL ``research_run.id``
  (UUID), never the raw text ``execution_id``.
- C-Notification — fetch the body by ``result_ref`` on demand; never carry it in the event.

The read surface is abstracted behind ``EventReaderPort`` (durable ``/events`` poll or SSE —
both cursor-based); the hand-off NEVER rides the in-memory ``GlobalExecutionEventBus`` (A1).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from control_plane import fetch_document_by_ref

# ─────────────────────────── named constants (no magic strings) ───────────────────────────

# The single event type this consumer reacts to (C-Correlation subject = execution_id).
RESEARCH_COMPLETED_TYPE = "com.silmari.research.completed.v1"

# CloudEvents record keys (the durable read surface yields these + a monotonic ``sequence``).
EVENT_ID_KEY = "id"
EVENT_TYPE_KEY = "type"
EVENT_SUBJECT_KEY = "subject"          # == execution_id (C-Correlation)
EVENT_SEQUENCE_KEY = "sequence"        # monotonic outbox Seq (durable cursor key)
EVENT_DATA_KEY = "data"
DTO_RESEARCH_PROMPT_KEY = "research_prompt"

# Structured-log counter names (reel-af has no metrics sink; these are stable log events).
LOG_STAMPED = "research_completed_stamped_total"
LOG_DEDUPED = "research_completed_deduped_total"
LOG_UNMATCHED = "research_completed_unmatched_total"
LOG_MALFORMED = "research_event_malformed_total"
LOG_SNAPSHOT_INCOMPLETE = "research_snapshot_incomplete_total"
LOG_READ_ERROR = "research_read_error_total"

# ─────────────────────────── flat config (web/events.json; one-jump access) ───────────────────────────

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "events.json")


def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


CONFIG = _load_config()
DEFAULT_CONSUMER = CONFIG["consumer_name"]
DEFAULT_BATCH_LIMIT = CONFIG["batch_limit"]
DEFAULT_POLL_INTERVAL_S = CONFIG["poll_interval_seconds"]
DEFAULT_MAX_RETRIES = CONFIG["max_retries"]
DEFAULT_BACKOFF_S = CONFIG["backoff_seconds"]


@dataclass(frozen=True)
class ConsumeResult:
    """Outcome of the one-tx effect for a fresh event: whether it was the first
    observation (dedup) and whether a local ``research_run`` was resolved to stamp."""

    first_seen: bool
    local_run_found: bool


def _log(logger, event: str, **fields) -> None:
    """Emit one structured-log counter line (stable name + fields). Never carries
    ``notes`` (never read)."""
    if logger is not None:
        logger.info("%s %s", event, fields)


def _malformed_reason(record: dict) -> str | None:
    """A poison/malformed event: missing ``id`` or ``subject``, or the wrong ``type``.
    Returns a reason string, or ``None`` when the record is well-formed."""
    if not record.get(EVENT_ID_KEY):
        return "missing_id"
    if not record.get(EVENT_SUBJECT_KEY):
        return "missing_subject"
    if record.get(EVENT_TYPE_KEY) != RESEARCH_COMPLETED_TYPE:
        return "wrong_type"
    return None


def _fetch_document(deps, record: dict, logger) -> None:
    """Fetch the research document BY REFERENCE (C-Notification), for its effect only: it
    satisfies the by-reference read and logs an incomplete/failed snapshot loud. It never
    blocks the stamp (provenance is keyed on ``execution_id``, not on the package), so the
    fetched document is not returned to the caller."""
    execution_id = record[EVENT_SUBJECT_KEY]
    dto_prompt = (record.get(EVENT_DATA_KEY) or {}).get(DTO_RESEARCH_PROMPT_KEY)
    try:
        status, body, _headers = deps.control_plane.get_execution(execution_id)
    except Exception as exc:  # noqa: BLE001 - a fetch failure never fails the stamp
        _log(logger, LOG_SNAPSHOT_INCOMPLETE, execution_id=execution_id, error=str(exc))
        return
    if status >= 400:
        _log(logger, LOG_SNAPSHOT_INCOMPLETE, execution_id=execution_id, status=status)
        return
    document = fetch_document_by_ref({**body, "execution_id": execution_id}, dto_prompt=dto_prompt)
    if not document.package_present:
        _log(logger, LOG_SNAPSHOT_INCOMPLETE, execution_id=execution_id)


def consume_since(
    deps,
    cursor: int,
    *,
    consumer: str = DEFAULT_CONSUMER,
    subject: str | None = None,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    logger=None,
) -> int:
    """Read ``research.completed`` events with ``sequence > cursor`` (type-filtered) and,
    per event, run the idempotent consume step. Returns the highest cursor reached.

    Serial guard-clause steps — no side-effecting control expressions:
      1. dispatched-case tight filter (``subject == my_dispatched_execution_id``) → advance, skip;
      2. malformed → mark (if id) + advance past, never stall (loud log);
      3. already processed → advance, skip the effect (dedup, C-Idempotent);
      4. fresh → fetch by reference (B3) + one-tx ``stamp_dedup_advance`` (B4, C5).

    The cursor advances for skipped events too (progress); for a fresh event the advance
    happens INSIDE ``stamp_dedup_advance`` so it commits with the effect (C-AtLeastOnce)."""
    log = logger if logger is not None else getattr(deps, "logger", None)
    records = deps.events.read_since(cursor, RESEARCH_COMPLETED_TYPE, batch_limit)
    highest = cursor

    def _skip_past(seq: int) -> int:
        # Advance the cursor past a filtered/malformed/deduped event (no stamp) and report
        # the new high-water seq — the shared trailer of the three skip branches.
        deps.cursor.advance(consumer, seq)
        return seq

    for record in records:
        seq = record.get(EVENT_SEQUENCE_KEY)

        if subject is not None and record.get(EVENT_SUBJECT_KEY) != subject:
            highest = _skip_past(seq)
            continue

        reason = _malformed_reason(record)
        if reason is not None:
            _log(log, LOG_MALFORMED, reason=reason, event_seq=seq)
            cloudevents_id = record.get(EVENT_ID_KEY)
            if cloudevents_id:
                deps.processed.mark(cloudevents_id, record.get(EVENT_SUBJECT_KEY))
            highest = _skip_past(seq)
            continue

        cloudevents_id = record[EVENT_ID_KEY]
        execution_id = record[EVENT_SUBJECT_KEY]
        if deps.processed.already_processed(cloudevents_id):
            _log(log, LOG_DEDUPED, cloudevents_id=cloudevents_id, event_seq=seq)
            highest = _skip_past(seq)
            continue

        _fetch_document(deps, record, log)                 # B3 by-reference (never notes)
        result = deps.processed.stamp_dedup_advance(record, consumer)  # B4 one-tx effect
        counter = LOG_STAMPED if result.local_run_found else LOG_UNMATCHED
        _log(log, counter, cloudevents_id=cloudevents_id, execution_id=execution_id, event_seq=seq)
        highest = seq

    return highest


def run_research_consumer_loop(
    deps,
    *,
    stop_event,
    consumer: str = DEFAULT_CONSUMER,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_S,
    sleep=None,
    logger=None,
) -> None:
    """Production driver (B5): read from the durable cursor, process, sleep, repeat, until
    ``stop_event`` is set (graceful stop). A read/transport error triggers BOUNDED retry
    with backoff and a loud log — it never crashes the app, never silently dies, and never
    advances the cursor on a failed read (catch-up from the last committed Seq — C-AtLeastOnce).

    Synchronous by design: reel-af is Flask/WSGI (no async lifespan). The inter-cycle pause
    uses the INTERRUPTIBLE ``stop_event.wait`` by default, so shutdown returns promptly even
    with a long poll interval; tests inject a ``sleep`` to drive/bound the loop deterministically.
    A plain background task — no scheduler framework, never the in-memory bus."""
    log = logger if logger is not None else getattr(deps, "logger", None)
    wait = sleep if sleep is not None else stop_event.wait   # interruptible in prod
    while not stop_event.is_set():
        try:
            start = deps.cursor.get(consumer)
            consume_since(
                deps, start, consumer=consumer, batch_limit=batch_limit, logger=log
            )
        except Exception as exc:  # noqa: BLE001 - a read error must not kill the driver
            _backoff(stop_event, exc, max_retries, backoff_seconds, wait, log)
        if stop_event.is_set():
            break
        wait(poll_interval_seconds)


def _backoff(stop_event, exc, max_retries: int, backoff_seconds: float, wait, logger) -> None:
    """Bounded retry/backoff for a durable-read error; the cursor is untouched so the next
    successful cycle catches up. Stops early if ``stop_event`` fires mid-backoff."""
    for attempt in range(1, max_retries + 1):
        _log(logger, LOG_READ_ERROR, error=str(exc), attempt=attempt)
        if stop_event.is_set():
            return
        wait(backoff_seconds)


class ConsumerHandle:
    """Handle to a running background consumer thread; ``stop`` cancels + awaits it
    (graceful shutdown — no orphaned task, no lost commit)."""

    def __init__(self, stop_event, thread):
        self._stop_event = stop_event
        self._thread = thread

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()


def _build_research_handler(deps, *, consumer=DEFAULT_CONSUMER):
    """Build the middleware-compatible handler for research.completed (B3).

    Returns h(dto, fetch_body) that durable-dedups on CE id (D4), fetches by
    reference (C-Notification, non-blocking), and stamps via stamp_dedup_advance."""
    log = getattr(deps, "logger", None)

    def _handler(dto, fetch_body):
        if deps.processed.already_processed(dto.event_id):
            _log(log, LOG_DEDUPED, cloudevents_id=dto.event_id)
            return

        try:
            fetch_body(dto.execution_id)
        except Exception as exc:  # noqa: BLE001 - fetch failure never fails the stamp
            _log(log, LOG_SNAPSHOT_INCOMPLETE, execution_id=dto.execution_id, error=str(exc))

        event_dict = {"id": dto.event_id, "subject": dto.execution_id, "sequence": dto.sequence}
        result = deps.processed.stamp_dedup_advance(event_dict, consumer)
        counter = LOG_STAMPED if result.local_run_found else LOG_UNMATCHED
        _log(log, counter, cloudevents_id=dto.event_id, execution_id=dto.execution_id)

    return _handler


def start_research_consumer(deps, *, logger=None, **kwargs) -> ConsumerHandle:
    """Spawn the durable-cursor consumer on a daemon thread (reel-af is Flask/WSGI, so a
    thread — not an asyncio task — is the lifecycle primitive). Returns a ``ConsumerHandle``
    whose ``stop()`` cancels + joins it. Started opt-in from the production app path only."""
    import threading

    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_research_consumer_loop,
        args=(deps,),
        kwargs={"stop_event": stop_event, "logger": logger, **kwargs},
        name="research-consumer",
        daemon=True,
    )
    thread.start()
    return ConsumerHandle(stop_event, thread)
