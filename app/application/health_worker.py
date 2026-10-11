"""Off-thread execution of the periodic health check.

The subsystem-health badge is refreshed on a timer.  Running that check on the
GUI thread means any probe that touches hardware (a WMI/LibreHardwareMonitor
read, a disk enumeration) freezes painting and input for as long as the probe
takes.  A probe is not "cheap" because a comment says so; the only safe rule is
that the GUI thread never performs unpredictable I/O.

This worker owns a single long-lived thread that runs the check.  A coalescing
flag guarantees at most one check is in flight, so a slow or hung probe can
never accumulate work: a request that arrives while a check is running is
folded into the next run instead of queueing.  The report reaches the GUI
through the existing event bus (``HealthService.check`` publishes
``TOPIC_HEALTH``, which the Qt bridge marshals onto the GUI thread), so the
worker needs no Qt affinity of its own and the architecture stays as-is.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class HealthCheckWorker:
    """Runs ``check`` on a dedicated thread, coalescing overlapping requests."""

    def __init__(self, check: Callable[[], object]) -> None:
        self._check = check
        self._lock = threading.Lock()
        self._pending = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the worker thread; safe to call more than once."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="HealthCheck", daemon=True)
        self._thread.start()

    def request(self) -> None:
        """Ask for a check.  A request during an in-flight check is coalesced.

        This never blocks the caller and never queues unbounded work — it just
        flips a flag and wakes the thread.
        """
        with self._lock:
            if self._pending or self._stop.is_set():
                return
            self._pending = True
        self._wake.set()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the worker to exit and join it with a bounded timeout."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                self._pending = False
            try:
                self._check()
            except Exception:  # noqa: BLE001 - a failing probe must not kill the worker
                # HealthService.check already isolates individual probes; this
                # guards the (unexpected) case where the whole check raises.
                continue


__all__ = ["HealthCheckWorker"]
