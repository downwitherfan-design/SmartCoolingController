"""Unit tests for the thermal analytics engine (spec §17, §18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.analytics.thermal import ThermalAnalyser, ThermalThresholds
from app.core.enums import FanControlSource, ThermalState, ThermalTrend
from app.models.cooling import CoolingProfile, FanCurve
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


def test_analysis_is_always_derived_provenance() -> None:
    analyser = ThermalAnalyser()
    analysis = analyser.analyse(_snapshot(cpu=50))
    assert analysis.provenance.value == "derived"


def test_custom_thresholds_are_honoured() -> None:
    analyser = ThermalAnalyser(ThermalThresholds(cpu_critical=70))
    assert analyser.analyse(_snapshot(cpu=72)).cpu_state is ThermalState.CRITICAL
