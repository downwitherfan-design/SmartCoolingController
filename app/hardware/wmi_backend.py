"""WMI back-end (Windows only).

WMI is the first fallback when LibreHardwareMonitor is unavailable.  It exposes
thermal-zone temperatures, CPU load/clock, memory and battery information
without a native helper.

Two access strategies are attempted, in order:

1. the pure-Python ``wmi`` package (thin wrapper over ``pywin32``), and
2. ``win32com.client`` directly.

Both are imported lazily so this module is importable on any platform.  On
non-Windows hosts :meth:`is_available` returns ``False``.
"""

from __future__ import annotations

import sys
from typing import Any

from app.core.clock import utc_now
from app.core.enums import (
    BackendKind,
    HardwareCategory,
    SensorType,
)
from app.hardware.base import HardwareMonitorBackend
from app.models.sensors import HardwareProfile, SensorReading


class WmiBackend(HardwareMonitorBackend):
    """Windows Management Instrumentation telemetry."""

    kind = BackendKind.WMI

    def __init__(self) -> None:
        super().__init__()
        self._wmi_module: Any = None
        self._connection: Any = None
        self._profile = HardwareProfile()

    # -- lifecycle ---------------------------------------------------------

    def is_available(self) -> bool:
        return sys.platform == "win32"

    def open(self) -> None:
        if sys.platform != "win32":
            self._mark_unavailable("WMI is Windows-only")
            return
        try:
            import wmi  # type: ignore[import-not-found]

            self._wmi_module = wmi
            self._connection = wmi.WMI()
            self._profile = self._build_profile()
            self._mark_ready()
            return
        except Exception:
            self._wmi_module = None
        try:
            import win32com.client  # type: ignore[import-not-found]

            self._connection = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            self._connection = self._connection.ConnectServer(".", "root\\cimv2")
            self._wmi_module = "win32com"
            self._profile = self._build_profile()
            self._mark_ready()
        except Exception as exc:
            self._mark_unavailable(f"WMI unavailable: {exc}")

    # -- data --------------------------------------------------------------

    def read(self) -> list[SensorReading]:
        if self._connection is None:
            return []
        readings: list[SensorReading] = []
        now = utc_now()
        readings.extend(self._read_thermal_zones(now))
        readings.extend(self._read_processor(now))
        readings.extend(self._read_memory(now))
        readings.extend(self._read_battery(now))
        if readings:
            self._record_read()
        return readings

    def profile(self) -> HardwareProfile:
        return self._profile

    # -- collectors --------------------------------------------------------

    def _query(self, wql: str, namespace: str = "root\\cimv2") -> list[Any]:
        try:
            if self._wmi_module == "win32com":
                return list(self._connection.ExecQuery(wql))
            return list(self._connection.query(wql))
        except Exception:
            return []

    def _read_thermal_zones(self, now: Any) -> list[SensorReading]:
        readings: list[SensorReading] = []
        try:
            if self._wmi_module == "win32com":
                locator = self._connection
                zones = list(locator.ExecQuery("SELECT * FROM MSAcpi_ThermalZoneTemperature"))
            else:
                zones = list(self._wmi_module.WMI(namespace="root\\wmi").MSAcpi_ThermalZoneTemperature())
        except Exception:
            return readings
        for index, zone in enumerate(zones):
            try:
                tenths_kelvin = float(zone.CurrentTemperature)
                celsius = tenths_kelvin / 10.0 - 273.15
            except (TypeError, ValueError):
                continue
            readings.append(
                SensorReading(
                    sensor_id=f"wmi.thermalzone.{index}",
                    hardware_name="Motherboard",
                    hardware_category=HardwareCategory.MOTHERBOARD,
                    sensor_name=f"Thermal Zone {index}",
                    sensor_type=SensorType.TEMPERATURE,
                    value=celsius,
                    unit="°C",
                    backend=BackendKind.WMI,
                    timestamp=now,
                )
            )
        return readings

    def _read_processor(self, now: Any) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for cpu in self._query("SELECT * FROM Win32_Processor"):
            try:
                load = float(cpu.LoadPercentage)
            except (TypeError, ValueError):
                load = None
            if load is not None:
                readings.append(
                    SensorReading(
                        sensor_id="wmi.cpu.load",
                        hardware_name=str(getattr(cpu, "Name", "CPU")),
                        hardware_category=HardwareCategory.CPU,
                        sensor_name="CPU Load",
                        sensor_type=SensorType.LOAD,
                        value=load,
                        unit="%",
                        backend=BackendKind.WMI,
                        timestamp=now,
                    )
                )
            try:
                clock = float(cpu.CurrentClockSpeed)
            except (TypeError, ValueError):
                clock = None
            if clock:
                readings.append(
                    SensorReading(
                        sensor_id="wmi.cpu.clock",
                        hardware_name=str(getattr(cpu, "Name", "CPU")),
                        hardware_category=HardwareCategory.CPU,
                        sensor_name="CPU Clock",
                        sensor_type=SensorType.CLOCK,
                        value=clock,
                        unit="MHz",
                        backend=BackendKind.WMI,
                        timestamp=now,
                    )
                )
        return readings

    def _read_memory(self, now: Any) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for os_obj in self._query("SELECT * FROM Win32_OperatingSystem"):
            try:
                total_kb = float(os_obj.TotalVisibleMemorySize)
                free_kb = float(os_obj.FreePhysicalMemory)
            except (TypeError, ValueError):
                continue
            if total_kb <= 0:
                continue
            used_kb = total_kb - free_kb
            readings.append(
                SensorReading(
                    sensor_id="wmi.mem.usage",
                    hardware_name="Memory",
                    hardware_category=HardwareCategory.MEMORY,
                    sensor_name="Memory Load",
                    sensor_type=SensorType.LOAD,
                    value=(used_kb / total_kb) * 100.0,
                    unit="%",
                    backend=BackendKind.WMI,
                    timestamp=now,
                )
            )
        return readings

    def _read_battery(self, now: Any) -> list[SensorReading]:
        readings: list[SensorReading] = []
        for battery in self._query("SELECT * FROM Win32_Battery"):
            try:
                level = float(battery.EstimatedChargeRemaining)
            except (TypeError, ValueError):
                continue
            readings.append(
                SensorReading(
                    sensor_id="wmi.battery.level",
                    hardware_name="Battery",
                    hardware_category=HardwareCategory.BATTERY,
                    sensor_name="Battery Level",
                    sensor_type=SensorType.LEVEL,
                    value=level,
                    unit="%",
                    backend=BackendKind.WMI,
                    timestamp=now,
                )
            )
        return readings

    def _build_profile(self) -> HardwareProfile:
        import platform
        import socket

        cpu_model = platform.processor() or "Unknown CPU"
        ram_gb = 0.0
        for cpu in self._query("SELECT * FROM Win32_Processor"):
            name = getattr(cpu, "Name", None)
            if name:
                cpu_model = str(name).strip()
        for os_obj in self._query("SELECT * FROM Win32_OperatingSystem"):
            try:
                ram_gb = round(float(os_obj.TotalVisibleMemorySize) / (1024 ** 2), 2)
            except (TypeError, ValueError):
                ram_gb = 0.0
        return HardwareProfile(
            cpu_model=cpu_model,
            ram_total_gb=ram_gb,
            os_name=f"{platform.system()} {platform.release()}",
            os_version=platform.version(),
            architecture=platform.machine(),
            hostname=socket.gethostname(),
        )


__all__ = ["WmiBackend"]
