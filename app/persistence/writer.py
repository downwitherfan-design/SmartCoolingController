"""Background persistence writer (spec §57, §206, §208).

Telemetry arrives on the sampling thread.  Writing synchronously would stall
sampling whenever the disk hiccups, so snapshots are pushed onto a bounded
queue and flushed here in batches.  If the disk cannot keep up the oldest
snapshots are dropped — live monitoring always wins over history, and the drop
is counted and surfaced to diagnostics (spec §57).

Retention is enforced on the same thread: snapshots older than the configured
window are deleted, and simulated samples can be purged on their own (spec
§104, §300).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.core.buffers import DropOldestQueue
from app.core.logging_setup import get_logger
from app.models.telemetry import TelemetrySnapshot
from app.persistence.repositories import SnapshotRepository

_log = get_logger(__name__)

#: Flush when this many snapshots are queued, or when the interval elapses.
DEFAULT_BATCH_SIZE = 60
DEFAULT_FLUSH_INTERVAL_S = 2.0


class PersistenceWriter:
    """Drains a snapshot queue into SQLite on a background thread."""

    def __init__(
        self,
        queue: DropOldestQueue[TelemetrySnapshot],
        snapshots: SnapshotRepository,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        retention_days: int = 30,
        max_rows: int = 2_000_000,
        prune_interval_s: float = 600.0,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._queue = queue
        self._snapshots = snapshots
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._retention_days = retention_days
        self._max_rows = max_rows
        self._prune_interval_s = prune_interval_s
        self._on_error = on_error

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._written = 0
        self._dropped = 0
        self._last_prune = 0.0

    @property
    def written(self) -> int:
        return self._written

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def pending(self) -> int:
        return len(self._queue)

    def set_retention(self, days: int, *, max_rows: int | None = None) -> None:
        self._retention_days = max(1, int(days))
        if max_rows is not None:
            self._max_rows = max(1000, int(max_rows))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="PersistenceWriter", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self.flush()

    def flush(self) -> int:
        """Write everything currently queued; returns rows written."""
        return self._drain(force=True)

    # -- loop --------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._drain(force=False)
            except Exception as exc:
                self._report(exc)
            self._stop.wait(self._flush_interval_s)

    def _drain(self, *, force: bool) -> int:
        pending = len(self._queue)
        if pending == 0:
            return 0
        limit = pending if force else min(self._batch_size, pending)
        batch = self._queue.drain(limit)
        if not batch:
            return 0
        self._dropped = self._queue.dropped
        written = self._snapshots.insert_many(batch)
        self._written += written
        self._maybe_prune()
        return written

    def _maybe_prune(self) -> None:
        now = time.monotonic()
        if now - self._last_prune < self._prune_interval_s:
            return
        self._last_prune = now
        cutoff = time.time() - self._retention_days * 86400
        removed = self._snapshots.delete_before(cutoff)
        trimmed = self._snapshots.prune_to_limit(self._max_rows)
        if removed or trimmed:
            _log.info("retention pruned %d expired and %d overflow snapshots", removed, trimmed)

    def _report(self, exc: Exception) -> None:
        _log.error("persistence write failed: %s", exc)
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:
                _log.exception("persistence error handler failed")


__all__ = ["PersistenceWriter", "DEFAULT_BATCH_SIZE", "DEFAULT_FLUSH_INTERVAL_S"]
