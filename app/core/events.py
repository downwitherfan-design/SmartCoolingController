"""A tiny, dependency-free publish/subscribe event bus.

Services publish typed events; the UI (and other services) subscribe without
holding references to each other.  This keeps the layering honest: the
telemetry engine never imports Qt, it simply publishes.

Threading model: :meth:`EventBus.publish` invokes subscribers synchronously on
the calling thread.  Services that must marshal onto the UI thread do so in
their own subscriber (the Qt bridge in ``app.application`` does exactly that).
A per-thread re-entrancy guard bounds nesting: a handler that publishes its
own topic deeper than :data:`MAX_REENTRANCY` has its event dropped (and
logged) instead of recursing until the stack overflows.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.clock import utc_now

_log = logging.getLogger(__name__)

Handler = Callable[["Event"], None]

#: Depth at which re-entrant publication of the *same* topic is considered a
#: runaway loop.  Shallow nesting (a handler that legitimately publishes a
#: related event, even on the same topic) is unaffected.
MAX_REENTRANCY = 16


@dataclass(frozen=True, slots=True)
class Event:
    """Base event payload."""

    topic: str
    payload: Any = None
    timestamp: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", utc_now())


class EventBus:
    """Synchronous, thread-safe pub/sub hub."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()
        #: Per-thread depth of in-flight publishes, keyed by topic.  A recursion
        #: guard must be per-thread: two threads publishing the same topic
        #: concurrently are legitimate, whereas one thread re-entering its own
        #: publish is the runaway case.
        self._publishing: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register ``handler`` for ``topic``; returns an unsubscribe callable."""
        with self._lock:
            handlers = self._subscribers[topic]
            if handler not in handlers:
                handlers.append(handler)

        def _unsubscribe() -> None:
            self.unsubscribe(topic, handler)

        return _unsubscribe

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            handlers = self._subscribers.get(topic)
            if not handlers:
                return
            try:
                handlers.remove(handler)
            except ValueError:
                return
            if not handlers:
                self._subscribers.pop(topic, None)

    def publish(self, topic: str, payload: Any = None) -> Event:
        """Deliver an event to every subscriber of ``topic``.

        Handlers that raise are logged and skipped so one faulty subscriber
        cannot take down the sampler thread.  Publication is depth-guarded: if a
        handler recursively republishes the same topic beyond
        :data:`MAX_REENTRANCY` levels, the event is dropped and logged rather
        than trapping the process in an unbounded recursion.
        """
        event = Event(topic=topic, payload=payload)
        thread = threading.get_ident()
        with self._lock:
            depths = self._publishing[thread]
            if depths[topic] >= MAX_REENTRANCY:
                _log.error(
                    "dropping topic %r: re-entrancy depth exceeded (%d)",
                    topic,
                    MAX_REENTRANCY,
                )
                return event
            depths[topic] += 1
        try:
            with self._lock:
                handlers = list(self._subscribers.get(topic, ()))
            for handler in handlers:
                try:
                    handler(event)
                except Exception:
                    _log.exception("event handler failed for topic %r", topic)
        finally:
            with self._lock:
                depth = self._publishing.get(thread)
                if depth is not None:
                    depth[topic] -= 1
                    if not depth[topic]:
                        depth.pop(topic, None)
                    if not depth:
                        self._publishing.pop(thread, None)
        return event

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()


__all__ = ["Event", "EventBus", "Handler"]
