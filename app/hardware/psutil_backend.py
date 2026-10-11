"""psutil back-end: portable CPU/GPU/memory/storage/battery telemetry.

psutil is the guaranteed floor of the fallback chain: it works on Windows,
Linux and macOS without elevation, so the application always has *some* real
telemetry.  It provides CPU load/clock, memory, per-disk activity and battery
state.  Temperatures are only emitted when the platform actually exposes them
(``psutil.sensors_temperatures``); on Windows this is normally empty, in which
case the higher-priority LibreHardwareMonitor/WMI back-ends supply them.
"""

from __future__ import annotations

import platform
import socket
import sys
import time
from typing import Any

from app.core.clock import utc_timestamp
from app.core.enums import (
    BackendKind,
    BackendStatus,
    HardwareCategory,
    SensorType,
)
from app.hardware.base import HardwareMonitorBackend
from app.models.sensors import HardwareProfile, SensorReading

try:  # pragma: no cover - import guard
    import psutil
except Exception:
    psutil = None  # type: ignore[assignment]


class PsutilBackend(HardwareMonitorBackend):
    """Cross-platform, unprivileged telemetry back-end."""

    kind = BackendKind.PSUTIL

    #: Mount topology changes very rarely, but ``disk_partitions()`` is a real
    #: syscall walk (measured ~34 ms per call here, 50× the rest of the read).
    #: Cache the list for this long and re-scan only when it expires; per-mount
    #: *usage* is still read fresh every sample, so free-space stays truthful.
    _PARTITIONS_TTL_S = 30.0

    def __init__(self) -> None:
        super().__init__()
        self._profile = HardwareProfile()
        self._cpu_count = 1
        self._temperature_keys: list[str] = []
        # Prime psutil's non-blocking CPU counter so the first read is instant.
        self._last_cpu_times: Any = None
        self._partitions: list[Any] | None = None
        self._partitions_at = 0.0

    def _partitions_cached(self) -> list[Any]:
        """Return the mount list, refreshing it at most once per TTL."""
        now = time.monotonic()
        if self._partitions is None or now - self._partitions_at >= self._PARTITIONS_TTL_S:
            try:
                self._partitions = list(psutil.disk_partitions(all=False))
            except Exception:
                # Keep the previous list rather than flapping to empty on a
                # transient failure; the caller treats a missing list as empty.
                self._partitions = self._partitions or []
            self._partitions_at = now
        return self._partitions

    # -- lifecycle ---------------------------------------------------------

    def is_available(self) -> bool:
        return psutil is not None

    def open(self) -> None:
        if psutil is None:
            self._mark_unavailable("psutil is not installed")
            return
        try:
            self._cpu_count = psutil.cpu_count(logical=True) or 1
            psutil.cpu_percent(interval=None)  # prime the counter
            self._profile = self._build_profile()
            self._discover_temperature_keys()
            self._mark_ready()
        except Exception as exc:
            self._mark_error(f"psutil init failed: {exc}")

    # -- data --------------------------------------------------------------

    def read(self) -> list[SensorReading]:
        if psutil is None or self._status in {
            BackendStatus.UNAVAILABLE,
            BackendStatus.ERROR,
        }:
            return []
        readings: list[SensorReading] = []
        now = utc_timestamp()
        try:
            readings.extend(self._read_cpu(now))
            readings.extend(self._read_memory(now))
            readings.extend(self._read_temperatures(now))
            readings.extend(self._read_storage(now))
            readings.extend(self._read_battery(now))
            self._record_read()
        except Exception as exc:
            self._mark_degraded(f"psutil read failed: {exc}")
        return readings

    def profile(self) -> HardwareProfile:
        return self._profile

    # -- collectors --------------------------------------------------------

    def _read_cpu(self, now: float) -> list[SensorReading]:
        readings: list[SensorReading] = []
        usage = float(psutil.cpu_percent(interval=None))
        readings.append(
            self._reading(
                "psutil.cpu.usage", "CPU", HardwareCategory.CPU,
                "Total CPU Load", SensorType.LOAD, usage, "%", now,
            )
        )
        freq = psutil.cpu_freq()
        if freq and freq.current:
            readings.append(
                self._reading(
                    "psutil.cpu.freq", "CPU", HardwareCategory.CPU,
                    "CPU Clock", SensorType.CLOCK, float(freq.current), "MHz", now,
                )
            )
        return readings

    def _read_memory(self, now: float) -> list[SensorReading]:
        readings: list[SensorReading] = []
        vm = psutil.virtual_memory()
        readings.append(
            self._reading(
                "psutil.mem.usage", "Memory", HardwareCategory.MEMORY,
                "Memory Load", SensorType.LOAD, float(vm.percent), "%", now,
            )
        )
        readings.append(
            self._reading(
                "psutil.mem.used", "Memory", HardwareCategory.MEMORY,
                "Memory Used", SensorType.DATA, vm.used / (1024 * 1024), "MiB", now,
            )
        )
        readings.append(
            self._reading(
                "psutil.mem.available", "Memory", HardwareCategory.MEMORY,
                "Memory Available", SensorType.DATA,
                vm.available / (1024 * 1024), "MiB", now,
            )
        )
        readings.append(
            self._reading(
                "psutil.mem.total", "Memory", HardwareCategory.MEMORY,
                "Memory Total", SensorType.DATA,
                vm.total / (1024 * 1024), "MiB", now,
            )
        )
        return readings

    def _read_temperatures(self, now: float) -> list[SensorReading]:
        readings: list[SensorReading] = []
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is None:
            return readings
        try:
            groups = getter() or {}
        except Exception:
            return readings
        for group_name, entries in groups.items():
            for index, entry in enumerate(entries):
                if entry.current is None:
                    continue
                label = entry.label or f"sensor {index}"
                low = getattr(entry, "low", None)
                high = getattr(entry, "high", None)
                readings.append(
                    self._reading(
                        f"psutil.temp.{group_name}.{index}",
                        str(group_name),
                        HardwareCategory.CPU if "core" in group_name.lower()
                        else HardwareCategory.OTHER,
                        f"{group_name} {label}".strip(),
                        SensorType.TEMPERATURE,
                        float(entry.current),
                        "°C",
                        now,
                        minimum=float(low) if low else None,
                        maximum=float(high) if high else None,
                    )
                )
        return readings

    def _read_storage(self, now: float) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for partition in self._partitions_cached():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except Exception:
                continue
            safe = partition.mountpoint.replace(":", "").replace("\\", "_").replace("/", "_")
            readings.append(
                self._reading(
                    f"psutil.disk.{safe}.usage", partition.device or safe,
                    HardwareCategory.STORAGE, f"{partition.mountpoint} Used Space",
                    SensorType.LOAD, float(usage.percent), "%", now,
                )
            )
        return readings

    def _read_battery(self, now: float) -> list[SensorReading]:
        readings: list[SensorReading] = []
        getter = getattr(psutil, "sensors_battery", None)
        if getter is None:
            return readings
        try:
            battery = getter()
        except Exception:
            battery = None
        if battery is None:
            return readings
        readings.append(
            self._reading(
                "psutil.battery.level", "Battery", HardwareCategory.BATTERY,
                "Battery Level", SensorType.LEVEL, float(battery.percent), "%", now,
            )
        )
        if battery.power_plugged is not None:
            readings.append(
                self._reading(
                    "psutil.battery.plugged", "Battery", HardwareCategory.BATTERY,
                    "AC Connected", SensorType.OTHER,
                    1.0 if battery.power_plugged else 0.0, "", now,
                )
            )
        return readings

    # -- helpers -----------------------------------------------------------

    def _discover_temperature_keys(self) -> None:
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is None:
            self._temperature_keys = []
            return
        try:
            self._temperature_keys = list((getter() or {}).keys())
        except Exception:
            self._temperature_keys = []

    def _build_profile(self) -> HardwareProfile:
        cpu_model = platform.processor() or platform.machine() or "Unknown CPU"
        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                ram_gb = round(vm.total / (1024 ** 3), 2)
            except Exception:
                ram_gb = 0.0
        else:
            ram_gb = 0.0
        storage_models: list[str] = []
        try:
            for partition in self._partitions_cached():
                if partition.device:
                    storage_models.append(partition.device)
        except Exception:
            storage_models = []
        return HardwareProfile(
            cpu_model=cpu_model,
            ram_total_gb=ram_gb,
            storage_models=sorted(set(storage_models)),
            os_name=f"{platform.system()} {platform.release()}",
            os_version=platform.version(),
            architecture=platform.machine(),
            hostname=socket.gethostname(),
            machine_fingerprint=self._fingerprint(),
        )

    @staticmethod
    def _fingerprint() -> str:
        import hashlib

        material = "|".join(
            [
                platform.node(),
                platform.machine(),
                platform.processor(),
                str(sys.maxsize),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _reading(
        sensor_id: str,
        hardware_name: str,
        category: HardwareCategory,
        sensor_name: str,
        sensor_type: SensorType,
        value: float,
        unit: str,
        now: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> SensorReading:
        from app.core.clock import utc_now

        return SensorReading(
            sensor_id=sensor_id,
            hardware_name=hardware_name,
            hardware_category=category,
            sensor_name=sensor_name,
            sensor_type=sensor_type,
            value=value,
            unit=unit,
            backend=BackendKind.PSUTIL,
            minimum=minimum,
            maximum=maximum,
            timestamp=utc_now(),
        )


__all__ = ["PsutilBackend"]
