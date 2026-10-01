"""Typed domain models for SmartCooling Controller.

All models are plain dataclasses (no Qt, no I/O) so they can be constructed
freely in tests and serialised deterministically.
"""

from app.models.alerts import AlertEvent, Anomaly, HealthReport
from app.models.cooling import (
    CoolingProfile,
    FanCalibration,
    FanCalibrationPoint,
    FanCurve,
    FanCurvePoint,
)
from app.models.devices import Esp32Device, Esp32Status
from app.models.sensors import (
    HardwareProfile,
    NormalisedMetric,
    SensorDescriptor,
    SensorMapping,
    SensorReading,
)
from app.models.sessions import Baseline, CoolingSession, SessionComparison
from app.models.telemetry import (
    BatteryTelemetry,
    CoolingPadTelemetry,
    CpuTelemetry,
    FanTelemetry,
    GpuTelemetry,
    MemoryTelemetry,
    StorageTelemetry,
    TelemetrySnapshot,
    ThermalAnalysis,
)

__all__ = [
    "AlertEvent",
    "Anomaly",
    "Baseline",
    "BatteryTelemetry",
    "CoolingPadTelemetry",
    "CoolingProfile",
    "CoolingSession",
    "CpuTelemetry",
    "Esp32Device",
    "Esp32Status",
    "FanCalibration",
    "FanCalibrationPoint",
    "FanCurve",
    "FanCurvePoint",
    "FanTelemetry",
    "GpuTelemetry",
    "HardwareProfile",
    "HealthReport",
    "MemoryTelemetry",
    "NormalisedMetric",
    "SensorDescriptor",
    "SensorMapping",
    "SensorReading",
    "SessionComparison",
    "StorageTelemetry",
    "TelemetrySnapshot",
    "ThermalAnalysis",
]
