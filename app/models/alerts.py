"""Alert, notification and anomaly models (spec §19, §86, §119)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from app.core.clock import to_iso, utc_now
from app.core.enums import (
    AlertCategory,
    AlertEventType,
    AlertSeverity,
    AnomalyKind,
    DataProvenance,
    HealthStatus,
)


@dataclass(slots=True)
class AlertEvent:
    """A persisted, actionable alert."""

    category: AlertCategory
    severity: AlertSeverity
    title: str
    message: str = ""
    #: Machine-readable key used for de-duplication and cooldown.
    dedup_key: str = ""
    source: str = ""
    value: float | None = None
    threshold: float | None = None
    unit: str = ""
    event_type: AlertEventType | None = None
    acknowledged: bool = False
    dismissed: bool = False
    alert_id: int | None = None
    timestamp: datetime = field(default_factory=utc_now)
    #: Derived alerts are labelled; measured thresholds are not.
    provenance: DataProvenance = DataProvenance.MEASURED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.alert_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "dedup_key": self.dedup_key,
            "source": self.source,
            "value": self.value,
            "threshold": self.threshold,
            "unit": self.unit,
            "event_type": self.event_type.value if self.event_type else None,
            "acknowledged": self.acknowledged,
            "dismissed": self.dismissed,
            "provenance": self.provenance.value,
            "timestamp": to_iso(self.timestamp),
        }


@dataclass(slots=True)
class Anomaly:
    """A heuristic observation, never a diagnosis (spec §19)."""

    kind: AnomalyKind
    summary: str
    detail: str = ""
    confidence: str = "low"
    value: float | None = None
    unit: str = ""
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "summary": self.summary,
            "detail": self.detail,
            "confidence": self.confidence,
            "value": self.value,
            "unit": self.unit,
            "timestamp": to_iso(self.timestamp),
        }


@dataclass(slots=True)
class HealthReport:
    """Independent subsystem health, never collapsed into one score (§88)."""

    cpu: HealthStatus = HealthStatus.UNKNOWN
    gpu: HealthStatus = HealthStatus.UNKNOWN
    fan: HealthStatus = HealthStatus.UNKNOWN
    esp32: HealthStatus = HealthStatus.UNKNOWN
    sensors: HealthStatus = HealthStatus.UNKNOWN
    database: HealthStatus = HealthStatus.UNKNOWN
    application: HealthStatus = HealthStatus.HEALTHY
    detail: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu.value,
            "gpu": self.gpu.value,
            "fan": self.fan.value,
            "esp32": self.esp32.value,
            "sensors": self.sensors.value,
            "database": self.database.value,
            "application": self.application.value,
            "detail": dict(self.detail),
        }

    #: Subsystems shown in the aggregate health badge, in display order.
    SUBSYSTEMS: ClassVar[tuple[str, ...]] = (
        "application",
        "cpu",
        "gpu",
        "fan",
        "esp32",
        "sensors",
        "database",
    )

    #: Severity ranking used only to pick a single badge colour.  The report
    #: itself never collapses subsystems into one score (spec §88); the badge is
    #: a summary and the per-subsystem truth stays available via :meth:`summary`.
    _SEVERITY: ClassVar[dict[HealthStatus, int]] = {
        HealthStatus.HEALTHY: 0,
        HealthStatus.UNKNOWN: 1,
        HealthStatus.WARNING: 2,
        HealthStatus.ERROR: 3,
    }

    def worst(self) -> HealthStatus:
        """The most severe subsystem status, for a single-colour indicator."""
        return max(
            (getattr(self, name) for name in self.SUBSYSTEMS),
            key=lambda status: self._SEVERITY[status],
        )

    def summary(self) -> dict[str, HealthStatus]:
        """Every subsystem that is not healthy, keyed by name."""
        return {
            name: getattr(self, name)
            for name in self.SUBSYSTEMS
            if getattr(self, name) is not HealthStatus.HEALTHY
        }


__all__ = ["AlertEvent", "Anomaly", "HealthReport"]
