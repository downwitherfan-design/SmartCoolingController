"""Telemetry snapshot models (spec §8, §16).

A :class:`TelemetrySnapshot` is an immutable, self-describing frame produced by
the telemetry engine at a fixed cadence.  Every subsystem value carries its own
state so the UI can show "unavailable" honestly rather than inventing zeros
(spec §45, §235, §300).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.clock import to_iso, utc_now
from app.core.enums import (
    BackendKind,
    DataProvenance,
    FanResponseState,
    FanState,
    MetricKey,
    MetricState,
    ThermalState,
    ThermalTrend,
)
from app.models.sensors import NormalisedMetric


@dataclass(slots=True)
class CpuTelemetry:
    temperature: float | None = None
    temperature_package: float | None = None
    temperature_core_avg: float | None = None
    temperature_core_max: float | None = None
    usage: float | None = None
    clock_mhz: float | None = None
    power_w: float | None = None
    voltage_v: float | None = None
    state: MetricState = MetricState.UNAVAILABLE
    thermal_state: ThermalState = ThermalState.NORMAL

    @property
    def available(self) -> bool:
        return self.temperature is not None or self.usage is not None


@dataclass(slots=True)
class GpuTelemetry:
    name: str = ""
    temperature: float | None = None
    hotspot_temperature: float | None = None
    usage: float | None = None
    clock_mhz: float | None = None
    power_w: float | None = None
    memory_usage: float | None = None
    memory_total_mb: float | None = None
    state: MetricState = MetricState.UNAVAILABLE
    thermal_state: ThermalState = ThermalState.NORMAL

    @property
    def available(self) -> bool:
        return self.temperature is not None or self.usage is not None


@dataclass(slots=True)
class MemoryTelemetry:
    total_mb: float | None = None
    used_mb: float | None = None
    available_mb: float | None = None
    usage_percent: float | None = None
    state: MetricState = MetricState.UNAVAILABLE


@dataclass(slots=True)
class StorageTelemetry:
    name: str = ""
    temperature: float | None = None
    activity_percent: float | None = None
    state: MetricState = MetricState.UNAVAILABLE


@dataclass(slots=True)
class BatteryTelemetry:
    present: bool = False
    level_percent: float | None = None
    charging: bool | None = None
    plugged: bool | None = None
    power_w: float | None = None
    state: MetricState = MetricState.UNAVAILABLE


@dataclass(slots=True)
class FanTelemetry:
    """Fan + cooling-pad state as reported by the ESP32."""

    target_percent: float | None = None
    pwm_percent: float | None = None
    rpm: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_w: float | None = None
    state: FanState = FanState.UNKNOWN
    response: FanResponseState = FanResponseState.UNKNOWN
    fault: str = "none"
    available: bool = False
    provenance: DataProvenance = DataProvenance.UNAVAILABLE


@dataclass(slots=True)
class CoolingPadTelemetry:
    """Optional sensors physically attached to the pad (INA219 / DS18B20)."""

    temperature: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_w: float | None = None
    available: bool = False


@dataclass(slots=True)
class ThermalAnalysis:
    """Result of the analytics engine for one snapshot (spec §17, §18)."""

    cpu_state: ThermalState = ThermalState.NORMAL
    gpu_state: ThermalState = ThermalState.NORMAL
    cpu_trend: ThermalTrend = ThermalTrend.STABLE
    gpu_trend: ThermalTrend = ThermalTrend.STABLE
    cpu_rate_c_per_min: float | None = None
    gpu_rate_c_per_min: float | None = None
    cpu_average_c: float | None = None
    cpu_min_c: float | None = None
    cpu_max_c: float | None = None
    gpu_average_c: float | None = None
    gpu_min_c: float | None = None
    gpu_max_c: float | None = None
    headroom_c: float | None = None
    weighted_input_c: float | None = None
    recommendation_pwm: float | None = None
    #: All values here are heuristic/derived.
    provenance: DataProvenance = DataProvenance.DERIVED


@dataclass(slots=True)
class TelemetrySnapshot:
    """A complete, immutable telemetry frame."""

    sequence: int
    timestamp: datetime = field(default_factory=utc_now)
    backend: BackendKind = BackendKind.NONE
    validity: MetricState = MetricState.UNAVAILABLE
    #: ``True`` when produced by Simulation Mode (spec §106, §295).
    simulated: bool = False

    cpu: CpuTelemetry = field(default_factory=CpuTelemetry)
    gpu: GpuTelemetry = field(default_factory=GpuTelemetry)
    memory: MemoryTelemetry = field(default_factory=MemoryTelemetry)
    storage: StorageTelemetry = field(default_factory=StorageTelemetry)
    battery: BatteryTelemetry = field(default_factory=BatteryTelemetry)
    fan: FanTelemetry = field(default_factory=FanTelemetry)
    pad: CoolingPadTelemetry = field(default_factory=CoolingPadTelemetry)
    thermal: ThermalAnalysis = field(default_factory=ThermalAnalysis)

    #: Raw normalised metrics, keyed by :class:`MetricKey` (for the advanced view).
    metrics: dict[MetricKey, NormalisedMetric] = field(default_factory=dict)

    def metric_value(self, key: MetricKey) -> float | None:
        metric = self.metrics.get(key)
        return metric.value if metric else None

    def to_row(self) -> dict[str, Any]:
        """Flatten into a row suitable for the telemetry_samples table."""
        return {
            "timestamp": to_iso(self.timestamp),
            "sequence": self.sequence,
            "backend": self.backend.value,
            "validity": self.validity.value,
            "simulated": int(self.simulated),
            "cpu_temperature": self.cpu.temperature,
            "cpu_usage": self.cpu.usage,
            "cpu_clock_mhz": self.cpu.clock_mhz,
            "cpu_power_w": self.cpu.power_w,
            "gpu_temperature": self.gpu.temperature,
            "gpu_hotspot": self.gpu.hotspot_temperature,
            "gpu_usage": self.gpu.usage,
            "gpu_power_w": self.gpu.power_w,
            "ram_usage": self.memory.usage_percent,
            "storage_temperature": self.storage.temperature,
            "battery_level": self.battery.level_percent,
            "fan_target": self.fan.target_percent,
            "fan_pwm": self.fan.pwm_percent,
            "fan_rpm": self.fan.rpm,
            "fan_voltage": self.fan.voltage_v,
            "fan_current": self.fan.current_a,
            "fan_power": self.fan.power_w,
            "cpu_state": self.thermal.cpu_state.value,
            "gpu_state": self.thermal.gpu_state.value,
            "cpu_rate": self.thermal.cpu_rate_c_per_min,
            "gpu_rate": self.thermal.gpu_rate_c_per_min,
            "recommendation_pwm": self.thermal.recommendation_pwm,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": to_iso(self.timestamp),
            "backend": self.backend.value,
            "validity": self.validity.value,
            "simulated": self.simulated,
            "cpu": {
                "temperature": self.cpu.temperature,
                "usage": self.cpu.usage,
                "clock_mhz": self.cpu.clock_mhz,
                "power_w": self.cpu.power_w,
                "thermal_state": self.cpu.thermal_state.value,
            },
            "gpu": {
                "name": self.gpu.name,
                "temperature": self.gpu.temperature,
                "hotspot_temperature": self.gpu.hotspot_temperature,
                "usage": self.gpu.usage,
                "power_w": self.gpu.power_w,
                "thermal_state": self.gpu.thermal_state.value,
            },
            "memory": {"usage_percent": self.memory.usage_percent},
            "fan": {
                "target_percent": self.fan.target_percent,
                "pwm_percent": self.fan.pwm_percent,
                "rpm": self.fan.rpm,
                "voltage_v": self.fan.voltage_v,
                "current_a": self.fan.current_a,
                "power_w": self.fan.power_w,
                "state": self.fan.state.value,
                "available": self.fan.available,
            },
            "thermal": {
                "cpu_state": self.thermal.cpu_state.value,
                "gpu_state": self.thermal.gpu_state.value,
                "cpu_trend": self.thermal.cpu_trend.value,
                "gpu_trend": self.thermal.gpu_trend.value,
                "recommendation_pwm": self.thermal.recommendation_pwm,
            },
        }


__all__ = [
    "BatteryTelemetry",
    "CoolingPadTelemetry",
    "CpuTelemetry",
    "FanTelemetry",
    "GpuTelemetry",
    "MemoryTelemetry",
    "StorageTelemetry",
    "TelemetrySnapshot",
    "ThermalAnalysis",
]
