"""The Qt event bridge must deliver worker-thread bus events on the GUI thread.

This is the invariant that makes the whole UI safe: the telemetry engine emits
from a background thread, and Qt widgets may only be touched from the thread
that owns them.  The test publishes from a real worker thread and asserts the
slot observes the *GUI* thread identity.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread

from app.application.qt_bridge import QtEventBridge
from app.core.events import Event, EventBus

TOPIC = "bridge.test"


def test_events_published_from_a_worker_arrive_on_the_gui_thread(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    received: list[tuple[Event, bool]] = []

    def slot(event: Event) -> None:
        received.append((event, QThread.currentThread() is qapp.thread()))

    bridge.received.connect(slot)
    bridge.attach([TOPIC])

    publisher_done = threading.Event()

    def publish_from_worker() -> None:
        bus.publish(TOPIC, {"value": 1})
        publisher_done.set()

    worker = threading.Thread(target=publish_from_worker, name="publisher")
    worker.start()
    worker.join(timeout=5)

    assert publisher_done.is_set()

    # The slot is queued onto the GUI thread; pump the loop until it lands.
    deadline = time.time() + 5.0
    while not received and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert received, "the queued event never reached the slot"
    event, on_gui_thread = received[0]
    assert on_gui_thread, "the slot ran on the worker thread, not the GUI thread"
    assert event.payload == {"value": 1}

    bridge.detach()


def test_detach_stops_delivery(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[Event] = []
    bridge.received.connect(seen.append)
    bridge.attach([TOPIC])

    bus.publish(TOPIC, 1)
    qapp.processEvents()
    assert len(seen) == 1

    bridge.detach()
    bus.publish(TOPIC, 2)
    qapp.processEvents()
    assert len(seen) == 1, "detached bridge must not deliver further events"
    assert bridge.topics == frozenset()


def test_attach_is_idempotent(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[Event] = []
    bridge.received.connect(seen.append)
    bridge.attach([TOPIC])
    bridge.attach([TOPIC, TOPIC])

    bus.publish(TOPIC, 1)
    qapp.processEvents()
    assert len(seen) == 1, "duplicate attach must not double-deliver"


def test_synchronous_publish_on_gui_thread_dispatches_immediately(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[Event] = []
    bridge.received.connect(seen.append)
    bridge.attach([TOPIC])

    bus.publish(TOPIC, "now")
    # A direct connection on the same thread means no loop pump is required.
    assert seen and seen[0].payload == "now"
    bridge.detach()
