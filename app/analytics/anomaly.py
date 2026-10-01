"""Anomaly detection heuristics (spec §19, §101, §102).

Each detector is intentionally conservative and its wording is cautious: the
application *observes* patterns, it does not *diagnose* faults.  Detectors keep
small amounts of state so they can notice transitions (a sensor disappearing, a
voltage spike) rather than only absolute levels.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.core.enums import AnomalyKind, MetricKey
from app.models.alerts import Anomaly
from app.models.telemetry import TelemetrySnapshot


@dataclass(slots=True)
class AnomalyThresholds:
    rapid_rise_c_per_min: float = 8.0
    stale_after_s: float = 5.0
    unexpected_rpm_ratio_low: float = 0.3
    unexpected_voltage_low: float = 8.0
    unexpected_voltage_high: float = 13.5
    unexpected_current_high: float = 2.0


class AnomalyDetector:
    """Detects transition-based anomalies across successive snapshots."""

    def __init__(self, thresholds: AnomalyThresholds | None = None) -> None:
        self.thresholds = thresholds or AnomalyThresholds()
        self._cpu_history: deque[tuple[float, float]] = deque(maxlen=16)
        self._gpu_history: deque[tuple[float, float]] = deque(maxlen=16)
        self._last_rpm: float | None = None
        self._seen_metrics: set[MetricKey] = set()

    def detect(self, snapshot: TelemetrySnapshot) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        anomalies.extend(self._rapid_rise(snapshot))
        anomalies.extend(self._sensor_disappearance(snapshot))
        anomalies.extend(self._fan(snapshot))
        anomalies.extend(self._electrical(snapshot))
        return anomalies

    # -- detectors ---------------------------------------------------------

    def _rapid_rise(self, snapshot: TelemetrySnapshot) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        timestamp = snapshot.timestamp.timestamp()
        for label, value, history in (
            ("CPU", snapshot.cpu.temperature, self._cpu_history),
            ("GPU", snapshot.gpu.temperature, self._gpu_history),
        ):
            if value is None:
                continue
            history.append((timestamp, value))
            rate = self._rate(history)
            if rate is not None and rate >= self.thresholds.rapid_rise_c_per_min:
                anomalies.append(
                    Anomaly(
                        kind=AnomalyKind.RAPID_THERMAL_RISE,
                        summary=f"{label} temperature rose quickly",
                        detail=(
                            f"Observed about {rate:+.1f} °C/min. This is an "
                            "application heuristic and may reflect a burst workload."
                        ),
                        confidence="medium",
                        value=rate,
                        unit="°C/min",
                    )
                )
        return anomalies

    def _sensor_disappearance(self, snapshot: TelemetrySnapshot) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        present = {key for key, metric in snapshot.metrics.items() if metric.available}
        disappeared = self._seen_metrics - present
        self._seen_metrics = present
        if disappeared:
            anomalies.append(
                Anomaly(
                    kind=AnomalyKind.SENSOR_DISAPPEARED,
                    summary="Some sensors stopped reporting",
                    detail="Sensors no longer available: " + ", ".join(sorted(disappeared)),
                    confidence="medium",
                )
            )
        return anomalies

    def _fan(self, snapshot: TelemetrySnapshot) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        rpm = snapshot.fan.rpm
        if rpm is not None and self._last_rpm is not None:
            target = snapshot.fan.target_percent
            if target is not None and target >= 40.0 and rpm < self._last_rpm * 0.5:
                anomalies.append(
                    Anomaly(
                        kind=AnomalyKind.UNEXPECTED_RPM,
                        summary="Fan speed dropped unexpectedly",
                        detail=(
                            f"RPM fell from {self._last_rpm:.0f} to {rpm:.0f} while the "
                            "target duty stayed high. The fan may be obstructed or "
                            "the tachometer may be misreading."
                        ),
                        confidence="low",
                        value=rpm,
                        unit="RPM",
                    )
                )
        self._last_rpm = rpm
        return anomalies

    def _electrical(self, snapshot: TelemetrySnapshot) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        voltage = snapshot.fan.voltage_v
        current = snapshot.fan.current_a
        if voltage is not None and (
            voltage < self.thresholds.unexpected_voltage_low
            or voltage > self.thresholds.unexpected_voltage_high
        ):
            anomalies.append(
                Anomaly(
                    kind=AnomalyKind.UNEXPECTED_VOLTAGE,
                    summary="Fan supply voltage is outside the expected range",
                    detail=f"Observed {voltage:.2f} V on the 12 V rail.",
                    confidence="medium",
                    value=voltage,
                    unit="V",
                )
            )
        if current is not None and current > self.thresholds.unexpected_current_high:
            anomalies.append(
                Anomaly(
                    kind=AnomalyKind.UNEXPECTED_POWER,
                    summary="Fan current draw is unusually high",
                    detail=f"Observed {current:.2f} A; the fan may be stalled or obstructed.",
                    confidence="medium",
                    value=current,
                    unit="A",
                )
            )
        return anomalies

    @staticmethod
    def _rate(history: deque[tuple[float, float]]) -> float | None:
        if len(history) < 4:
            return None
        first_t, first_v = history[0]
        last_t, last_v = history[-1]
        span = last_t - first_t
        if span <= 1e-6:
            return None
        return (last_v - first_v) / span * 60.0


__all__ = ["AnomalyDetector", "AnomalyThresholds"]
