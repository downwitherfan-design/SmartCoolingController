"""Anomaly detector heuristics (spec §19, §101, §102).

The detector is intentionally conservative and its wording is cautious.  The one
sharp edge is a threshold that is declared but never consulted: a knob that the
UI or a config file exposes but the detector ignores is worse than no knob,
because it silently does nothing.  These tests pin the intended behaviour of
each detector, including that every declared threshold is actually wired in.
"""

from __future__ import annotations

from datetime import timedelta

from app.analytics.anomaly import AnomalyDetector, AnomalyThresholds
from app.core.enums import AnomalyKind, DataProvenance, MetricKey, MetricState
from app.models.sensors import NormalisedMetric
from app.models.telemetry import CpuTelemetry, FanTelemetry, TelemetrySnapshot


def _metric(key: MetricKey, value: float | None) -> NormalisedMetric:
    state = MetricState.VALID if value is not None else MetricState.UNAVAILABLE
    return NormalisedMetric(
        key=key,
        value=value,
        unit="",
        state=state,
        provenance=DataProvenance.MEASURED,
    )


def _snap(*, cpu: float | None = None, t_offset: float = 0.0) -> TelemetrySnapshot:
    snap = TelemetrySnapshot(sequence=0)
    snap.cpu = CpuTelemetry(temperature=cpu)
    snap.timestamp = snap.timestamp + timedelta(seconds=t_offset)
    return snap


def test_rapid_rise_is_detected_once_the_window_fills() -> None:
    detector = AnomalyDetector()
    kinds: list[AnomalyKind] = []
    for index in range(6):
        snapshot = _snap(cpu=40.0 + index * 4.0, t_offset=index * 5.0)
        kinds.extend(a.kind for a in detector.detect(snapshot))
    assert AnomalyKind.RAPID_THERMAL_RISE in kinds


def test_slow_rise_is_not_flagged() -> None:
    detector = AnomalyDetector()
    kinds: list[AnomalyKind] = []
    for index in range(8):
        snapshot = _snap(cpu=40.0 + index, t_offset=index * 30.0)
        kinds.extend(a.kind for a in detector.detect(snapshot))
    assert AnomalyKind.RAPID_THERMAL_RISE not in kinds


def test_vanished_sensor_is_reported() -> None:
    detector = AnomalyDetector()
    detector.detect(_snap(cpu=50.0))
    with_metric = TelemetrySnapshot(sequence=1)
    with_metric.metrics = {MetricKey.CPU_TEMPERATURE: _metric(MetricKey.CPU_TEMPERATURE, 50.0)}
    detector.detect(with_metric)

    without = TelemetrySnapshot(sequence=2)
    without.metrics = {MetricKey.CPU_TEMPERATURE: _metric(MetricKey.CPU_TEMPERATURE, None)}
    kinds = [a.kind for a in detector.detect(without)]
    assert AnomalyKind.SENSOR_DISAPPEARED in kinds


def test_fan_drop_honours_the_configured_ratio() -> None:
    # The threshold is a public knob; the detector must actually consult it
    # rather than a hardcoded 0.5.
    detector = AnomalyDetector(AnomalyThresholds(unexpected_rpm_ratio_low=0.1))

    def fan_snap(rpm: float, target: float = 80.0) -> TelemetrySnapshot:
        snap = TelemetrySnapshot(sequence=0)
        snap.fan = FanTelemetry(rpm=rpm, target_percent=target, available=True)
        return snap

    detector.detect(fan_snap(2000))
    # 700 RPM is 0.35 of the previous reading.  With ratio 0.1 that is *not* a
    # drop; with the old hardcoded 0.5 it would have been.
    kinds = [a.kind for a in detector.detect(fan_snap(700))]
    assert AnomalyKind.UNEXPECTED_RPM not in kinds

    detector = AnomalyDetector(AnomalyThresholds(unexpected_rpm_ratio_low=0.5))
    detector.detect(fan_snap(2000))
    kinds = [a.kind for a in detector.detect(fan_snap(700))]
    assert AnomalyKind.UNEXPECTED_RPM in kinds


def test_fan_drop_requires_a_high_target() -> None:
    detector = AnomalyDetector()
    snap = TelemetrySnapshot(sequence=0)
    snap.fan = FanTelemetry(rpm=2000, target_percent=10.0, available=True)
    detector.detect(snap)
    snap.fan = FanTelemetry(rpm=100, target_percent=10.0, available=True)
    kinds = [a.kind for a in detector.detect(snap)]
    assert AnomalyKind.UNEXPECTED_RPM not in kinds


def test_voltage_out_of_range_is_reported() -> None:
    detector = AnomalyDetector()
    low = TelemetrySnapshot(sequence=0)
    low.fan = FanTelemetry(voltage_v=5.0, available=True)
    high = TelemetrySnapshot(sequence=1)
    high.fan = FanTelemetry(voltage_v=15.0, available=True)
    kinds = [a.kind for a in detector.detect(low)]
    kinds.extend(a.kind for a in detector.detect(high))
    assert kinds.count(AnomalyKind.UNEXPECTED_VOLTAGE) == 2


def test_nominal_voltage_is_not_flagged() -> None:
    detector = AnomalyDetector()
    snap = TelemetrySnapshot(sequence=0)
    snap.fan = FanTelemetry(voltage_v=12.0, available=True)
    assert detector.detect(snap) == []


def test_high_current_is_reported() -> None:
    detector = AnomalyDetector()
    snap = TelemetrySnapshot(sequence=0)
    snap.fan = FanTelemetry(current_a=3.5, available=True)
    kinds = [a.kind for a in detector.detect(snap)]
    assert AnomalyKind.UNEXPECTED_POWER in kinds


def test_anomalies_always_carry_cautious_wording() -> None:
    detector = AnomalyDetector()
    snap = TelemetrySnapshot(sequence=0)
    snap.fan = FanTelemetry(voltage_v=15.0, current_a=3.0, available=True)
    for anomaly in detector.detect(snap):
        assert anomaly.summary
        assert anomaly.detail
        assert anomaly.confidence in {"low", "medium", "high"}
