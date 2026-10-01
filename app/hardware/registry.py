"""Hardware back-end selection and fallback (spec §12, §114, §212).

The :class:`HardwareRegistry` owns the ordered list of back-ends and performs
automatic failover:

    LibreHardwareMonitor -> WMI -> NVIDIA -> Windows API -> psutil -> Simulation

Back-ends that cannot initialise are marked unavailable and skipped.  The
registry exposes the selected back-end and a full health report so the
diagnostics page can show *why* a metric is missing (spec §114, §115).

Discovery is deliberately explicit: :meth:`HardwareRegistry.discover` is a
separate, potentially slow step, so the UI can show progress and the first
sensor load never freezes the window (spec §278, §279).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.enums import BackendKind, BackendStatus
from app.core.logging_setup import get_logger
from app.hardware.base import HardwareMonitorBackend
from app.hardware.libre_hardware_monitor import LibreHardwareMonitorBackend
from app.hardware.nvidia_backend import NvidiaBackend
from app.hardware.psutil_backend import PsutilBackend
from app.hardware.windows_api_backend import WindowsApiBackend
from app.hardware.wmi_backend import WmiBackend
from app.models.sensors import HardwareProfile, SensorReading

_log = get_logger(__name__)

BackendFactory = Callable[[], HardwareMonitorBackend]


@dataclass(slots=True)
class BackendHealth:
    kind: BackendKind
    status: BackendStatus
    sensor_count: int = 0
    read_count: int = 0
    error_count: int = 0
    last_error: str = ""
    last_success: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "status": self.status.value,
            "sensor_count": self.sensor_count,
            "read_count": self.read_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_success": self.last_success,
        }


@dataclass(slots=True)
class DiscoveryResult:
    profile: HardwareProfile
    readings: list[SensorReading] = field(default_factory=list)
    selected: BackendKind = BackendKind.NONE
    health: list[BackendHealth] = field(default_factory=list)
    elevation_required: bool = False
    notes: list[str] = field(default_factory=list)


def default_factories() -> list[BackendFactory]:
    """The production back-end chain, in priority order."""
    return [
        LibreHardwareMonitorBackend,
        WmiBackend,
        NvidiaBackend,
        WindowsApiBackend,
        PsutilBackend,
    ]


class HardwareRegistry:
    """Owns back-ends and merges their readings with priority."""

    def __init__(
        self,
        factories: list[BackendFactory] | None = None,
        *,
        simulation: HardwareMonitorBackend | None = None,
    ) -> None:
        self._factories = list(factories) if factories is not None else default_factories()
        self._simulation = simulation
        self._backends: list[HardwareMonitorBackend] = []
        self._profile = HardwareProfile()
        self._elevation_required = False
        self._notes: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    @property
    def backends(self) -> list[HardwareMonitorBackend]:
        return list(self._backends)

    @property
    def profile(self) -> HardwareProfile:
        return self._profile

    @property
    def elevation_required(self) -> bool:
        return self._elevation_required

    @property
    def notes(self) -> list[str]:
        return list(self._notes)

    def discover(self, *, progress: Callable[[str], None] | None = None) -> DiscoveryResult:
        """Initialise every viable back-end and take a first reading."""
        self.close()
        self._notes.clear()
        self._elevation_required = False
        for factory in self._factories:
            backend = factory()
            name = backend.kind.value
            if progress:
                progress(name)
            if not backend.is_available():
                continue
            try:
                backend.open()
            except Exception as exc:
                _log.warning("backend %s failed to open: %s", name, exc)
                backend._mark_error(str(exc))
                continue
            if backend.status in {BackendStatus.UNAVAILABLE, BackendStatus.ERROR}:
                _log.info("backend %s unavailable: %s", name, backend.last_error)
                if backend.kind is BackendKind.LIBRE_HARDWARE_MONITOR:
                    self._elevation_required = True
                continue
            self._backends.append(backend)

        readings = self.read()
        selected = self._selected_kind(readings)
        self._merge_profile()
        if not self._backends:
            self._notes.append(
                "No hardware monitoring back-end initialised; only system "
                "information is available."
            )
        return DiscoveryResult(
            profile=self._profile,
            readings=readings,
            selected=selected,
            health=self.health(),
            elevation_required=self._elevation_required,
            notes=list(self._notes),
        )

    def close(self) -> None:
        for backend in self._backends:
            with contextlib.suppress(Exception):
                backend.close()
        self._backends.clear()

    # -- data --------------------------------------------------------------

    def read(self) -> list[SensorReading]:
        """Read every live back-end and de-duplicate by sensor id.

        Higher priority back-ends come first in the list, so on a collision the
        first reading wins — which is exactly the priority order we want.
        """
        merged: dict[str, SensorReading] = {}
        for backend in self._backends:
            try:
                readings = backend.read()
            except Exception as exc:
                _log.warning("backend %s read failed: %s", backend.kind.value, exc)
                continue
            for reading in readings:
                merged.setdefault(reading.sensor_id, reading)
        if self._simulation is not None:
            for reading in self._simulation.read():
                merged.setdefault(reading.sensor_id, reading)
        return list(merged.values())

    def set_simulation(self, backend: HardwareMonitorBackend | None) -> None:
        self._simulation = backend

    # -- diagnostics -------------------------------------------------------

    def selected_backend(self) -> BackendKind:
        # Precedence: an explicit Simulation Mode override wins (the user asked
        # for it, spec §29/§104); then any real sensor back-end; the uptime-only
        # Windows API helper is always the last resort (spec §28).
        if self._simulation is not None:
            return BackendKind.SIMULATION
        for backend in self._backends:
            if backend.status in {BackendStatus.READY, BackendStatus.DEGRADED} and (
                backend.kind is not BackendKind.WINDOWS_API
            ):
                return backend.kind
        for backend in self._backends:
            if backend.status in {BackendStatus.READY, BackendStatus.DEGRADED}:
                return backend.kind
        return BackendKind.NONE

    def health(self) -> list[BackendHealth]:
        report: list[BackendHealth] = []
        for backend in self._backends:
            report.append(
                BackendHealth(
                    kind=backend.kind,
                    status=backend.status,
                    read_count=backend.read_count,
                    error_count=backend.error_count,
                    last_error=backend.last_error,
                    last_success=backend.last_success,
                )
            )
        if self._simulation is not None:
            report.append(
                BackendHealth(
                    kind=BackendKind.SIMULATION,
                    status=self._simulation.status,
                    read_count=self._simulation.read_count,
                )
            )
        return report

    # -- helpers -----------------------------------------------------------

    def _selected_kind(self, readings: list[SensorReading]) -> BackendKind:
        for backend in self._backends:
            if any(reading.backend is backend.kind for reading in readings):
                return backend.kind
        return self.selected_backend()

    def _merge_profile(self) -> None:
        """Combine profile fragments from every back-end, first wins."""
        cpu_model = ""
        gpu_models: list[str] = []
        storage: list[str] = []
        ram_gb = 0.0
        for backend in self._backends:
            fragment = backend.profile()
            cpu_model = cpu_model or fragment.cpu_model
            ram_gb = ram_gb or fragment.ram_total_gb
            for model in fragment.gpu_models:
                if model not in gpu_models:
                    gpu_models.append(model)
            for model in fragment.storage_models:
                if model not in storage:
                    storage.append(model)
        base = self._profile
        self._profile = HardwareProfile(
            cpu_model=cpu_model or base.cpu_model,
            gpu_models=gpu_models or base.gpu_models,
            ram_total_gb=ram_gb or base.ram_total_gb,
            storage_models=storage or base.storage_models,
            os_name=base.os_name,
            os_version=base.os_version,
            os_build=base.os_build,
            architecture=base.architecture,
            hostname=base.hostname,
            machine_fingerprint=base.machine_fingerprint,
        )


__all__ = [
    "BackendFactory",
    "BackendHealth",
    "DiscoveryResult",
    "HardwareRegistry",
    "default_factories",
]
