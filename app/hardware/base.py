"""Hardware monitoring back-end contract.

Every back-end implements :class:`HardwareMonitorBackend`.  The interface is
deliberately small and synchronous: a back-end owns whatever native resources
it needs and exposes a flat list of :class:`SensorReading` objects plus a
:class:`HardwareProfile`.  Higher layers (discovery, normalisation, telemetry)
never talk to a vendor library directly.

A back-end must never fabricate values.  When a metric is genuinely
unavailable it is simply not emitted, or it is emitted with
``unavailable=True`` (spec §12, §300).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.enums import BackendKind, BackendStatus
from app.models.sensors import HardwareProfile, SensorReading


class HardwareMonitorBackend(ABC):
    """Abstract hardware telemetry source."""

    #: Which back-end this class implements.
    kind: BackendKind = BackendKind.NONE

    def __init__(self) -> None:
        self._status: BackendStatus = BackendStatus.UNKNOWN
        self._error_count: int = 0
        self._last_error: str = ""
        self._last_success: float | None = None
        self._read_count: int = 0

    # -- lifecycle ---------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check: could this back-end plausibly work on this machine?"""

    @abstractmethod
    def open(self) -> None:
        """Acquire native resources.  Raise on unrecoverable failure."""

    def close(self) -> None:
        """Release native resources.  Safe to call multiple times."""

    # -- data --------------------------------------------------------------

    @abstractmethod
    def read(self) -> list[SensorReading]:
        """Return the current readings.  Must never raise on a single sensor."""

    def profile(self) -> HardwareProfile:
        """Static description of the machine; may be empty for some back-ends."""
        return HardwareProfile()

    # -- diagnostics -------------------------------------------------------

    @property
    def status(self) -> BackendStatus:
        return self._status

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def last_success(self) -> float | None:
        return self._last_success

    @property
    def read_count(self) -> int:
        return self._read_count

    def _mark_ready(self) -> None:
        self._status = BackendStatus.READY

    def _mark_degraded(self, reason: str) -> None:
        self._status = BackendStatus.DEGRADED
        self._last_error = reason

    def _mark_error(self, reason: str) -> None:
        self._status = BackendStatus.ERROR
        self._last_error = reason
        self._error_count += 1

    def _mark_unavailable(self, reason: str) -> None:
        self._status = BackendStatus.UNAVAILABLE
        self._last_error = reason

    def _record_read(self) -> None:
        from app.core.clock import utc_timestamp

        self._read_count += 1
        self._last_success = utc_timestamp()

    def __enter__(self) -> HardwareMonitorBackend:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["HardwareMonitorBackend"]
