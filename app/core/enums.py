"""Canonical enumerations shared across every layer of the application.

Keeping these in one place avoids stringly-typed drift between the hardware
adapters, the analytics engine, the wire protocol and the UI.
"""

from __future__ import annotations

from enum import Enum
from typing import TypeVar

_T = TypeVar("_T", bound="StrEnum")


class StrEnum(str, Enum):
    """A ``str`` enum whose members serialise as their value.

    Python 3.11 provides :class:`enum.StrEnum`; we define our own so the code
    behaves identically on 3.10+ and so ``json.dumps`` works out of the box.
    """

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)

    @classmethod
    def coerce(cls: type[_T], value: object, default: _T) -> _T:
        # ``_T`` is bound to the concrete subclass, so ``Kind.coerce(...)`` is
        # typed as returning ``Kind`` rather than the base ``StrEnum`` — without
        # this every call site would look like it returned the wrong enum.
        if isinstance(value, cls):
            return value
        try:
            return cls(value)  # type: ignore[call-arg]
        except (ValueError, KeyError):
            return default


class BackendKind(StrEnum):
    """Hardware monitoring back-ends, ordered by preference."""

    LIBRE_HARDWARE_MONITOR = "libre_hardware_monitor"
    WMI = "wmi"
    NVIDIA = "nvidia"
    WINDOWS_API = "windows_api"
    PSUTIL = "psutil"
    SIMULATION = "simulation"
    NONE = "none"


class BackendStatus(StrEnum):
    UNKNOWN = "unknown"
    INITIALISING = "initialising"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SensorType(StrEnum):
    TEMPERATURE = "temperature"
    LOAD = "load"
    POWER = "power"
    VOLTAGE = "voltage"
    CURRENT = "current"
    CLOCK = "clock"
    FAN = "fan"
    DATA = "data"
    LEVEL = "level"
    THROUGHPUT = "throughput"
    TIME = "time"
    OTHER = "other"


class HardwareCategory(StrEnum):
    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    STORAGE = "storage"
    MOTHERBOARD = "motherboard"
    BATTERY = "battery"
    NETWORK = "network"
    COOLER = "cooler"
    SYSTEM = "system"
    OTHER = "other"


class MetricState(StrEnum):
    """Validity of a single normalised metric."""

    VALID = "valid"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    ERROR = "error"
    SIMULATED = "simulated"
    DERIVED = "derived"


class DataProvenance(StrEnum):
    """Trust label shown in the UI (spec §188)."""

    MEASURED = "measured"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    SIMULATED = "simulated"
    UNAVAILABLE = "unavailable"


class MetricKey(StrEnum):
    """Logical, hardware-agnostic metric identifiers (spec §10)."""

    CPU_TEMPERATURE = "cpu_temperature"
    CPU_TEMPERATURE_PACKAGE = "cpu_temperature_package"
    CPU_TEMPERATURE_CORE_AVG = "cpu_temperature_core_avg"
    CPU_TEMPERATURE_CORE_MAX = "cpu_temperature_core_max"
    CPU_USAGE = "cpu_usage"
    CPU_CLOCK = "cpu_clock"
    CPU_POWER = "cpu_power"
    CPU_VOLTAGE = "cpu_voltage"

    GPU_TEMPERATURE = "gpu_temperature"
    GPU_HOTSPOT_TEMPERATURE = "gpu_hotspot_temperature"
    GPU_USAGE = "gpu_usage"
    GPU_CLOCK = "gpu_clock"
    GPU_POWER = "gpu_power"
    GPU_MEMORY_USAGE = "gpu_memory_usage"
    GPU_MEMORY_TOTAL = "gpu_memory_total"

    RAM_USAGE = "ram_usage"
    RAM_USED = "ram_used"
    RAM_AVAILABLE = "ram_available"
    RAM_TOTAL = "ram_total"

    STORAGE_TEMPERATURE = "storage_temperature"
    STORAGE_ACTIVITY = "storage_activity"

    BATTERY_LEVEL = "battery_level"
    BATTERY_POWER = "battery_power"
    BATTERY_VOLTAGE = "battery_voltage"
    BATTERY_STATE = "battery_state"

    FAN_TARGET = "fan_target"
    FAN_PWM = "fan_pwm"
    FAN_RPM = "fan_rpm"
    FAN_VOLTAGE = "fan_voltage"
    FAN_CURRENT = "fan_current"
    FAN_POWER = "fan_power"

    PAD_TEMPERATURE = "pad_temperature"
    PAD_CURRENT = "pad_current"
    PAD_VOLTAGE = "pad_voltage"
    PAD_POWER = "pad_power"


class ThermalState(StrEnum):
    COOL = "cool"
    NORMAL = "normal"
    WARM = "warm"
    HIGH = "high"
    CRITICAL = "critical"


