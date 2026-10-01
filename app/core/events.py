"""A tiny, dependency-free publish/subscribe event bus.

Services publish typed events; the UI (and other services) subscribe without
holding references to each other.  This keeps the layering honest: the
telemetry engine never imports Qt, it simply publishes.

Threading model: :meth:`EventBus.publish` invokes subscribers synchronously on
the calling thread.  Services that must marshal onto the UI thread do so in
their own subscriber (the Qt bridge in ``app.application`` does exactly that).
A re-entrancy guard prevents a handler that publishes from recursing forever.
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
        self._publishing: set[str] = set()

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
        cannot take down the sampler thread.
        """
        event = Event(topic=topic, payload=payload)
        with self._lock:
            handlers = list(self._subscribers.get(topic, ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                _log.exception("event handler failed for topic %r", topic)
        return event

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()


__all__ = ["Event", "EventBus", "Handler"]
