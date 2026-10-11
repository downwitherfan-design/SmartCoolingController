"""Qt bridge for the application event bus.

The telemetry engine samples on a worker thread and publishes to the
:class:`~app.core.events.EventBus`.  Qt widgets may only be touched from the
thread that owns them (the GUI thread), so the UI must never subscribe to the
bus directly.

This bridge is the single marshalling point: it subscribes to the requested
topics, re-emits every event through one Qt signal, and lets Qt's
``AutoConnection`` deliver it on the GUI thread.  Because the emitting thread
differs from the receiver's thread, Qt queues the call automatically — no locks,
no ``QMetaObject.invokeMethod`` boilerplate, and a synchronous call on the GUI
thread (as in tests) still dispatches immediately.

Usage::

    bridge = QtEventBridge(bus, parent=window)
    bridge.attach({TOPIC_SNAPSHOT, TOPIC_ALERT})
    bridge.received.connect(window._on_bus_event)
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, Signal

from app.core.events import Event, EventBus


class QtEventBridge(QObject):
    """Re-emits bus events on the Qt main thread as a single ``event`` signal.

    Topics attached as *coalescing* (see :meth:`attach`) keep only their newest
    queued event.  When the GUI thread is busy, an unbounded run of display
    snapshots would otherwise pile up in Qt's event queue and each be rendered
    in turn once the thread frees up — a visible stutter that shows obsolete
    frames.  Coalescing collapses that backlog to one event per topic, so the
    renderer always draws the freshest state.  Lossless topics (alerts, control,
    persistence, connection state) are never coalesced.
    """

    #: Emitted for every delivered topic; the payload is the bus :class:`Event`.
    received = Signal(object)

    #: Internal: schedules a coalesced drain on the GUI thread.  Emitted at most
    #: once while a drain is outstanding, so at most one callback is ever queued.
    _drain_requested = Signal()

    def __init__(self, bus: EventBus, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._unsubscribe: list[Callable[[], None]] = []
        self._topics: set[str] = set()
        self._coalesced: set[str] = set()
        self._pending: dict[str, Event] = {}
        self._drain_scheduled = False
        self._lock = threading.Lock()
        self._drain_requested.connect(self._drain)

    @property
    def topics(self) -> frozenset[str]:
        return frozenset(self._topics)

    def attach(self, topics: Iterable[str], *, coalesce: Iterable[str] = ()) -> None:
        """Subscribe to ``topics``; safe to call more than once per topic.

        Topics named in ``coalesce`` deliver only the newest queued event for
        that topic, collapsing a backlog while the GUI thread is busy.
        """
        coalesced = set(coalesce)
        for topic in topics:
            if topic in self._topics:
                if topic in coalesced:
                    self._coalesced.add(topic)
                continue
            self._topics.add(topic)
            if topic in coalesced:
                self._coalesced.add(topic)
            self._unsubscribe.append(self._bus.subscribe(topic, self._relay))

    def detach(self) -> None:
        """Unsubscribe from every topic and drop any queued coalesced events."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        self._topics.clear()
        self._coalesced.clear()
        with self._lock:
            self._pending.clear()
            self._drain_scheduled = False

    @property
    def pending_display_events(self) -> int:
        """Number of coalesced events waiting for the next drain (bounded)."""
        with self._lock:
            return len(self._pending)

    def _relay(self, event: Event) -> None:
        # Called on the publisher's thread.  ``emit`` is thread-safe and, for a
        # receiver living on another thread, Qt queues the slot invocation.
        if event.topic not in self._coalesced:
            self.received.emit(event)
            return
        with self._lock:
            self._pending[event.topic] = event
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
        self._drain_requested.emit()

    def _drain(self) -> None:
        """Deliver the newest coalesced event per topic (runs on the GUI thread)."""
        with self._lock:
            # Swap the map atomically: an event published right now lands in the
            # fresh map and is delivered by the next drain, never dropped.
            pending = self._pending
            self._pending = {}
            self._drain_scheduled = False
        for event in pending.values():
            self.received.emit(event)


__all__ = ["QtEventBridge"]