class ThermalTrend(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"


class FanState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BOOSTING = "boosting"
    LIMITED = "limited"
    STALLED = "stalled"
    FAULT = "fault"
    UNKNOWN = "unknown"


class FanResponseState(StrEnum):
    NORMAL = "normal"
    LOWER_THAN_EXPECTED = "lower_than_expected"
    NO_RESPONSE = "no_response"
    UNKNOWN = "unknown"


class FanControlSource(StrEnum):
    """Which thermal input drives the fan curve."""

    CPU = "cpu"
    GPU = "gpu"
    MAX = "max"
    WEIGHTED = "weighted"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PAIRING = "pairing"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAUTHORIZED = "unauthorized"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    ERROR = "error"


class MessageType(StrEnum):
    """ESP32 wire protocol message types (spec §44)."""

    HELLO = "HELLO"
    PAIR = "PAIR"
    PAIR_ACK = "PAIR_ACK"
    TELEMETRY = "TELEMETRY"
    STATUS = "STATUS"
    COMMAND = "COMMAND"
    CONFIG = "CONFIG"
    ALERT = "ALERT"
    HEARTBEAT = "HEARTBEAT"
    ERROR = "ERROR"
    GOODBYE = "GOODBYE"
    DISCOVER = "DISCOVER"
    DISCOVER_RESPONSE = "DISCOVER_RESPONSE"


class ControllerMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"
    SAFE = "safe"
    CALIBRATION = "calibration"


class FaultState(StrEnum):
    NONE = "none"
    OVERCURRENT = "overcurrent"
    OVERVOLTAGE = "overvoltage"
    STALL = "stall"
    OVERTEMP = "overtemp"
    TACH_LOSS = "tach_loss"
    WATCHDOG = "watchdog"
    UNKNOWN = "unknown"


class CoolingProfileKind(StrEnum):
    SILENT = "silent"
    BALANCED = "balanced"
    PERFORMANCE = "performance"
    TURBO = "turbo"
    CUSTOM = "custom"


class AlertSeverity(StrEnum):
    INFORMATION = "information"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertCategory(StrEnum):
    THERMAL = "thermal"
    FAN = "fan"
    POWER = "power"
    CONNECTION = "connection"
    SENSOR = "sensor"
    SYSTEM = "system"
    DATABASE = "database"


class AnomalyKind(StrEnum):
    RAPID_THERMAL_RISE = "rapid_thermal_rise"
    UNEXPECTED_RPM = "unexpected_rpm"
    UNEXPECTED_POWER = "unexpected_power"
    SENSOR_DISAPPEARED = "sensor_disappeared"
    STALE_DATA = "stale_data"
    ESP32_DISCONNECT = "esp32_disconnect"
    INVALID_NETWORK_DATA = "invalid_network_data"
    UNEXPECTED_VOLTAGE = "unexpected_voltage"


class SessionState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


class ThemeMode(StrEnum):
    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"


class Language(StrEnum):
    ENGLISH = "en"
    PERSIAN = "fa"


class DataMode(StrEnum):
    STANDARD = "standard"
    PORTABLE = "portable"


class PerformanceMode(StrEnum):
    NORMAL = "normal"
    LIGHTWEIGHT = "lightweight"


class AnimationLevel(StrEnum):
    OFF = "off"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class PageId(StrEnum):
    DASHBOARD = "dashboard"
    TELEMETRY = "telemetry"
    FAN_CONTROL = "fan_control"
    SYSTEM_DIAGRAM = "system_diagram"
    HISTORY = "history"
    SESSIONS = "sessions"
    ESP32 = "esp32"
    SENSORS = "sensors"
    ALERTS = "alerts"
    DIAGNOSTICS = "diagnostics"
    SETTINGS = "settings"
    ABOUT = "about"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"


class AlertEventType(StrEnum):
    """Marker events rendered on the thermal timeline (spec §34)."""

    THERMAL_WARNING = "thermal_warning"
    CRITICAL_TEMPERATURE = "critical_temperature"
    PROFILE_CHANGE = "profile_change"
    FAN_BOOST = "fan_boost"
    FAN_ANOMALY = "fan_anomaly"
    ESP32_DISCONNECT = "esp32_disconnect"
    ESP32_RECONNECT = "esp32_reconnect"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SENSOR_FAILURE = "sensor_failure"


class ChartRange(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H6 = "6h"
    H24 = "24h"
    D7 = "7d"
    CUSTOM = "custom"


class CalibrationStep(StrEnum):
    IDLE = "idle"
    DETECT_DEVICE = "detect_device"
    VERIFY_COMMS = "verify_comms"
    TEST_LOW_PWM = "test_low_pwm"
    TEST_MED_PWM = "test_med_pwm"
    TEST_HIGH_PWM = "test_high_pwm"
    BUILD_CURVE = "build_curve"
    SAVE = "save"
    DONE = "done"
    CANCELLED = "cancelled"


__all__ = [name for name in dir() if name.endswith(("Enum",)) or name in {
    "BackendKind", "BackendStatus", "SensorType", "HardwareCategory",
    "MetricState", "DataProvenance", "MetricKey", "ThermalState",
    "ThermalTrend", "FanState", "FanResponseState", "FanControlSource",
    "ConnectionState", "MessageType", "ControllerMode", "FaultState",
    "CoolingProfileKind", "AlertSeverity", "AlertCategory", "AnomalyKind",
    "SessionState", "HealthStatus", "ThemeMode", "Language", "DataMode",
    "PerformanceMode", "AnimationLevel", "PageId", "ExportFormat",
    "AlertEventType", "ChartRange", "CalibrationStep", "StrEnum",
}]
