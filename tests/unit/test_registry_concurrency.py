"""Hardware discovery must be safe to run while telemetry keeps reading.

Background discovery introduces a second thread that mutates the registry's
backend list while the telemetry sampler calls :meth:`HardwareRegistry.read`
from its own thread.  These tests pin that contract: a read must never observe a
half-built backend list, and a slow ``open()`` must not run under the lock (or
reads would serialise behind it).
"""

from __future__ import annotations

import threading
import time

from app.core.enums import BackendKind
from app.hardware.base import HardwareMonitorBackend
from app.hardware.registry import HardwareRegistry
from app.models.sensors import HardwareCategory, SensorReading, SensorType


def _reading(sensor_id: str, backend: BackendKind) -> SensorReading:
    return SensorReading(
        sensor_id=sensor_id,
        hardware_name="Fake",
        hardware_category=HardwareCategory.CPU,
        sensor_name=sensor_id,
        sensor_type=SensorType.TEMPERATURE,
        value=42.0,
        unit="°C",
        backend=backend,
    )


class _SlowBackend(HardwareMonitorBackend):
    """Opens slowly and records whether the registry lock was held meanwhile."""

    kind = BackendKind.PSUTIL

    def __init__(self, opened: threading.Event, *, open_seconds: float = 0.3) -> None:
        super().__init__()
        self._opened = opened
        self._open_seconds = open_seconds
        self.open_started = threading.Event()

    def is_available(self) -> bool:
        return True

    def open(self) -> None:
        self.open_started.set()
        time.sleep(self._open_seconds)
        self._mark_ready()
        self._opened.set()

    def read(self) -> list[SensorReading]:
        return [_reading("fake.temp", self.kind)]


def test_read_never_sees_a_partially_built_backend_list() -> None:
    """Concurrent reads during discovery always see a consistent snapshot."""
    opened = threading.Event()
    backend = _SlowBackend(opened)
    registry = HardwareRegistry(factories=[lambda: backend])

    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                registry.read()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers:
        thread.start()
    try:
        registry.discover()
    finally:
        stop.set()
        for thread in readers:
            thread.join(timeout=2.0)

    assert not errors, f"reader observed an inconsistent registry: {errors}"
    assert [b.kind for b in registry.backends] == [BackendKind.PSUTIL]


def test_slow_open_does_not_hold_the_registry_lock() -> None:
    """A concurrent read must not block behind a slow ``backend.open()``."""
    opened = threading.Event()
    backend = _SlowBackend(opened, open_seconds=0.4)
    registry = HardwareRegistry(factories=[lambda: backend])

    discover_thread = threading.Thread(target=registry.discover)
    discover_thread.start()
    assert backend.open_started.wait(1.0), "discovery never reached open()"

    # While open() sleeps, a read must return promptly rather than waiting.
    start = time.perf_counter()
    registry.read()
    elapsed = time.perf_counter() - start
    discover_thread.join(timeout=3.0)

    assert elapsed < 0.2, f"read blocked behind open() for {elapsed:.3f}s"


def test_discovery_uses_the_priority_order() -> None:
    """The first viable backend wins and later ones still load as fallbacks."""
    opened: list[BackendKind] = []

    def make(kind: BackendKind) -> type[HardwareMonitorBackend]:
        class _Backend(_SlowBackend):
            def __init__(self) -> None:
                super().__init__(threading.Event(), open_seconds=0.0)
                self.kind = kind

            def open(self) -> None:
                opened.append(kind)
                self._mark_ready()

        return _Backend

    first, second = make(BackendKind.LIBRE_HARDWARE_MONITOR), make(BackendKind.PSUTIL)
    registry = HardwareRegistry(factories=[first, second])
    result = registry.discover()

    assert opened == [BackendKind.LIBRE_HARDWARE_MONITOR, BackendKind.PSUTIL]
    assert result.selected is BackendKind.LIBRE_HARDWARE_MONITOR
