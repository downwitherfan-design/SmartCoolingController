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

from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, Signal

from app.core.events import Event, EventBus


class QtEventBridge(QObject):
    """Re-emits bus events on the Qt main thread as a single ``event`` signal."""

    #: Emitted for every attached topic; the payload is the bus :class:`Event`.
    received = Signal(object)

    def __init__(self, bus: EventBus, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._unsubscribe: list[Callable[[], None]] = []
        self._topics: set[str] = set()

    @property
    def topics(self) -> frozenset[str]:
        return frozenset(self._topics)

    def attach(self, topics: Iterable[str]) -> None:
        """Subscribe to ``topics``; safe to call more than once per topic."""
        for topic in topics:
            if topic in self._topics:
                continue
            self._topics.add(topic)
            self._unsubscribe.append(self._bus.subscribe(topic, self._relay))

    def detach(self) -> None:
        """Unsubscribe from every topic."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        self._topics.clear()

    def _relay(self, event: Event) -> None:
        # Called on the publisher's thread.  ``emit`` is thread-safe and, for a
        # receiver living on another thread, Qt queues the slot invocation.
        self.received.emit(event)


__all__ = ["QtEventBridge"]

