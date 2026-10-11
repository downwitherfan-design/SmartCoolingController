"""Unit tests for the thermal analytics engine (spec §17, §18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.thermal import ThermalAnalyser, ThermalThresholds
from app.core.enums import (
    DataProvenance,
    FanControlSource,
    MetricKey,
    MetricState,
    ThermalState,
    ThermalTrend,
)
from app.models.cooling import CoolingProfile, FanCurve
from app.models.sensors import NormalisedMetric
from app.models.telemetry import CpuTelemetry, GpuTelemetry, TelemetrySnapshot


def _snapshot(
    cpu: float | None = None,
    gpu: float | None = None,
    *,
    at: datetime | None = None,
) -> TelemetrySnapshot:
    snap = TelemetrySnapshot(sequence=0)
    if at is not None:
        snap.timestamp = at
    snap.cpu = CpuTelemetry(temperature=cpu)
    snap.gpu = GpuTelemetry(temperature=gpu)
    return snap


def _profile(source: FanControlSource = FanControlSource.MAX) -> CoolingProfile:
    return CoolingProfile(
        key="test",
        name="Test",
        curve=FanCurve.from_points([(30, 0), (60, 50), (90, 100)]),
        source=source,
        thermal_weight_cpu=0.6,
        thermal_weight_gpu=0.4,
    )


def test_cool_temperature_maps_to_cool_state() -> None:
    analyser = ThermalAnalyser()
    analysis = analyser.analyse(_snapshot(cpu=35, gpu=30))
    assert analysis.cpu_state is ThermalState.COOL
    assert analysis.gpu_state is ThermalState.COOL


def test_thresholds_map_to_expected_states() -> None:
    analyser = ThermalAnalyser()
    # CPU: warm>=70, high>=80, critical>=92 (no hysteresis on the way up).
    assert analyser.analyse(_snapshot(cpu=75)).cpu_state is ThermalState.WARM
    analyser = ThermalAnalyser()
    assert analyser.analyse(_snapshot(cpu=85)).cpu_state is ThermalState.HIGH
    analyser = ThermalAnalyser()
    assert analyser.analyse(_snapshot(cpu=95)).cpu_state is ThermalState.CRITICAL


def test_hysteresis_prevents_flapping_near_a_boundary() -> None:
    analyser = ThermalAnalyser(hysteresis_c=3.0)
    assert analyser.analyse(_snapshot(cpu=81)).cpu_state is ThermalState.HIGH
    # Dropping to 78 is only 3 °C below the 80 °C high threshold; with 3 °C
    # hysteresis the state must stay HIGH rather than flap back to WARM.
    assert analyser.analyse(_snapshot(cpu=78)).cpu_state is ThermalState.HIGH
    # A decisive drop clears it.
    assert analyser.analyse(_snapshot(cpu=70)).cpu_state is ThermalState.WARM


def test_headroom_is_measured_against_the_nearest_critical_limit() -> None:
    analyser = ThermalAnalyser()
    analysis = analyser.analyse(_snapshot(cpu=82, gpu=80))
    # CPU critical is 92 → 10 °C headroom; GPU critical is 90 → 10 °C.
    assert analysis.headroom_c == pytest.approx(10.0)


def test_weighted_input_uses_the_profile_weights() -> None:
    analyser = ThermalAnalyser()
    analyser.set_profile(_profile(FanControlSource.WEIGHTED))
    analysis = analyser.analyse(_snapshot(cpu=80, gpu=60))
    # (80*0.6 + 60*0.4) / 1.0 == 72
    assert analysis.weighted_input_c == pytest.approx(72.0)


def test_weighted_input_max_source_takes_the_hottest() -> None:
    analyser = ThermalAnalyser()
    analyser.set_profile(_profile(FanControlSource.MAX))
    analysis = analyser.analyse(_snapshot(cpu=55, gpu=80))
    assert analysis.weighted_input_c == pytest.approx(80.0)


def test_weighted_input_cpu_source_ignores_gpu() -> None:
    analyser = ThermalAnalyser()
    analyser.set_profile(_profile(FanControlSource.CPU))
    analysis = analyser.analyse(_snapshot(cpu=50, gpu=95))
    assert analysis.weighted_input_c == pytest.approx(50.0)


def test_single_sensor_degrades_gracefully() -> None:
    analyser = ThermalAnalyser()
    analyser.set_profile(_profile(FanControlSource.WEIGHTED))
    analysis = analyser.analyse(_snapshot(cpu=None, gpu=64))
    assert analysis.weighted_input_c == pytest.approx(64.0)
    assert analysis.cpu_state is ThermalState.NORMAL  # never observed


def test_no_sensors_yields_no_weighted_input() -> None:
    analyser = ThermalAnalyser()
    analysis = analyser.analyse(_snapshot(cpu=None, gpu=None))
    assert analysis.weighted_input_c is None
    assert analysis.recommendation_pwm is None


def test_recommendation_follows_the_curve() -> None:
    analyser = ThermalAnalyser()
    analyser.set_profile(_profile(FanControlSource.MAX))
    analysis = analyser.analyse(_snapshot(cpu=60, gpu=None))
    assert analysis.recommendation_pwm == pytest.approx(50.0)


def test_rising_temperature_is_detected_as_a_trend() -> None:
    analyser = ThermalAnalyser()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    analysis = None
    for index in range(12):
        analysis = analyser.analyse(
            _snapshot(cpu=40 + index * 4, at=base + timedelta(seconds=index * 5))
        )
    assert analysis is not None
    assert analysis.cpu_trend is ThermalTrend.RISING
    assert analysis.cpu_rate_c_per_min is not None
    assert analysis.cpu_rate_c_per_min > 0


def test_falling_temperature_is_detected_as_a_trend() -> None:
    analyser = ThermalAnalyser()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    analysis = None
    for index in range(12):
        analysis = analyser.analyse(
            _snapshot(cpu=90 - index * 4, at=base + timedelta(seconds=index * 5))
        )
    assert analysis is not None
    assert analysis.cpu_trend is ThermalTrend.FALLING


def test_stable_temperature_is_reported_as_stable() -> None:
    analyser = ThermalAnalyser()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    analysis = None
    for index in range(12):
        analysis = analyser.analyse(
            _snapshot(cpu=65, at=base + timedelta(seconds=index * 5))
        )
    assert analysis is not None
    assert analysis.cpu_trend is ThermalTrend.STABLE


def test_analysis_writes_thermal_state_back_onto_the_snapshot() -> None:
    analyser = ThermalAnalyser()
    snapshot = _snapshot(cpu=85)
    analyser.analyse(snapshot)
    assert snapshot.cpu.thermal_state is ThermalState.HIGH


def test_average_min_max_track_the_observed_window() -> None:
    analyser = ThermalAnalyser()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index, temp in enumerate([40, 50, 60, 70]):
        analysis = analyser.analyse(
            _snapshot(cpu=temp, at=base + timedelta(seconds=index))
        )
    assert analysis.cpu_min_c == pytest.approx(40)
    assert analysis.cpu_max_c == pytest.approx(70)
    assert analysis.cpu_average_c == pytest.approx(55)


def test_critical_temperature_raises_an_anomaly() -> None:
    analyser = ThermalAnalyser()
    analyser.analyse(_snapshot(cpu=96))
    kinds = {a.kind.value for a in analyser.last_anomalies}
    assert kinds, "a critical CPU temperature should raise at least one anomaly"


def _rising_analyser(**kwargs) -> tuple[ThermalAnalyser, list[str]]:
    analyser = ThermalAnalyser(**kwargs)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    kinds: list[str] = []
    # +1 °C every 5 s is about +12 °C/min — above a low threshold, below a high
    # one, and comfortably under the CPU warm limit so no other anomaly fires.
    for index in range(12):
        analyser.analyse(
            _snapshot(cpu=40 + index, at=base + timedelta(seconds=index * 5))
        )
    kinds = [a.kind.value for a in analyser.last_anomalies]
    return analyser, kinds


def test_configured_rapid_rise_threshold_is_honoured() -> None:
    """``alerts.rapid_rise_c_per_min`` must actually drive the detector.

    The analyser used to hardcode the threshold as ``stable_band * 6`` and
    ignore the setting entirely.  A low threshold must flag the same rise the
    default threshold lets through.
    """
    _, permissive = _rising_analyser(rapid_rise_c_per_min=5.0)
    assert "rapid_thermal_rise" in permissive

    _, strict = _rising_analyser(rapid_rise_c_per_min=50.0)
    assert "rapid_thermal_rise" not in strict


def test_rapid_rise_threshold_can_be_changed_at_runtime() -> None:
    analyser = ThermalAnalyser(rapid_rise_c_per_min=50.0)
    analyser.set_rapid_rise_threshold(5.0)
    assert analyser._rapid_rise_c_per_min == 5.0


def test_analysis_is_always_derived_provenance() -> None:
    analyser = ThermalAnalyser()
    analysis = analyser.analyse(_snapshot(cpu=50))
    assert analysis.provenance.value == "derived"



def _metric(key: MetricKey, value: float | None) -> NormalisedMetric:
    state = MetricState.VALID if value is not None else MetricState.UNAVAILABLE
    return NormalisedMetric(
        key=key, value=value, unit="", state=state, provenance=DataProvenance.MEASURED
    )


def _sensor_snapshot(*, at: datetime, cpu: float | None) -> TelemetrySnapshot:
    snap = _snapshot(cpu=cpu, at=at)
    snap.metrics = {MetricKey.CPU_TEMPERATURE: _metric(MetricKey.CPU_TEMPERATURE, cpu)}
    return snap


def test_vanished_sensor_is_reported_by_the_live_analyser() -> None:
    """A sensor that stops reporting must raise SENSOR_DISAPPEARED.

    The live detector never emitted this kind; only the standalone reference
    detector did.  A sensor seen reporting and then gone for the stale window
    must be flagged, once.
    """
    analyser = ThermalAnalyser(sensor_stale_after_s=5.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    analyser.analyse(_sensor_snapshot(at=base, cpu=50.0))
    assert "sensor_disappeared" not in {
        a.kind.value for a in analyser.last_anomalies
    }

    # Gone but within the grace window: still nothing.
    analyser.analyse(_sensor_snapshot(at=base + timedelta(seconds=3), cpu=None))
    assert "sensor_disappeared" not in {
        a.kind.value for a in analyser.last_anomalies
    }

    # Gone past the window: reported exactly once.
    analyser.analyse(_sensor_snapshot(at=base + timedelta(seconds=6), cpu=None))
    kinds = [a.kind.value for a in analyser.last_anomalies]
    assert "sensor_disappeared" in kinds

    analyser.analyse(_sensor_snapshot(at=base + timedelta(seconds=9), cpu=None))
    assert "sensor_disappeared" not in {
        a.kind.value for a in analyser.last_anomalies
    }


def test_transient_sensor_drop_does_not_raise_an_anomaly() -> None:
    """One missing sample must not raise; the sensor returning clears it."""
    analyser = ThermalAnalyser(sensor_stale_after_s=5.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    analyser.analyse(_sensor_snapshot(at=base, cpu=50.0))
    analyser.analyse(_sensor_snapshot(at=base + timedelta(seconds=1), cpu=None))
    # Back before the window elapses.
    analyser.analyse(_sensor_snapshot(at=base + timedelta(seconds=2), cpu=51.0))
    analyser.analyse(_sensor_snapshot(at=base + timedelta(seconds=30), cpu=52.0))
    assert "sensor_disappeared" not in {
        a.kind.value for a in analyser.last_anomalies
    }


def test_sensor_that_was_never_present_is_not_a_dropout() -> None:
    analyser = ThermalAnalyser(sensor_stale_after_s=0.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # GPU temperature was never available, so its absence is not a transition.
    analyser.analyse(_snapshot(cpu=50, gpu=None, at=base))
    assert "sensor_disappeared" not in {
        a.kind.value for a in analyser.last_anomalies
    }


def test_custom_thresholds_are_honoured() -> None:
    analyser = ThermalAnalyser(ThermalThresholds(cpu_critical=70))
    assert analyser.analyse(_snapshot(cpu=72)).cpu_state is ThermalState.CRITICAL
