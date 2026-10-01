"""LibreHardwareMonitor back-end via pythonnet (spec §13, §183).

This is the preferred Windows back-end because it exposes CPU package/core
temperatures, GPU core/hotspot temperatures, fan speeds, power and voltages
that psutil and plain WMI cannot reach without vendor SDKs.

Design notes
------------
* The managed assembly (``LibreHardwareMonitorLib.dll``) is *bundled* with the
  application; the user never installs LibreHardwareMonitor separately.
* Everything is accessed through ``clr``/reflection at runtime, so this module
  imports cleanly on machines without pythonnet and simply reports
  ``is_available() == False``.
* Any failure degrades gracefully; the discovery service falls back to the next
  back-end.  No value is ever invented.

The library requires administrator privileges for some sensors.  When it cannot
be initialised without elevation the back-end reports ``UNAVAILABLE`` with a
clear reason so the UI can offer an optional elevated restart (spec §61, §113).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from app.core.clock import utc_now
from app.core.enums import (
    BackendKind,
    HardwareCategory,
    SensorType,
)
from app.hardware.base import HardwareMonitorBackend
from app.models.sensors import HardwareProfile, SensorReading

try:  # pragma: no cover - platform guard
    import clr  # type: ignore[import-not-found]  # provided by pythonnet
except Exception:
    clr = None  # type: ignore[assignment]


_ASSEMBLY_CANDIDATES = (
    "LibreHardwareMonitorLib.dll",
    "lib/LibreHardwareMonitorLib.dll",
    "hardware/LibreHardwareMonitorLib.dll",
    "libre_hardware_monitor/LibreHardwareMonitorLib.dll",
)

_HARDWARE_MAP: dict[str, HardwareCategory] = {
    "cpu": HardwareCategory.CPU,
    "gpu": HardwareCategory.GPU,
    "memory": HardwareCategory.MEMORY,
    "motherboard": HardwareCategory.MOTHERBOARD,
    "storage": HardwareCategory.STORAGE,
    "battery": HardwareCategory.BATTERY,
    "network": HardwareCategory.NETWORK,
    "cooler": HardwareCategory.COOLER,
    "superio": HardwareCategory.MOTHERBOARD,
    "pcmeter": HardwareCategory.SYSTEM,
}

_SENSOR_MAP: dict[str, SensorType] = {
    "temperature": SensorType.TEMPERATURE,
    "load": SensorType.LOAD,
    "power": SensorType.POWER,
    "voltage": SensorType.VOLTAGE,
    "current": SensorType.CURRENT,
    "clock": SensorType.CLOCK,
    "fan": SensorType.FAN,
    "data": SensorType.DATA,
    "level": SensorType.LEVEL,
    "throughput": SensorType.THROUGHPUT,
    "time": SensorType.TIME,
    "smalldata": SensorType.DATA,
    "factor": SensorType.OTHER,
}


def find_assembly() -> Path | None:
    """Locate the bundled LibreHardwareMonitorLib assembly."""
    override = os.environ.get("SMARTCOOLING_LHM_PATH")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
    roots = [Path(sys.executable).resolve().parent, Path(__file__).resolve().parents[2]]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, Path(meipass))
    for root in roots:
        for relative in _ASSEMBLY_CANDIDATES:
            candidate = root / relative
            if candidate.is_file():
                return candidate
    return None


class LibreHardwareMonitorBackend(HardwareMonitorBackend):
    """pythonnet bridge to LibreHardwareMonitorLib."""

    kind = BackendKind.LIBRE_HARDWARE_MONITOR

    def __init__(self, assembly: Path | None = None) -> None:
        super().__init__()
        self._assembly = assembly or find_assembly()
        self._computer: Any = None
        self._profile = HardwareProfile()

    # -- lifecycle ---------------------------------------------------------

    def is_available(self) -> bool:
        if clr is None or sys.platform != "win32":
            return False
        return self._assembly is not None and self._assembly.is_file()

    def open(self) -> None:
        if clr is None:
            self._mark_unavailable("pythonnet is not installed")
            return
        if sys.platform != "win32":
            self._mark_unavailable("LibreHardwareMonitor is Windows-only")
            return
        if self._assembly is None:
            self._mark_unavailable("LibreHardwareMonitorLib.dll was not bundled")
            return
        try:
            clr.AddReference(str(self._assembly))
            from LibreHardwareMonitor import Hardware  # type: ignore[import-not-found]

            self._hardware_namespace = Hardware
            computer = Hardware.Computer()
            # Enable everything we can; the library ignores unsupported groups.
            for attribute in (
                "IsCpuEnabled",
                "IsGpuEnabled",
                "IsMemoryEnabled",
                "IsMotherboardEnabled",
                "IsStorageEnabled",
                "IsBatteryEnabled",
                "IsNetworkEnabled",
                "IsControllerEnabled",
            ):
                try:
                    setattr(computer, attribute, True)
                except Exception:
                    continue
            computer.Open()
            self._computer = computer
            self._profile = self._build_profile()
            self._mark_ready()
        except Exception as exc:
            self._mark_unavailable(f"LibreHardwareMonitor init failed: {exc}")

    def close(self) -> None:
        if self._computer is not None:
            with contextlib.suppress(Exception):
                self._computer.Close()
            self._computer = None

    # -- data --------------------------------------------------------------

    def read(self) -> list[SensorReading]:
        if self._computer is None:
            return []
        readings: list[SensorReading] = []
        now = utc_now()
        try:
            for hardware in self._computer.Hardware:
                hardware.Update()
                category = _HARDWARE_MAP.get(
                    str(getattr(hardware, "HardwareType", "")).lower(),
                    HardwareCategory.OTHER,
                )
                hardware_name = str(getattr(hardware, "Name", "Hardware"))
                readings.extend(self._read_hardware(hardware, hardware_name, category, now))
                # Sub-hardware (e.g. CPU cores, GPU sub-devices).
                for sub in getattr(hardware, "SubHardware", []) or []:
                    try:
                        sub.Update()
                    except Exception:
                        continue
                    sub_name = f"{hardware_name} / {getattr(sub, 'Name', 'Core')}"
                    readings.extend(self._read_hardware(sub, sub_name, category, now))
            self._record_read()
        except Exception as exc:
            self._mark_degraded(f"LibreHardwareMonitor read failed: {exc}")
        return readings

    def _read_hardware(
        self,
        hardware: Any,
        hardware_name: str,
        category: HardwareCategory,
        now: Any,
    ) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for sensor in getattr(hardware, "Sensors", []) or []:
            try:
                sensor_type = _SENSOR_MAP.get(
                    str(getattr(sensor, "SensorType", "")).lower(), SensorType.OTHER
                )
                value = getattr(sensor, "Value", None)
                if value is None:
                    continue
                sensor_name = str(getattr(sensor, "Name", "Sensor"))
                sensor_id = f"lhm.{hardware_name}.{sensor_name}".replace(" ", "_")
                readings.append(
                    SensorReading(
                        sensor_id=sensor_id,
                        hardware_name=hardware_name,
                        hardware_category=category,
                        sensor_name=sensor_name,
                        sensor_type=sensor_type,
                        value=float(value),
                        unit=_unit_for(sensor_type),
                        backend=BackendKind.LIBRE_HARDWARE_MONITOR,
                        minimum=_safe_float(getattr(sensor, "Min", None)),
                        maximum=_safe_float(getattr(sensor, "Max", None)),
                        unavailable=False,
                        timestamp=now,
                        metadata={
                            "identifier": str(getattr(sensor, "Identifier", "")),
                            "index": int(getattr(sensor, "Index", 0) or 0),
                        },
                    )
                )
            except Exception:
                continue
        return readings

    def profile(self) -> HardwareProfile:
        return self._profile

    def _build_profile(self) -> HardwareProfile:
        cpu_model = "Unknown CPU"
        gpu_models: list[str] = []
        storage_models: list[str] = []
        try:
            for hardware in self._computer.Hardware:
                category = _HARDWARE_MAP.get(
                    str(getattr(hardware, "HardwareType", "")).lower(),
                    HardwareCategory.OTHER,
                )
                name = str(getattr(hardware, "Name", ""))
                if category is HardwareCategory.CPU:
                    cpu_model = name
                elif category is HardwareCategory.GPU:
                    gpu_models.append(name)
                elif category is HardwareCategory.STORAGE:
                    storage_models.append(name)
        except Exception:
            pass
        import platform
        import socket

        return HardwareProfile(
            cpu_model=cpu_model,
            gpu_models=gpu_models,
            storage_models=storage_models,
            os_name=f"{platform.system()} {platform.release()}",
            os_version=platform.version(),
            architecture=platform.machine(),
            hostname=socket.gethostname(),
        )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unit_for(sensor_type: SensorType) -> str:
    from app.core.units import canonical_unit

    return canonical_unit(sensor_type)


__all__ = ["LibreHardwareMonitorBackend", "find_assembly"]
