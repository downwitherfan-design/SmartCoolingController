"""Alerting and notification service (spec §19, §86, §87, §119, §120).

Thresholds are evaluated against every snapshot, but an alert is only *raised*
when the condition becomes true and only *cleared* when it becomes false.  A
cooldown keyed on ``dedup_key`` prevents a flapping sensor from producing a
notification storm (spec §87).

The service never fabricates certainty: every alert records the value and the
threshold that produced it, so the UI can show exactly why it fired.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.clock import utc_now
from app.core.enums import (
    AlertCategory,
    AlertEventType,
    AlertSeverity,
    DataProvenance,
)
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.models.alerts import AlertEvent
from app.models.telemetry import TelemetrySnapshot
from app.persistence.repositories import AlertRepository

_log = get_logger(__name__)

TOPIC_ALERT = "alerts.raised"
TOPIC_ALERT_CLEARED = "alerts.cleared"

#: Emit a notification at or above this severity.
NOTIFY_SEVERITY = AlertSeverity.WARNING


@dataclass(slots=True)
class ThresholdRule:
    key: str
    category: AlertCategory
    severity: AlertSeverity
    title: str
    threshold: float
    unit: str
    event_type: AlertEventType
    message: str = ""
    #: ``True`` for "higher is worse", ``False`` for "lower is worse".
    higher_is_worse: bool = True


def default_rules(
    *,
    cpu_warning: float = 85.0,
    cpu_critical: float = 95.0,
    gpu_warning: float = 85.0,
    gpu_critical: float = 95.0,
    storage_warning: float = 70.0,
) -> list[ThresholdRule]:
    return [
        ThresholdRule(
            "cpu.warning", AlertCategory.THERMAL, AlertSeverity.WARNING,
            "CPU temperature is high", cpu_warning, "°C", AlertEventType.THERMAL_WARNING,
            "CPU has been running hot; consider raising the fan profile.",
        ),
        ThresholdRule(
            "cpu.critical", AlertCategory.THERMAL, AlertSeverity.CRITICAL,
            "CPU temperature is critical", cpu_critical, "°C",
            AlertEventType.CRITICAL_TEMPERATURE,
            "CPU is at a critical temperature. Cooling has been forced to maximum.",
        ),
        ThresholdRule(
            "gpu.warning", AlertCategory.THERMAL, AlertSeverity.WARNING,
            "GPU temperature is high", gpu_warning, "°C", AlertEventType.THERMAL_WARNING,
            "GPU has been running hot; consider raising the fan profile.",
        ),
        ThresholdRule(
            "gpu.critical", AlertCategory.THERMAL, AlertSeverity.CRITICAL,
            "GPU temperature is critical", gpu_critical, "°C",
            AlertEventType.CRITICAL_TEMPERATURE,
            "GPU is at a critical temperature. Cooling has been forced to maximum.",
        ),
        ThresholdRule(
            "storage.hot", AlertCategory.THERMAL, AlertSeverity.WARNING,
            "Storage temperature is high", storage_warning, "°C", AlertEventType.THERMAL_WARNING,
            "An NVMe drive is running warm.",
        ),
    ]


class AlertService:
    """Evaluates threshold rules and records alerts."""

    def __init__(
        self,
        repository: AlertRepository,
        bus: EventBus,
        *,
        rules: list[ThresholdRule] | None = None,
        cooldown_s: float = 120.0,
        notifications_enabled: bool = True,
    ) -> None:
        self._repo = repository
        self._bus = bus
        self._rules = rules or default_rules()
        self._cooldown_s = cooldown_s
        self._notifications = notifications_enabled
        self._active: dict[str, AlertEvent] = {}

    # -- configuration -----------------------------------------------------

    def set_rules(self, rules: list[ThresholdRule]) -> None:
        self._rules = rules

    def set_cooldown(self, seconds: float) -> None:
        self._cooldown_s = max(0.0, seconds)

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._notifications = enabled

    # -- queries -----------------------------------------------------------

    def recent(self, limit: int = 200, *, include_dismissed: bool = False) -> list[AlertEvent]:
        return self._repo.list_recent(limit, include_dismissed=include_dismissed)

    @property
    def active(self) -> list[AlertEvent]:
        return list(self._active.values())

    def acknowledge(self, alert_id: int) -> None:
        self._repo.mark(alert_id, acknowledged=True)

    def dismiss(self, alert_id: int) -> None:
        self._repo.mark(alert_id, dismissed=True)
        self._active = {k: v for k, v in self._active.items() if v.alert_id != alert_id}

    def clear_history(self) -> None:
        self._repo.clear()

    # -- evaluation --------------------------------------------------------

    def evaluate(self, snapshot: TelemetrySnapshot) -> list[AlertEvent]:
        """Evaluate every rule and raise or clear alerts as needed."""
        raised: list[AlertEvent] = []
        for rule in self._rules:
            value = self._value_for(rule.key, snapshot)
            if value is None:
                continue
            breached = value >= rule.threshold if rule.higher_is_worse else value <= rule.threshold
            if breached:
                alert = self._raise(rule, value, snapshot)
                if alert is not None:
                    raised.append(alert)
            else:
                self._clear(rule)
        return raised

    def raise_event(
        self,
        *,
        category: AlertCategory,
        severity: AlertSeverity,
        title: str,
        message: str = "",
        event_type: AlertEventType,
        dedup_key: str = "",
        value: float | None = None,
        threshold: float | None = None,
        unit: str = "",
        source: str = "application",
    ) -> AlertEvent:
        """Record a one-off event alert (profile change, connect, etc.)."""
        alert = AlertEvent(
            category=category,
            severity=severity,
            title=title,
            message=message,
            dedup_key=dedup_key,
            source=source,
            value=value,
            threshold=threshold,
            unit=unit,
            event_type=event_type,
        )
        self._repo.upsert(alert)
        self._bus.publish(TOPIC_ALERT, alert)
        self._notify(alert)
        return alert

    # -- internals ---------------------------------------------------------

    def _raise(
        self, rule: ThresholdRule, value: float, snapshot: TelemetrySnapshot
    ) -> AlertEvent | None:
        if rule.key in self._active:
            return None  # already active
        last = self._repo.last_raised(rule.key)
        now = utc_now()
        if last is not None and (now.timestamp() - last) < self._cooldown_s:
            return None  # cooling down
        alert = AlertEvent(
            category=rule.category,
            severity=rule.severity,
            title=rule.title,
            message=rule.message,
            dedup_key=rule.key,
            source="thermal-monitor",
            value=round(value, 1),
            threshold=rule.threshold,
            unit=rule.unit,
            event_type=rule.event_type,
            provenance=DataProvenance.SIMULATED if snapshot.simulated else DataProvenance.MEASURED,
        )
        self._repo.upsert(alert)
        self._active[rule.key] = alert
        self._bus.publish(TOPIC_ALERT, alert)
        self._notify(alert)
        return alert

    def _clear(self, rule: ThresholdRule) -> None:
        alert = self._active.pop(rule.key, None)
        if alert is None:
            return
        self._bus.publish(TOPIC_ALERT_CLEARED, alert)

    def _notify(self, alert: AlertEvent) -> None:
        if not self._notifications or alert.severity is AlertSeverity.INFORMATION:
            return
        self._bus.publish("alerts.notify", alert)

    @staticmethod
    def _value_for(key: str, snapshot: TelemetrySnapshot) -> float | None:
        if key.startswith("cpu."):
            return snapshot.cpu.temperature
        if key.startswith("gpu."):
            return snapshot.gpu.temperature
        if key.startswith("storage."):
            return snapshot.storage.temperature
        return None


__all__ = [
    "AlertService",
    "ThresholdRule",
    "default_rules",
    "TOPIC_ALERT",
    "TOPIC_ALERT_CLEARED",
    "NOTIFY_SEVERITY",
]
