"""Cooling control service (spec §39, §40, §41, §47).

This is the application's control brain.  On every telemetry snapshot it turns
a cooling profile plus the current thermal analysis into a fan duty target and
hands it to the ESP32.

Safety contract
---------------
The desktop *recommends*; the ESP32 firmware is the final authority and is free
to ignore any command (spec §2, §47).  Even so, this controller is written as if
it were in charge, so that a bug here cannot command anything dangerous:

* the target is always clamped into ``[min_pwm, max_pwm]`` and then into the
  absolute ``[0, 100]`` envelope;
* a critical temperature forces full speed immediately, bypassing the ramp
  limiter — safety is never rate-limited;
* the ramp limiter only ever *slows* a change, never amplifies it;
* a stalled fan while duty is high raises an anomaly rather than silently
  pushing more power.

Every decision carries a human-readable reason so the UI can explain itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.clock import utc_now
from app.core.enums import (
    AnomalyKind,
    ControllerMode,
    DataProvenance,
    FanControlSource,
    ThermalState,
)
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.core.units import clamp
from app.models.alerts import Anomaly
from app.models.cooling import CoolingProfile
from app.models.telemetry import TelemetrySnapshot

_log = get_logger(__name__)

TOPIC_DECISION = "cooling.decision"
TOPIC_MODE = "cooling.mode"

#: Temperature at or above which we force 100% regardless of the profile.
ABSOLUTE_CRITICAL_C = 95.0
#: Duty at or above which a stopped fan is considered stalled.
STALL_DUTY_PERCENT = 40.0


@dataclass(slots=True)
class SafetyLimits:
    """Hard limits the controller will never exceed."""

    min_pwm: float = 0.0
    max_pwm: float = 100.0
    absolute_critical_c: float = ABSOLUTE_CRITICAL_C
    stall_rpm: float = 300.0
    emergency_pwm: float = 100.0


@dataclass(slots=True)
class CoolingDecision:
    """The outcome of one control step."""

    target_pwm: float
    previous_pwm: float | None
    reason: str
    profile_key: str = ""
    mode: ControllerMode = ControllerMode.AUTO
    safety_override: bool = False
    rate_limited: bool = False
    clamped: bool = False
    weighted_input_c: float | None = None
    provenance: DataProvenance = DataProvenance.DERIVED
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def changed(self) -> bool:
        return self.previous_pwm is None or abs(self.target_pwm - self.previous_pwm) >= 0.5

    def to_dict(self) -> dict[str, object]:
        return {
            "target_pwm": round(self.target_pwm, 2),
            "previous_pwm": None if self.previous_pwm is None else round(self.previous_pwm, 2),
            "reason": self.reason,
            "profile_key": self.profile_key,
            "mode": self.mode.value,
            "safety_override": self.safety_override,
            "rate_limited": self.rate_limited,
            "clamped": self.clamped,
            "weighted_input_c": self.weighted_input_c,
            "provenance": self.provenance.value,
            "timestamp": self.timestamp.isoformat(),
        }


class CoolingController:
    """Computes and applies the fan duty target."""

    def __init__(
        self,
        bus: EventBus,
        *,
        profile: CoolingProfile | None = None,
        limits: SafetyLimits | None = None,
        apply_command: object | None = None,
    ) -> None:
        self._bus = bus
        self._profile = profile
        self._limits = limits or SafetyLimits()
        self._mode = ControllerMode.AUTO
        self._manual_pwm = 0.0
        self._enabled = True
        self._target: float | None = None
        self._last_update_at: datetime | None = None
        self._apply_command = apply_command
        self._last_decision: CoolingDecision | None = None

    # -- configuration -----------------------------------------------------

    @property
    def mode(self) -> ControllerMode:
        return self._mode

    @property
    def profile(self) -> CoolingProfile | None:
        return self._profile

    @property
    def target_pwm(self) -> float | None:
        return self._target

    @property
    def last_decision(self) -> CoolingDecision | None:
        return self._last_decision

    def set_apply_command(self, apply_command: object | None) -> None:
        """Register ``apply_command(command: str, **kwargs)`` (the connection manager)."""
        self._apply_command = apply_command

    def set_profile(self, profile: CoolingProfile | None) -> None:
        self._profile = profile
        if profile is not None:
            self._limits.min_pwm = profile.min_pwm
            self._limits.max_pwm = profile.max_pwm

    def set_mode(self, mode: ControllerMode) -> None:
        if mode is self._mode:
            return
        self._mode = mode
        self._bus.publish(TOPIC_MODE, {"mode": mode})
        if mode is ControllerMode.MANUAL:
            self._command("setMode", mode=ControllerMode.MANUAL.value)
            self._command("setPwm", pwm=self._manual_pwm)
        else:
            self._command("setMode", mode=mode.value)

    def set_enabled(self, enabled: bool) -> None:
        """Turn cooling on or off. Off commands a safe stop; the firmware decides."""
        self._enabled = enabled
        if not enabled:
            self._target = 0.0
            self._command("stop")
        else:
            self._command("setMode", mode=self._mode.value)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_manual_pwm(self, pwm: float) -> None:
        self._manual_pwm = clamp(pwm, 0.0, 100.0)
        if self._mode is ControllerMode.MANUAL:
            self._target = self._manual_pwm
            self._command("setPwm", pwm=self._target)

    # -- control loop ------------------------------------------------------

    def update(self, snapshot: TelemetrySnapshot) -> CoolingDecision:
        """Advance the controller by one snapshot and return the decision."""
        previous = self._target

        if not self._enabled:
            self._target = 0.0
            return self._emit(
                CoolingDecision(0.0, previous, "Cooling is switched off", mode=self._mode)
            )

        if self._mode is ControllerMode.SAFE:
            decision = self._clamp_and_emit(
                self._limits.emergency_pwm,
                previous,
                "Safe mode — running the fan at maximum",
                safety_override=True,
            )
            self._apply(decision)
            return self._emit(decision)

        if self._mode is ControllerMode.CALIBRATION:
            return self._emit(
                CoolingDecision(
                    previous or 0.0,
                    previous,
                    "Calibration in progress — fan controlled by firmware",
                    mode=self._mode,
                )
            )

        if self._mode is ControllerMode.MANUAL:
            decision = self._clamp_and_emit(
                self._manual_pwm, previous, "Manual override", safety_override=False
            )
            self._apply(decision)
            return self._emit(decision)

        # Automatic mode.
        critical = self._critical_temperature(snapshot)
        if critical:
            decision = CoolingDecision(
                self._limits.emergency_pwm,
                previous,
                "Critical temperature — forcing maximum cooling",
                profile_key=self._profile.key if self._profile else "",
                mode=self._mode,
                safety_override=True,
                weighted_input_c=snapshot.thermal.weighted_input_c,
            )
            self._target = decision.target_pwm
            self._apply(decision)
            return self._emit(decision)

        if self._profile is None:
            return self._emit(
                CoolingDecision(
                    previous or 0.0, previous, "No cooling profile selected", mode=self._mode
                )
            )

        weighted = snapshot.thermal.weighted_input_c
        if weighted is None:
            return self._emit(
                CoolingDecision(
                    previous or 0.0,
                    previous,
                    "No temperature data available",
                    profile_key=self._profile.key,
                    mode=self._mode,
                )
            )

        desired = self._profile.curve.duty_for(
            weighted, min_pwm=self._profile.min_pwm, max_pwm=self._profile.max_pwm
        )
        source_note = {
            FanControlSource.CPU: "CPU temperature",
            FanControlSource.GPU: "GPU temperature",
            FanControlSource.MAX: "hottest of CPU/GPU",
            FanControlSource.WEIGHTED: "weighted CPU/GPU temperature",
        }.get(self._profile.source, "thermal input")
        decision = self._clamp_and_emit(
            desired,
            previous,
            f"Profile '{self._profile.name}' from {source_note}",
            safety_override=False,
            weighted=weighted,
        )
        decision = self._rate_limit(decision, snapshot)
        self._detect_stall(snapshot, decision)
        return self._emit(decision)

    # -- helpers -----------------------------------------------------------

    def _critical_temperature(self, snapshot: TelemetrySnapshot) -> bool:
        if snapshot.thermal.cpu_state is ThermalState.CRITICAL:
            return True
        if snapshot.thermal.gpu_state is ThermalState.CRITICAL:
            return True
        return any(
            temperature is not None and temperature >= self._limits.absolute_critical_c
            for temperature in (snapshot.cpu.temperature, snapshot.gpu.temperature)
        )

    def _clamp_and_emit(
        self,
        desired: float,
        previous: float | None,
        reason: str,
        *,
        safety_override: bool,
        weighted: float | None = None,
    ) -> CoolingDecision:
        low = self._limits.min_pwm
        high = self._limits.max_pwm
        if safety_override:
            # A safety override is never subject to the profile envelope: a
            # profile whose max_pwm is below the emergency duty would otherwise
            # cap the emergency response.  Only the absolute [0, 100] envelope
            # applies, and the firmware remains the final authority (spec §47).
            low, high = 0.0, 100.0
        clamped_value = clamp(desired, low, high)
        clamped_value = clamp(clamped_value, 0.0, 100.0)
        return CoolingDecision(
            clamped_value,
            previous,
            reason,
            profile_key=self._profile.key if self._profile else "",
            mode=self._mode,
            safety_override=safety_override,
            clamped=abs(clamped_value - desired) > 1e-6,
            weighted_input_c=weighted,
        )

    def _rate_limit(self, decision: CoolingDecision, snapshot: TelemetrySnapshot) -> CoolingDecision:
        if self._profile is None or decision.previous_pwm is None:
            return decision
        now = utc_now()
        dt = 1.0 if self._last_update_at is None else max(
            0.0, (now - self._last_update_at).total_seconds()
        )
        if dt <= 0.0:
            return decision
        delta = decision.target_pwm - decision.previous_pwm
        if abs(delta) < 1e-6:
            return decision
        rate = (
            self._profile.ramp_up_pct_per_s if delta > 0 else self._profile.ramp_down_pct_per_s
        )
        allowed = rate * dt
        if abs(delta) <= allowed:
            return decision
        limited = decision.previous_pwm + allowed * (1.0 if delta > 0 else -1.0)
        decision.target_pwm = clamp(limited, 0.0, 100.0)
        decision.rate_limited = True
        decision.reason += f" (ramped at {rate:.0f}%/s)"
        return decision

    def _detect_stall(self, snapshot: TelemetrySnapshot, decision: CoolingDecision) -> None:
        rpm = snapshot.fan.rpm
        if (
            rpm is not None
            and decision.target_pwm >= STALL_DUTY_PERCENT
            and rpm < self._limits.stall_rpm
        ):
            self._bus.publish(
                TOPIC_DECISION,
                {
                    "anomaly": Anomaly(
                        kind=AnomalyKind.UNEXPECTED_RPM,
                        summary="Fan is not spinning despite a high duty target",
                        detail=(
                            f"Target {decision.target_pwm:.0f}% but tachometer reads "
                            f"{rpm:.0f} RPM. Check for an obstruction or a wiring fault."
                        ),
                        confidence="medium",
                        value=rpm,
                        unit="RPM",
                    )
                },
            )

    def _apply(self, decision: CoolingDecision) -> None:
        if self._mode is ControllerMode.AUTO:
            self._command("setPwm", pwm=round(decision.target_pwm, 1))

    def _command(self, command: str, **kwargs: object) -> None:
        applier = self._apply_command
        if applier is None:
            return
        try:
            applier(command, **kwargs)  # type: ignore[operator]
        except Exception:  # noqa: BLE001
            _log.exception("failed to send cooling command %s", command)

    def _emit(self, decision: CoolingDecision) -> CoolingDecision:
        self._target = decision.target_pwm
        self._last_update_at = decision.timestamp
        self._last_decision = decision
        self._bus.publish(TOPIC_DECISION, decision)
        return decision


__all__ = [
    "CoolingController",
    "CoolingDecision",
    "SafetyLimits",
    "TOPIC_DECISION",
    "TOPIC_MODE",
    "ABSOLUTE_CRITICAL_C",
    "STALL_DUTY_PERCENT",
]
