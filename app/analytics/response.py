"""Cooling response and efficiency analysis (spec §20, §21, §42, §101, §102).

These are *application heuristics*.  They estimate how the system responded to
a change in fan duty by comparing the temperature before and after a fan
increase.  Nothing here claims laboratory-grade measurement; every result is
labelled ``Application Heuristic`` in the UI.

The estimator watches the recommended/target fan duty.  When duty rises by at
least :data:`MIN_DUTY_STEP` percent it records the pre-change temperature and
starts a window; when the temperature slope flattens (or the window expires) it
reports the observed delta and a stabilisation time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.clock import utc_now
from app.core.enums import DataProvenance, FanResponseState
from app.core.units import clamp
from app.models.telemetry import TelemetrySnapshot

#: A duty change smaller than this is treated as noise.
MIN_DUTY_STEP = 8.0
#: How long to watch after a fan increase before giving up.
MAX_RESPONSE_WINDOW_S = 180.0
#: Slope magnitude (°C/min) below which the temperature is considered settled.
SETTLED_BAND_C_PER_MIN = 0.4


@dataclass(slots=True)
class CoolingResponse:
    """One observed cooling response to a fan increase."""

    before_temperature_c: float
    after_temperature_c: float | None
    duty_before: float
    duty_after: float
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    stabilization_s: float | None = None
    recovery_s: float | None = None
    provenance: DataProvenance = DataProvenance.ESTIMATED

    @property
    def delta_c(self) -> float | None:
        if self.after_temperature_c is None:
            return None
        return self.after_temperature_c - self.before_temperature_c

    def to_dict(self) -> dict[str, object]:
        return {
            "before_temperature_c": self.before_temperature_c,
            "after_temperature_c": self.after_temperature_c,
            "delta_c": self.delta_c,
            "duty_before": self.duty_before,
            "duty_after": self.duty_after,
            "stabilization_s": self.stabilization_s,
            "recovery_s": self.recovery_s,
            "provenance": self.provenance.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


@dataclass(slots=True)
class CoolingEfficiency:
    """A bounded 0–100 heuristic score (spec §21).

    Formula (documented deliberately):

        efficiency = 100 * (temperature_reduction_credit * 0.6
                            + stabilization_credit * 0.2
                            + airflow_credit * 0.2)

    where

    * ``temperature_reduction_credit`` = clamp(delta_c / 10, 0, 1)
    * ``stabilization_credit``        = clamp(1 - stabilization_s / 120, 0, 1)
    * ``airflow_credit``              = clamp(rpm / 5000, 0, 1)

    It is a rough comparison aid, not a physical measurement.
    """

    score: float | None
    temperature_reduction_credit: float = 0.0
    stabilization_credit: float = 0.0
    airflow_credit: float = 0.0
    provenance: DataProvenance = DataProvenance.ESTIMATED

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "temperature_reduction_credit": self.temperature_reduction_credit,
            "stabilization_credit": self.stabilization_credit,
            "airflow_credit": self.airflow_credit,
            "provenance": self.provenance.value,
        }


class CoolingResponseAnalyser:
    """Watches fan duty changes and estimates their thermal effect."""

    def __init__(self) -> None:
        self._last_duty: float | None = None
        self._active: CoolingResponse | None = None
        self._last_slope: float | None = None
        self._history: list[CoolingResponse] = []

    @property
    def history(self) -> list[CoolingResponse]:
        return list(self._history)

    @property
    def active(self) -> CoolingResponse | None:
        return self._active

    def observe(
        self,
        snapshot: TelemetrySnapshot,
        *,
        slope_c_per_min: float | None,
    ) -> CoolingResponse | None:
        """Feed a snapshot; returns a completed response when one finishes."""
        temperature = snapshot.cpu.temperature
        duty = snapshot.fan.target_percent
        if duty is None:
            duty = snapshot.thermal.recommendation_pwm
        if temperature is None or duty is None:
            self._last_slope = slope_c_per_min
            return None

        completed: CoolingResponse | None = None

        if self._active is not None:
            elapsed = (utc_now() - self._active.started_at).total_seconds()
            settled = slope_c_per_min is not None and abs(slope_c_per_min) <= SETTLED_BAND_C_PER_MIN
            if settled or elapsed >= MAX_RESPONSE_WINDOW_S:
                self._active.after_temperature_c = temperature
                self._active.ended_at = utc_now()
                self._active.stabilization_s = elapsed
                self._history.append(self._active)
                completed = self._active
                self._active = None
        elif (
            self._last_duty is not None
            and duty - self._last_duty >= MIN_DUTY_STEP
        ):
            self._active = CoolingResponse(
                before_temperature_c=temperature,
                after_temperature_c=None,
                duty_before=self._last_duty,
                duty_after=duty,
            )

        self._last_duty = duty
        self._last_slope = slope_c_per_min
        return completed

    def efficiency(
        self,
        response: CoolingResponse | None,
        *,
        rpm: float | None,
    ) -> CoolingEfficiency:
        if response is None or response.delta_c is None:
            return CoolingEfficiency(score=None)
        reduction_credit = clamp(max(0.0, -response.delta_c) / 10.0, 0.0, 1.0)
        stabilization = response.stabilization_s or MAX_RESPONSE_WINDOW_S
        stabilization_credit = clamp(1.0 - stabilization / 120.0, 0.0, 1.0)
        airflow_credit = clamp((rpm or 0.0) / 5000.0, 0.0, 1.0)
        score = 100.0 * (
            reduction_credit * 0.6 + stabilization_credit * 0.2 + airflow_credit * 0.2
        )
        return CoolingEfficiency(
            score=round(score, 1),
            temperature_reduction_credit=round(reduction_credit, 3),
            stabilization_credit=round(stabilization_credit, 3),
            airflow_credit=round(airflow_credit, 3),
        )

    def fan_response_state(
        self,
        *,
        target_pwm: float | None,
        rpm: float | None,
        expected_rpm: float | None,
    ) -> FanResponseState:
        """Compare actual RPM to the RPM expected for the current duty."""
        if target_pwm is None or rpm is None:
            return FanResponseState.UNKNOWN
        if target_pwm < 5.0:
            return FanResponseState.NORMAL if rpm < 200 else FanResponseState.UNKNOWN
        if expected_rpm is None:
            return FanResponseState.UNKNOWN
        ratio = rpm / max(1.0, expected_rpm)
        if ratio >= 0.75:
            return FanResponseState.NORMAL
        if ratio >= 0.3:
            return FanResponseState.LOWER_THAN_EXPECTED
        return FanResponseState.NO_RESPONSE


__all__ = [
    "CoolingResponse",
    "CoolingEfficiency",
    "CoolingResponseAnalyser",
    "MIN_DUTY_STEP",
    "MAX_RESPONSE_WINDOW_S",
    "SETTLED_BAND_C_PER_MIN",
]
