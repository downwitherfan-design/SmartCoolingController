"""Unit tests for the cooling controller (spec §20, §21, §22, §105).

These exercise the safety envelope: the controller must never exceed its
configured limits, must honour manual/off/safe modes, and must force maximum
cooling on a critical temperature regardless of profile.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums import ControllerMode
from app.core.events import EventBus
from app.models.cooling import CoolingProfile, FanCurve
from app.models.telemetry import CpuTelemetry, GpuTelemetry, TelemetrySnapshot, ThermalAnalysis
from app.services.cooling_control import CoolingController, SafetyLimits


def _profile(**overrides) -> CoolingProfile:
    base = {
        "key": "balanced",
        "name": "Balanced",
        "curve": FanCurve.from_points([(30, 0), (60, 50), (90, 100)]),
        "min_pwm": 0.0,
        "max_pwm": 100.0,
        "ramp_up_pct_per_s": 1000.0,  # disable rate limiting unless a test sets it
        "ramp_down_pct_per_s": 1000.0,
    }
    base.update(overrides)
    return CoolingProfile(**base)


def _snapshot(cpu: float | None, *, weighted: float | None = None) -> TelemetrySnapshot:
    snap = TelemetrySnapshot(sequence=0)
    snap.cpu = CpuTelemetry(temperature=cpu)
    snap.gpu = GpuTelemetry(temperature=None)
    snap.thermal = ThermalAnalysis(weighted_input_c=weighted if weighted is not None else cpu)
    return snap


def _advance(ctrl: CoolingController, seconds: float) -> None:
    """Move the controller's internal clock back so rate limiting can elapse.

    The rate limiter is wall-clock based; back-dating its last-update stamp lets
    us assert ramp behaviour deterministically instead of sleeping.
    """
    from app.core.clock import utc_now

    ctrl._last_update_at = utc_now() - timedelta(seconds=seconds)


@pytest.fixture
def controller() -> CoolingController:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile())
    return ctrl


def test_auto_mode_follows_the_curve(controller: CoolingController) -> None:
    decision = controller.update(_snapshot(60))
    assert decision.target_pwm == pytest.approx(50.0)
    assert decision.mode is ControllerMode.AUTO
    assert decision.safety_override is False


def test_critical_temperature_forces_maximum_cooling() -> None:
    ctrl = CoolingController(EventBus(), limits=SafetyLimits())
    ctrl.set_profile(_profile(max_pwm=70))
    snap = _snapshot(99)
    snap.thermal.cpu_state = __import__("app.core.enums", fromlist=["ThermalState"]).ThermalState.CRITICAL
    decision = ctrl.update(snap)
    assert decision.target_pwm == pytest.approx(100.0)
    assert decision.safety_override is True


def test_disabled_controller_stops_the_fan(controller: CoolingController) -> None:
    controller.update(_snapshot(70))
    controller.set_enabled(False)
    decision = controller.update(_snapshot(70))
    assert decision.target_pwm == pytest.approx(0.0)


def test_manual_mode_ignores_the_curve(controller: CoolingController) -> None:
    controller.set_mode(ControllerMode.MANUAL)
    controller.set_manual_pwm(35.0)
    decision = controller.update(_snapshot(90))
    assert decision.target_pwm == pytest.approx(35.0)
    assert decision.mode is ControllerMode.MANUAL


def test_safe_mode_runs_the_fan_at_maximum(controller: CoolingController) -> None:
    controller.set_mode(ControllerMode.SAFE)
    decision = controller.update(_snapshot(40))
    assert decision.target_pwm == pytest.approx(100.0)
    assert decision.safety_override is True


def test_safe_mode_ignores_a_capped_profile_envelope() -> None:
    # Regression: a profile with a low max_pwm used to clamp the safety
    # override, so "safe mode" could command well below full speed.
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile(min_pwm=0, max_pwm=25))
    ctrl.set_mode(ControllerMode.SAFE)
    assert ctrl.update(_snapshot(40)).target_pwm == pytest.approx(100.0)


def test_critical_temperature_ignores_a_capped_profile_envelope() -> None:
    # Same safety-override defect on the automatic path: an over-temperature
    # must reach the absolute envelope, not the profile's ceiling.
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile(min_pwm=0, max_pwm=20))
    decision = ctrl.update(_snapshot(101))
    assert decision.target_pwm == pytest.approx(100.0)
    assert decision.safety_override is True


def test_safety_override_still_respects_the_absolute_envelope() -> None:
    ctrl = CoolingController(EventBus(), limits=SafetyLimits(emergency_pwm=120.0))
    ctrl.set_mode(ControllerMode.SAFE)
    assert ctrl.update(_snapshot(40)).target_pwm == pytest.approx(100.0)



def test_manual_pwm_is_clamped_to_the_hard_limits() -> None:
    ctrl = CoolingController(EventBus(), limits=SafetyLimits(min_pwm=20, max_pwm=80))
    ctrl.set_mode(ControllerMode.MANUAL)
    ctrl.set_manual_pwm(95.0)
    assert ctrl.update(_snapshot(50)).target_pwm == pytest.approx(80.0)
    ctrl.set_manual_pwm(5.0)
    assert ctrl.update(_snapshot(50)).target_pwm == pytest.approx(20.0)


def test_profile_envelope_limits_the_curve_output() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile(min_pwm=30, max_pwm=70))
    assert ctrl.update(_snapshot(20)).target_pwm == pytest.approx(30.0)
    _advance(ctrl, 60.0)
    assert ctrl.update(_snapshot(88)).target_pwm == pytest.approx(70.0)


def test_rate_limiting_slows_a_sudden_ramp_up() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile(ramp_up_pct_per_s=10.0))
    first = ctrl.update(_snapshot(30))  # 0 %
    assert first.target_pwm == pytest.approx(0.0)
    # One second later we demand ~100 %; the ramp allows only ~10 %.
    _advance(ctrl, 1.0)
    second = ctrl.update(_snapshot(90))
    assert second.target_pwm == pytest.approx(10.0, abs=1.5)
    assert second.rate_limited is True


def test_no_profile_reports_a_reason_without_crashing() -> None:
    ctrl = CoolingController(EventBus())
    decision = ctrl.update(_snapshot(80))
    assert decision.target_pwm == pytest.approx(0.0)
    assert "profile" in decision.reason.lower()


def test_no_temperature_data_keeps_the_previous_target() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile())
    ctrl.update(_snapshot(60))  # -> 50 %
    decision = ctrl.update(_snapshot(None, weighted=None))
    assert decision.target_pwm == pytest.approx(50.0)


def test_calibration_mode_leaves_the_fan_alone() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile())
    ctrl.set_mode(ControllerMode.CALIBRATION)
    decision = ctrl.update(_snapshot(95))
    assert decision.mode is ControllerMode.CALIBRATION
    assert decision.safety_override is False


def test_decision_reports_change_against_the_previous_target() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile())
    first = ctrl.update(_snapshot(30))
    assert first.changed is True  # previous is None
    _advance(ctrl, 60.0)
    second = ctrl.update(_snapshot(60))
    assert second.changed is True
    third = ctrl.update(_snapshot(60))
    assert third.changed is False


def test_decision_serialises_to_a_plain_dict() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile())
    payload = ctrl.update(_snapshot(60)).to_dict()
    assert payload["mode"] == "auto"
    assert isinstance(payload["target_pwm"], float)
    assert "timestamp" in payload
