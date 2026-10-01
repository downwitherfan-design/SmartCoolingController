"""Sensor level models: raw readings, descriptors and normalised metrics.

These types form the contract between the hardware back-ends and everything
above them.  A back-end never speaks in terms of CPU/GPU; it emits
:class:`SensorReading` objects carrying original metadata (spec §10).  The
normalisation layer maps those onto :class:`NormalisedMetric` values keyed by
:class:`~app.core.enums.MetricKey`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.clock import to_iso, utc_now
from app.core.enums import (
    BackendKind,
    DataProvenance,
    HardwareCategory,
    MetricKey,
    MetricState,
    SensorType,
)


@dataclass(slots=True)
class SensorReading:
    """One raw value as reported by a hardware back-end."""

    sensor_id: str
    hardware_name: str
    hardware_category: HardwareCategory
    sensor_name: str
    sensor_type: SensorType
    value: float
    unit: str
    backend: BackendKind
    #: Optional per-sensor min/max as reported by the back-end.
    minimum: float | None = None
    maximum: float | None = None
    #: True when the back-end reports the value as unavailable/invalid.
    unavailable: bool = False
    timestamp: Any = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "hardware_name": self.hardware_name,
            "hardware_category": self.hardware_category.value,
            "sensor_name": self.sensor_name,
            "sensor_type": self.sensor_type.value,
            "value": self.value,
            "unit": self.unit,
            "backend": self.backend.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unavailable": self.unavailable,
            "timestamp": to_iso(self.timestamp),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class SensorDescriptor:
    """Stable identity + metadata for a sensor (spec §54, §115)."""

    sensor_id: str
    hardware_name: str
    hardware_category: HardwareCategory
    sensor_name: str
    sensor_type: SensorType
    unit: str
    backend: BackendKind
    #: Most recent value, or ``None`` when never read.
    current: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    average: float | None = None
    state: MetricState = MetricState.UNAVAILABLE
    favorite: bool = False
    #: Logical metric this sensor is mapped to, if any.
    mapped_metric: MetricKey | None = None

    @property
    def display_name(self) -> str:
        return f"{self.hardware_name} / {self.sensor_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "hardware_name": self.hardware_name,
            "hardware_category": self.hardware_category.value,
            "sensor_name": self.sensor_name,
            "sensor_type": self.sensor_type.value,
            "unit": self.unit,
            "backend": self.backend.value,
            "current": self.current,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "average": self.average,
            "state": self.state.value,
            "favorite": self.favorite,
            "mapped_metric": self.mapped_metric.value if self.mapped_metric else None,
        }


@dataclass(slots=True)
class NormalisedMetric:
    """A logical metric with its trust label and provenance (spec §10, §188)."""

    key: MetricKey
    value: float | None
    unit: str
    state: MetricState
    provenance: DataProvenance
    #: Underlying sensor that produced the value, when measured.
    source_sensor_id: str | None = None
    source_backend: BackendKind = BackendKind.NONE
    source_hardware: str = ""
    source_sensor_name: str = ""
    timestamp: Any = field(default_factory=utc_now)

    @property
    def available(self) -> bool:
        return self.value is not None and self.state in {
            MetricState.VALID,
            MetricState.DERIVED,
            MetricState.SIMULATED,
        }

    @classmethod
    def unavailable(
        cls,
        key: MetricKey,
        unit: str,
        *,
        state: MetricState = MetricState.UNAVAILABLE,
    ) -> NormalisedMetric:
        return cls(
            key=key,
            value=None,
            unit=unit,
            state=state,
            provenance=DataProvenance.UNAVAILABLE,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.value,
            "value": self.value,
            "unit": self.unit,
            "state": self.state.value,
            "provenance": self.provenance.value,
            "source_sensor_id": self.source_sensor_id,
            "source_backend": self.source_backend.value,
            "source_hardware": self.source_hardware,
            "source_sensor_name": self.source_sensor_name,
            "timestamp": to_iso(self.timestamp),
        }


@dataclass(slots=True)
class SensorMapping:
    """A persisted rule mapping a logical metric to a concrete sensor.

    ``priority`` breaks ties when several sensors could represent the same
    metric; higher wins.  A mapping with ``sensor_id == ""`` means "auto".
    """

    metric: MetricKey
    sensor_id: str
    priority: int = 100
    user_defined: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.value,
            "sensor_id": self.sensor_id,
            "priority": self.priority,
            "user_defined": self.user_defined,
        }


@dataclass(slots=True)
class HardwareProfile:
    """Static description of the host machine (spec §122)."""

    cpu_model: str = "Unknown CPU"
    gpu_models: list[str] = field(default_factory=list)
    ram_total_gb: float = 0.0
    storage_models: list[str] = field(default_factory=list)
    os_name: str = ""
    os_version: str = ""
    os_build: str = ""
    architecture: str = ""
    hostname: str = ""
    machine_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_model": self.cpu_model,
            "gpu_models": list(self.gpu_models),
            "ram_total_gb": self.ram_total_gb,
            "storage_models": list(self.storage_models),
            "os_name": self.os_name,
            "os_version": self.os_version,
            "os_build": self.os_build,
            "architecture": self.architecture,
            "hostname": self.hostname,
            "machine_fingerprint": self.machine_fingerprint,
        }


__all__ = [
    "HardwareProfile",
    "NormalisedMetric",
    "SensorDescriptor",
    "SensorMapping",
    "SensorReading",
]
