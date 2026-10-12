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


# ---------------------------------------------------------------------------
# Regression: the control loop must actually reach the firmware.
#
# A ``setPwm`` command is the only thing that overrides firmware automatic
# control, so every explicit-control mode (manual, safe, critical override) must
# send one.  Ordinary AUTO must *not* send ``setPwm`` (the firmware keeps
# authority and follows the ``recommendedPwm`` value in the telemetry packet),
# and must hand authority back with ``setMode AUTO`` after an override.
# ---------------------------------------------------------------------------


class _FakeFirmware:
    """Records commands and models the firmware authority the way MockEsp32 does."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []
        self.mode = "auto"
        self.pwm = 0.0
        self.recommendation: float | None = None

    def __call__(self, command: str, **kwargs: object) -> None:
        self.commands.append((command, kwargs))
        if command == "setPwm":
            self.pwm = float(kwargs["pwm"])
            self.mode = "manual"
        elif command == "setMode":
            self.mode = str(kwargs["mode"])
        elif command == "stop":
            self.pwm = 0.0
            self.mode = "manual"

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.commands]

    def feed(self, ctrl: CoolingController, snapshot: TelemetrySnapshot) -> None:
        """Model the telemetry push: AUTO firmware follows the recommendation."""
        ctrl.update(snapshot)
        self.recommendation = ctrl.recommended_pwm()
        if self.mode == "auto" and self.recommendation is not None:
            self.pwm = self.recommendation


def test_safe_mode_sends_an_explicit_manual_maximum() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile(max_pwm=25))
    ctrl.set_mode(ControllerMode.SAFE)
    ctrl.update(_snapshot(40))
    assert ("setPwm", {"pwm": 100.0}) in firmware.commands
    assert firmware.mode == "manual"
    assert firmware.pwm == pytest.approx(100.0)


def test_critical_temperature_sends_an_explicit_manual_maximum() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile(max_pwm=20))
    snap = _snapshot(99)
    snap.thermal.cpu_state = __import__(
        "app.core.enums", fromlist=["ThermalState"]
    ).ThermalState.CRITICAL
    ctrl.update(snap)
    assert firmware.names == ["setPwm"]
    assert firmware.pwm == pytest.approx(100.0)


def test_manual_mode_sends_the_slider_duty() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile())
    ctrl.set_mode(ControllerMode.MANUAL)
    ctrl.set_manual_pwm(37.0)
    ctrl.update(_snapshot(60))
    assert firmware.pwm == pytest.approx(37.0)
    assert firmware.mode == "manual"


def test_auto_recommendation_reaches_the_firmware_and_is_not_a_setpwm() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile())
    firmware.feed(ctrl, _snapshot(60))
    # The firmware follows the recommendation without ever being forced manual.
    assert firmware.mode == "auto"
    assert firmware.pwm == pytest.approx(50.0)
    assert "setPwm" not in firmware.names


def test_auto_hands_authority_back_after_an_override() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile())
    ctrl.set_mode(ControllerMode.SAFE)
    firmware.feed(ctrl, _snapshot(60))
    assert firmware.mode == "manual"
    # Recover: switch to AUTO and a non-critical snapshot must restore firmware
    # authority, after which the recommendation drives the fan again.
    ctrl.set_mode(ControllerMode.AUTO)
    _advance(ctrl, 60.0)  # let the ramp-down from the override complete
    firmware.feed(ctrl, _snapshot(60))
    assert ("setMode", {"mode": "auto"}) in firmware.commands
    assert firmware.mode == "auto"
    assert firmware.pwm == pytest.approx(50.0)


def test_a_steady_override_does_not_flood_the_link() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile())
    ctrl.set_mode(ControllerMode.SAFE)
    for _ in range(20):
        ctrl.update(_snapshot(60))
    assert firmware.names.count("setPwm") == 1


def test_disabling_and_re_asserting_the_same_override_re_commands() -> None:
    firmware = _FakeFirmware()
    ctrl = CoolingController(EventBus(), apply_command=firmware)
    ctrl.set_profile(_profile())
    ctrl.set_mode(ControllerMode.SAFE)
    ctrl.update(_snapshot(60))
    assert firmware.pwm == pytest.approx(100.0)
    ctrl.set_enabled(False)  # sends ``stop`` -> fan at zero
    assert firmware.pwm == pytest.approx(0.0)
    ctrl.set_enabled(True)
    ctrl.update(_snapshot(60))
    # The override must be re-asserted even though the duty is unchanged.
    assert firmware.pwm == pytest.approx(100.0)
    assert firmware.names.count("setPwm") == 2


def test_recommended_pwm_is_none_outside_auto_and_when_disabled() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.set_profile(_profile())
    assert ctrl.recommended_pwm() is None  # nothing computed yet
    ctrl.update(_snapshot(60))
    assert ctrl.recommended_pwm() == pytest.approx(50.0)
    ctrl.set_mode(ControllerMode.MANUAL)
    assert ctrl.recommended_pwm() is None
    ctrl.set_mode(ControllerMode.AUTO)
    ctrl.set_enabled(False)
    assert ctrl.recommended_pwm() is None


def test_recommended_pwm_is_none_without_a_profile() -> None:
    ctrl = CoolingController(EventBus())
    ctrl.update(_snapshot(60))
    assert ctrl.recommended_pwm() is None
