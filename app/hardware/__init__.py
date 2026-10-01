"""Hardware abstraction layer.

Contains the back-end contract, the concrete Windows/portable back-ends, the
deterministic normaliser and the fallback registry.  Nothing here imports Qt.
"""

from app.hardware.base import HardwareMonitorBackend
from app.hardware.libre_hardware_monitor import (
    LibreHardwareMonitorBackend,
    find_assembly,
)
from app.hardware.normalisation import (
    ALL_METRICS,
    METRIC_RULES,
    MetricRule,
    NormalisationResult,
    SensorNormaliser,
)
from app.hardware.nvidia_backend import NvidiaBackend
from app.hardware.psutil_backend import PsutilBackend
from app.hardware.registry import (
    BackendHealth,
    DiscoveryResult,
    HardwareRegistry,
    default_factories,
)
from app.hardware.windows_api_backend import WindowsApiBackend
from app.hardware.wmi_backend import WmiBackend

__all__ = [
    "ALL_METRICS",
    "METRIC_RULES",
    "BackendHealth",
    "DiscoveryResult",
    "HardwareMonitorBackend",
    "HardwareRegistry",
    "LibreHardwareMonitorBackend",
    "MetricRule",
    "NormalisationResult",
    "NvidiaBackend",
    "PsutilBackend",
    "SensorNormaliser",
    "WindowsApiBackend",
    "WmiBackend",
    "default_factories",
    "find_assembly",
]
