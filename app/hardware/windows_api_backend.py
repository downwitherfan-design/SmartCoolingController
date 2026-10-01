"""Windows API back-end for system level facts (spec §215, §216).

This back-end supplies reliable, unprivileged facts that other sources miss:
system uptime, OS build and (on Windows) battery/AC state via the Win32 API.
On non-Windows platforms it still reports uptime from ``psutil``/``time`` so the
application has a consistent data shape everywhere.
"""

from __future__ import annotations

import platform
import socket
import sys
import time

from app.core.clock import utc_now
from app.core.enums import (
    BackendKind,
    HardwareCategory,
    SensorType,
)
from app.hardware.base import HardwareMonitorBackend
from app.models.sensors import HardwareProfile, SensorReading


class WindowsApiBackend(HardwareMonitorBackend):
    """Uptime and OS level information."""

    kind = BackendKind.WINDOWS_API

    def __init__(self) -> None:
        super().__init__()
        self._profile = HardwareProfile()

    def is_available(self) -> bool:
        # Uptime is meaningful everywhere; the Windows-specific extras degrade.
        return True

    @staticmethod
    def platform_is_supported() -> bool:
        """Whether this back-end can report Windows-specific facts.

        On non-Windows platforms the back-end still opens and contributes
        uptime, but it must not be advertised as the *selected* Windows source;
        the registry prefers a real sensor back-end when one is present.
        """
        return sys.platform == "win32"

    def open(self) -> None:
        self._profile = self._build_profile()
        self._mark_ready()

    def read(self) -> list[SensorReading]:
        readings: list[SensorReading] = []
        now = utc_now()
        uptime = self._uptime_seconds()
        if uptime is not None:
            readings.append(
                SensorReading(
                    sensor_id="win.uptime",
                    hardware_name="System",
                    hardware_category=HardwareCategory.SYSTEM,
                    sensor_name="System Uptime",
                    sensor_type=SensorType.TIME,
                    value=uptime,
                    unit="s",
                    backend=BackendKind.WINDOWS_API,
                    timestamp=now,
                )
            )
        readings.extend(self._read_battery(now))
        if readings:
            self._record_read()
        return readings

    def profile(self) -> HardwareProfile:
        return self._profile

    @staticmethod
    def _uptime_seconds() -> float | None:
        if sys.platform == "win32":
            try:
                import ctypes

                return float(ctypes.windll.kernel32.GetTickCount64()) / 1000.0
            except Exception:
                return None
        try:
            import psutil

            return float(time.time() - psutil.boot_time())
        except Exception:
            return None

    def _read_battery(self, now: object) -> list[SensorReading]:
        if sys.platform != "win32":
            return []
        try:
            import ctypes

            class _PowerStatus(ctypes.Structure):
                _fields_ = [  # noqa: RUF012 - ctypes struct convention
                    ("ACLineStatus", ctypes.c_ubyte),
                    ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            status = _PowerStatus()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return []
        except Exception:
            return []
        readings: list[SensorReading] = []
        if status.BatteryLifePercent != 255:
            readings.append(
                SensorReading(
                    sensor_id="win.battery.level",
                    hardware_name="Battery",
                    hardware_category=HardwareCategory.BATTERY,
                    sensor_name="Battery Level",
                    sensor_type=SensorType.LEVEL,
                    value=float(status.BatteryLifePercent),
                    unit="%",
                    backend=BackendKind.WINDOWS_API,
                    timestamp=now,
                )
            )
        readings.append(
            SensorReading(
                sensor_id="win.battery.acline",
                hardware_name="Battery",
                hardware_category=HardwareCategory.BATTERY,
                sensor_name="AC Connected",
                sensor_type=SensorType.OTHER,
                value=1.0 if status.ACLineStatus == 1 else 0.0,
                unit="",
                backend=BackendKind.WINDOWS_API,
                timestamp=now,
            )
        )
        return readings

    def _build_profile(self) -> HardwareProfile:
        return HardwareProfile(
            os_name=f"{platform.system()} {platform.release()}",
            os_version=platform.version(),
            os_build=platform.version(),
            architecture=platform.machine(),
            hostname=socket.gethostname(),
        )


__all__ = ["WindowsApiBackend"]
