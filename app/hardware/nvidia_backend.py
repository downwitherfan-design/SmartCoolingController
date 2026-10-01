"""Vendor GPU back-ends (spec §213).

NVIDIA GPUs expose temperature, utilisation, clocks, power draw and memory
through ``nvidia-smi``.  The AMD/Intel paths fall back to whatever the platform
provides (LibreHardwareMonitor or WMI); there is no reliable, dependency-free
API for those vendors, so we do not pretend there is one.

Only metrics that are actually reported by the tool are emitted.  ``nvidia-smi``
is invoked with a fixed, argument-free query string so no user input ever
reaches the command line (spec §50).
"""

from __future__ import annotations

import shutil
import subprocess

from app.core.clock import utc_now
from app.core.enums import (
    BackendKind,
    HardwareCategory,
    SensorType,
)
from app.hardware.base import HardwareMonitorBackend
from app.models.sensors import HardwareProfile, SensorReading

_QUERY_FIELDS = [
    ("name", "name", None),
    ("temperature.gpu", "GPU Core Temperature", SensorType.TEMPERATURE),
    ("utilization.gpu", "GPU Load", SensorType.LOAD),
    ("clocks.current.graphics", "GPU Clock", SensorType.CLOCK),
    ("power.draw", "GPU Power", SensorType.POWER),
    ("memory.used", "GPU Memory Used", SensorType.DATA),
    ("memory.total", "GPU Memory Total", SensorType.DATA),
    ("fan.speed", "GPU Fan", SensorType.FAN),
]


class NvidiaBackend(HardwareMonitorBackend):
    """NVIDIA telemetry via the bundled/system ``nvidia-smi`` executable."""

    kind = BackendKind.NVIDIA

    def __init__(self) -> None:
        super().__init__()
        self._executable: str | None = None
        self._profile = HardwareProfile()

    def is_available(self) -> bool:
        return shutil.which("nvidia-smi") is not None

    def open(self) -> None:
        self._executable = shutil.which("nvidia-smi")
        if self._executable is None:
            self._mark_unavailable("nvidia-smi was not found")
            return
        try:
            readings = self.read()
            if readings:
                self._mark_ready()
            else:
                self._mark_unavailable("nvidia-smi reported no devices")
        except Exception as exc:
            self._mark_unavailable(f"nvidia-smi failed: {exc}")

    def read(self) -> list[SensorReading]:
        if self._executable is None:
            return []
        query = ",".join(field for field, _, _ in _QUERY_FIELDS)
        try:
            completed = subprocess.run(
                [
                    self._executable,
                    f"--query-gpu={query}",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._mark_degraded(f"nvidia-smi execution failed: {exc}")
            return []
        if completed.returncode != 0:
            self._mark_degraded(f"nvidia-smi exited with {completed.returncode}")
            return []
        readings: list[SensorReading] = []
        now = utc_now()
        for line in completed.stdout.strip().splitlines():
            values = [part.strip() for part in line.split(",")]
            if len(values) != len(_QUERY_FIELDS):
                continue
            gpu_name = values[0] or "NVIDIA GPU"
            for index, ((_, label, sensor_type), raw) in enumerate(
                zip(_QUERY_FIELDS, values, strict=False)
            ):
                if sensor_type is None or raw in {"", "N/A", "[Not Supported]"}:
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                readings.append(
                    SensorReading(
                        sensor_id=f"nvidia.{gpu_name}.{index}",
                        hardware_name=gpu_name,
                        hardware_category=HardwareCategory.GPU,
                        sensor_name=label,
                        sensor_type=sensor_type,
                        value=value,
                        unit=_unit(sensor_type),
                        backend=BackendKind.NVIDIA,
                        timestamp=now,
                    )
                )
        if readings:
            self._record_read()
        return readings

    def profile(self) -> HardwareProfile:
        if self._profile.gpu_models:
            return self._profile
        names = {
            reading.hardware_name
            for reading in self.read()
            if reading.hardware_category is HardwareCategory.GPU
        }
        self._profile = HardwareProfile(gpu_models=sorted(names))
        return self._profile


def _unit(sensor_type: SensorType) -> str:
    from app.core.units import canonical_unit

    return canonical_unit(sensor_type)


__all__ = ["NvidiaBackend"]
