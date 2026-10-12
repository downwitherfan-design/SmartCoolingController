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


def _publish_from_worker(bus: EventBus, topic: str, values) -> None:
    """Publish a burst on a real worker thread, as the telemetry engine does."""
    worker = threading.Thread(target=lambda: [bus.publish(topic, v) for v in values])
    worker.start()
    worker.join(timeout=5)


def test_coalesced_topic_collapses_a_backlog_to_the_newest(qapp) -> None:
    """While the GUI is busy, display events must stay bounded and newest-wins."""
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[int] = []
    bridge.received.connect(lambda e: seen.append(e.payload))
    bridge.attach([TOPIC], coalesce=[TOPIC])

    # A worker publishes a burst while the GUI thread is held busy (no pump).
    _publish_from_worker(bus, TOPIC, range(500))
    assert bridge.pending_display_events == 1, "backlog must be bounded to one"

    qapp.processEvents()
    assert seen == [499], "only the newest display value may be rendered"


def test_lossless_topics_are_never_coalesced(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[object] = []
    bridge.received.connect(lambda e: seen.append(e.payload))
    bridge.attach([TOPIC, "alert"], coalesce=[TOPIC])

    _publish_from_worker(bus, "alert", range(50))
    qapp.processEvents()
    assert seen == list(range(50)), "critical topics must not lose an event"


def test_coalesced_and_lossless_share_the_bridge(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[tuple[str, object]] = []
    bridge.received.connect(lambda e: seen.append((e.topic, e.payload)))
    bridge.attach([TOPIC, "alert"], coalesce=[TOPIC])

    _publish_from_worker(bus, TOPIC, [1, 2, 3])
    _publish_from_worker(bus, "alert", ["a", "b"])
    qapp.processEvents()

    topics = [t for t, _ in seen]
    assert seen.count(("alert", "a")) == 1 and seen.count(("alert", "b")) == 1
    assert [p for t, p in seen if t == TOPIC] == [3], "display keeps only the newest"
    assert topics.count("alert") == 2


def test_detach_drops_pending_coalesced_events(qapp) -> None:
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[object] = []
    bridge.received.connect(seen.append)
    bridge.attach([TOPIC], coalesce=[TOPIC])

    _publish_from_worker(bus, TOPIC, [1])
    assert bridge.pending_display_events == 1
    bridge.detach()
    assert bridge.pending_display_events == 0
    qapp.processEvents()
    assert seen == [], "a detached bridge must not deliver queued events"



def test_detach_during_publication_delivers_nothing(qapp) -> None:
    """Shutdown race: a relay already in flight must not reach a detached UI.

    ``EventBus.publish`` snapshots its handler list, so ``detach()`` can run
    after the bridge's relay was captured but before it is called.  The bridge
    must observe the detach and drop the event instead of emitting into a window
    that is being torn down.
    """
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[object] = []
    bridge.received.connect(seen.append)

    entered = threading.Event()
    release = threading.Event()

    def blocker(event: Event) -> None:
        entered.set()
        release.wait(5)

    bus.subscribe(TOPIC, blocker)  # registered before the bridge: runs first
    bridge.attach([TOPIC], coalesce=[TOPIC])

    worker = threading.Thread(target=lambda: bus.publish(TOPIC, 1))
    worker.start()
    assert entered.wait(5), "publish() never snapshotted the handler list"
    # publish() has snapshotted handlers; detach() while the relay is pending.
    bridge.detach()
    release.set()
    worker.join(timeout=5)
    qapp.processEvents()

    assert seen == [], "a bridge detached mid-publication must deliver nothing"


def test_reattach_after_detach_resumes_delivery(qapp) -> None:
    """detach() then attach() again must work (lifecycle: reconnect)."""
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[object] = []
    bridge.received.connect(lambda e: seen.append(e.payload))

    bridge.attach([TOPIC], coalesce=[TOPIC])
    _publish_from_worker(bus, TOPIC, [1])
    qapp.processEvents()
    assert seen == [1]

    bridge.detach()
    _publish_from_worker(bus, TOPIC, [2])
    qapp.processEvents()
    assert seen == [1], "detached bridge must not deliver"

    bridge.attach([TOPIC], coalesce=[TOPIC])
    _publish_from_worker(bus, TOPIC, [3])
    qapp.processEvents()
    assert seen == [1, 3], "re-attached bridge must resume delivery"


def test_high_pressure_stays_bounded_and_delivers_newest(qapp) -> None:
    """5000 publications while the GUI is blocked must collapse to one, newest."""
    bus = EventBus()
    bridge = QtEventBridge(bus)
    seen: list[object] = []
    bridge.received.connect(lambda e: seen.append(e.payload))
    bridge.attach([TOPIC], coalesce=[TOPIC])

    _publish_from_worker(bus, TOPIC, range(5000))
    assert bridge.pending_display_events == 1, "backlog must stay bounded to one"
    qapp.processEvents()
    assert seen == [4999], "the newest snapshot must be the one rendered"


def test_critical_events_survive_display_congestion(qapp) -> None:
    """Alerts published during a display backlog must all be delivered, in order."""
    bus = EventBus()
    bridge = QtEventBridge(bus)
    alerts: list[object] = []
    display: list[object] = []
    bridge.received.connect(
        lambda e: alerts.append(e.payload) if e.topic == "alert" else display.append(e.payload)
    )
    bridge.attach([TOPIC, "alert"], coalesce=[TOPIC])

    _publish_from_worker(bus, TOPIC, range(1000))   # congest the display path
    _publish_from_worker(bus, "alert", range(25))   # critical path while congested
    qapp.processEvents()

    assert alerts == list(range(25)), "every critical event must be delivered, in order"
    assert display == [999], "display still shows only the newest snapshot"
