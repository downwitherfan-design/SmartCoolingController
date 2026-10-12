"""EventBus behaviour, including the re-entrancy guard.

The bus docstring used to promise a re-entrancy guard that did not exist, so a
handler subscribing to the topic it also publishes could recurse until the
stack overflowed.  These tests pin the guard, the fault-isolation behaviour and
thread safety.
"""

from __future__ import annotations

import threading

from app.core.events import MAX_REENTRANCY, EventBus


def test_publish_delivers_to_every_subscriber() -> None:
    bus = EventBus()
    seen: list = []
    bus.subscribe("t", lambda e: seen.append(e.payload))
    bus.subscribe("t", lambda e: seen.append(e.payload * 2))
    bus.publish("t", 21)
    assert seen == [21, 42]


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    seen: list = []
    unsubscribe = bus.subscribe("t", lambda e: seen.append(e.payload))
    bus.publish("t", 1)
    unsubscribe()
    bus.publish("t", 2)
    assert seen == [1]


def test_a_raising_handler_does_not_starve_the_others() -> None:
    bus = EventBus()
    seen: list = []

    def boom(_event) -> None:
        raise RuntimeError("handler fault")

    bus.subscribe("t", boom)
    bus.subscribe("t", lambda e: seen.append(e.payload))
    bus.publish("t", "ok")
    assert seen == ["ok"]


def test_reentrant_self_publish_is_bounded() -> None:
    bus = EventBus()
    calls = 0

    def loop(event) -> None:
        nonlocal calls
        calls += 1
        # Republish the same topic from inside its own handler.  Without the
        # guard this recurses forever; with it, delivery stops at the limit.
        bus.publish("t", event.payload)

    bus.subscribe("t", loop)
    bus.publish("t", "hello")
    assert calls == MAX_REENTRANCY


def test_shallow_cross_topic_re_entrancy_is_allowed() -> None:
    bus = EventBus()
    seen: list = []
    bus.subscribe("a", lambda e: bus.publish("b", e.payload + 1))
    bus.subscribe("b", lambda e: seen.append(e.payload))
    bus.publish("a", 1)
    assert seen == [2]


def test_concurrent_publishers_are_all_delivered() -> None:
    bus = EventBus()
    seen: list = []
    lock = threading.Lock()

    def handler(event) -> None:
        with lock:
            seen.append(event.payload)

    bus.subscribe("t", handler)
    threads = [
        threading.Thread(target=lambda n=n: [bus.publish("t", n) for _ in range(50)])
        for n in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(seen) == 200
