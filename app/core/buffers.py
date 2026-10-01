"""Bounded, thread-safe containers used by the telemetry pipeline.

The spec is explicit about bounded in-memory buffers (spec §89, §206) and about
never letting a slow consumer grow a queue without limit.  These primitives
give the sampler a fixed memory footprint regardless of runtime.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """A fixed-capacity deque with a lock and snapshot semantics."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: deque[T] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    def append(self, item: T) -> None:
        with self._lock:
            self._items.append(item)

    def extend(self, items: Iterable[T]) -> None:
        with self._lock:
            self._items.extend(items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def latest(self, default: T | None = None) -> T | None:
        with self._lock:
            return self._items[-1] if self._items else default

    def snapshot(self) -> list[T]:
        with self._lock:
            return list(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.snapshot())


class DropOldestQueue(Generic[T]):
    """A queue that never blocks the producer and drops the oldest item.

    Used to hand samples to the persistence writer: if the disk is momentarily
    slow we prefer to drop the oldest sample rather than stall the sampler.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: deque[T] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def put(self, item: T) -> None:
        with self._not_empty:
            if len(self._items) >= self._capacity:
                self._items.popleft()
                self._dropped += 1
            self._items.append(item)
            self._not_empty.notify()

    def get(self, timeout: float | None = None) -> T | None:
        with self._not_empty:
            if not self._items:
                self._not_empty.wait(timeout)
            if not self._items:
                return None
            return self._items.popleft()

    def drain(self, limit: int | None = None) -> list[T]:
        with self._lock:
            if limit is None or limit >= len(self._items):
                items = list(self._items)
                self._items.clear()
                return items
            return [self._items.popleft() for _ in range(limit)]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class SlidingWindow:
    """A time-weighted sliding window for numeric series.

    Stores ``(timestamp, value)`` pairs and can compute mean, min, max and the
    least-squares slope over a bounded horizon.  Used by the thermal analytics
    engine so history does not grow without bound.
    """

    def __init__(self, capacity: int = 2048) -> None:
        self._buffer = RingBuffer[tuple[float, float]](capacity)

    def __len__(self) -> int:
        return len(self._buffer)

    def add(self, timestamp: float, value: float) -> None:
        self._buffer.append((timestamp, value))

    def clear(self) -> None:
        self._buffer.clear()

    def values(self) -> list[float]:
        return [value for _, value in self._buffer.snapshot()]

    def points(self) -> list[tuple[float, float]]:
        return self._buffer.snapshot()

    def since(self, timestamp: float) -> list[tuple[float, float]]:
        return [point for point in self._buffer.snapshot() if point[0] >= timestamp]

    def latest(self) -> tuple[float, float] | None:
        return self._buffer.latest()

    def mean(self, window_s: float | None = None) -> float | None:
        points = self._select(window_s)
        if not points:
            return None
        return sum(value for _, value in points) / len(points)

    def minimum(self, window_s: float | None = None) -> float | None:
        points = self._select(window_s)
        return min((value for _, value in points), default=None)

    def maximum(self, window_s: float | None = None) -> float | None:
        points = self._select(window_s)
        return max((value for _, value in points), default=None)

    def slope_per_second(self, window_s: float = 60.0) -> float | None:
        """Least-squares slope of value vs time over the window."""
        points = self._select(window_s)
        if len(points) < 3:
            return None
        n = len(points)
        mean_t = sum(t for t, _ in points) / n
        mean_v = sum(v for _, v in points) / n
        numerator = sum((t - mean_t) * (v - mean_v) for t, v in points)
        denominator = sum((t - mean_t) ** 2 for t, _ in points)
        if denominator <= 1e-9:
            return None
        return numerator / denominator

    def slope_per_minute(self, window_s: float = 60.0) -> float | None:
        slope = self.slope_per_second(window_s)
        return None if slope is None else slope * 60.0

    def _select(self, window_s: float | None) -> list[tuple[float, float]]:
        points = self._buffer.snapshot()
        if window_s is None or not points:
            return points
        cutoff = points[-1][0] - window_s
        return [point for point in points if point[0] >= cutoff]


__all__ = ["DropOldestQueue", "RingBuffer", "SlidingWindow"]
