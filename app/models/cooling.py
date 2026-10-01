"""Cooling domain models: fan curves, profiles and calibration (spec §39-§42).

A :class:`FanCurve` is a monotonic piecewise-linear map from a thermal input
(°C) to a fan duty (%).  Validation is strict: unordered points, out-of-range
temperatures or non-monotonic duties are rejected so a user can never author a
curve that would confuse the controller or the safety logic (spec §237).
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import CoolingProfileKind, FanControlSource
from app.core.errors import ProfileValidationError

#: Hard bounds shared by the editor, the validator and the controller.
MIN_TEMPERATURE_C = 0.0
MAX_TEMPERATURE_C = 120.0
MIN_PWM = 0.0
MAX_PWM = 100.0

CURVE_SCHEMA_VERSION = 1
PROFILE_SCHEMA_VERSION = 1


@dataclass(slots=True)
class FanCurvePoint:
    temperature_c: float
    duty_percent: float

    def to_dict(self) -> dict[str, float]:
        return {
            "temperature_c": round(self.temperature_c, 3),
            "duty_percent": round(self.duty_percent, 3),
        }


@dataclass(slots=True)
class FanCurve:
    """A validated, monotonic temperature -> duty curve."""

    name: str = "Default"
    points: list[FanCurvePoint] = field(
        default_factory=lambda: [
            FanCurvePoint(30.0, 0.0),
            FanCurvePoint(50.0, 25.0),
            FanCurvePoint(65.0, 45.0),
            FanCurvePoint(75.0, 65.0),
            FanCurvePoint(85.0, 85.0),
            FanCurvePoint(95.0, 100.0),
        ]
    )
    schema_version: int = CURVE_SCHEMA_VERSION

    def validate(self) -> None:
        """Raise :class:`ProfileValidationError` when the curve is unsafe."""
        if len(self.points) < 2:
            raise ProfileValidationError("a fan curve needs at least two points")
        ordered = sorted(self.points, key=lambda p: p.temperature_c)
        seen: set[float] = set()
        previous_duty = -1.0
        for point in ordered:
            if not (MIN_TEMPERATURE_C <= point.temperature_c <= MAX_TEMPERATURE_C):
                raise ProfileValidationError(
                    f"temperature {point.temperature_c}°C is out of range"
                )
            if not (MIN_PWM <= point.duty_percent <= MAX_PWM):
                raise ProfileValidationError(
                    f"duty {point.duty_percent}% is out of range"
                )
            if point.temperature_c in seen:
                raise ProfileValidationError(
                    f"duplicate temperature {point.temperature_c}°C"
                )
            seen.add(point.temperature_c)
            if point.duty_percent < previous_duty - 1e-9:
                raise ProfileValidationError(
                    "fan curve must be monotonically non-decreasing"
                )
            previous_duty = point.duty_percent

    def normalised(self) -> FanCurve:
        """Return a validated copy with points sorted by temperature."""
        self.validate()
        return FanCurve(
            name=self.name,
            points=sorted(self.points, key=lambda p: p.temperature_c),
            schema_version=self.schema_version,
        )

    def duty_for(self, temperature_c: float, *, min_pwm: float = 0.0, max_pwm: float = 100.0) -> float:
        """Linearly interpolate the duty for a temperature.

        Values below the first point clamp to the first duty; values above the
        last point clamp to the last duty.  The result is clamped to the
        ``[min_pwm, max_pwm]`` envelope.
        """
        curve = self.normalised()
        temps = [p.temperature_c for p in curve.points]
        if temperature_c <= temps[0]:
            duty = curve.points[0].duty_percent
        elif temperature_c >= temps[-1]:
            duty = curve.points[-1].duty_percent
        else:
            index = bisect.bisect_left(temps, temperature_c)
            low, high = curve.points[index - 1], curve.points[index]
            span = high.temperature_c - low.temperature_c
            ratio = 0.0 if span <= 0 else (temperature_c - low.temperature_c) / span
            duty = low.duty_percent + ratio * (high.duty_percent - low.duty_percent)
        return max(min_pwm, min(max_pwm, duty))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "points": [p.to_dict() for p in sorted(self.points, key=lambda p: p.temperature_c)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FanCurve:
        points = data.get("points")
        if not isinstance(points, list):
            raise ProfileValidationError("curve 'points' must be a list")
        try:
            parsed = [
                FanCurvePoint(float(p["temperature_c"]), float(p["duty_percent"]))
                for p in points
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileValidationError(f"invalid curve point: {exc}") from exc
        curve = cls(
            name=str(data.get("name", "Imported")),
            points=parsed,
            schema_version=int(data.get("schema_version", CURVE_SCHEMA_VERSION)),
        )
        return curve.normalised()

    @classmethod
    def from_points(cls, points: Iterable[tuple[float, float]], name: str = "Default") -> FanCurve:
        return cls(name=name, points=[FanCurvePoint(t, d) for t, d in points]).normalised()


@dataclass(slots=True)
class CoolingProfile:
    """A named cooling strategy (spec §39)."""

    key: str
    name: str
    kind: CoolingProfileKind = CoolingProfileKind.CUSTOM
    curve: FanCurve = field(default_factory=FanCurve)
    source: FanControlSource = FanControlSource.MAX
    min_pwm: float = 0.0
    max_pwm: float = 100.0
    hysteresis_c: float = 3.0
    ramp_up_pct_per_s: float = 8.0
    ramp_down_pct_per_s: float = 4.0
    thermal_weight_cpu: float = 0.6
    thermal_weight_gpu: float = 0.4
    load_weight: float = 0.1
    power_weight: float = 0.2
    builtin: bool = False
    schema_version: int = PROFILE_SCHEMA_VERSION

    def validate(self) -> None:
        self.curve.validate()
        if not (MIN_PWM <= self.min_pwm <= self.max_pwm <= MAX_PWM):
            raise ProfileValidationError("invalid PWM envelope")
        if self.hysteresis_c < 0:
            raise ProfileValidationError("hysteresis must be non-negative")
        if self.ramp_up_pct_per_s <= 0 or self.ramp_down_pct_per_s <= 0:
            raise ProfileValidationError("ramp rates must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "key": self.key,
            "name": self.name,
            "kind": self.kind.value,
            "source": self.source.value,
            "min_pwm": self.min_pwm,
            "max_pwm": self.max_pwm,
            "hysteresis_c": self.hysteresis_c,
            "ramp_up_pct_per_s": self.ramp_up_pct_per_s,
            "ramp_down_pct_per_s": self.ramp_down_pct_per_s,
            "thermal_weight_cpu": self.thermal_weight_cpu,
            "thermal_weight_gpu": self.thermal_weight_gpu,
            "load_weight": self.load_weight,
            "power_weight": self.power_weight,
            "builtin": self.builtin,
            "curve": self.curve.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoolingProfile:
        curve = FanCurve.from_dict(data.get("curve", {}))
        profile = cls(
            key=str(data.get("key", "")),
            name=str(data.get("name", "Imported")),
            kind=CoolingProfileKind.coerce(data.get("kind"), CoolingProfileKind.CUSTOM),
            curve=curve,
            source=FanControlSource.coerce(data.get("source"), FanControlSource.MAX),
            min_pwm=float(data.get("min_pwm", 0.0)),
            max_pwm=float(data.get("max_pwm", 100.0)),
            hysteresis_c=float(data.get("hysteresis_c", 3.0)),
            ramp_up_pct_per_s=float(data.get("ramp_up_pct_per_s", 8.0)),
            ramp_down_pct_per_s=float(data.get("ramp_down_pct_per_s", 4.0)),
            thermal_weight_cpu=float(data.get("thermal_weight_cpu", 0.6)),
            thermal_weight_gpu=float(data.get("thermal_weight_gpu", 0.4)),
            load_weight=float(data.get("load_weight", 0.1)),
            power_weight=float(data.get("power_weight", 0.2)),
            builtin=False,
            schema_version=int(data.get("schema_version", PROFILE_SCHEMA_VERSION)),
        )
        profile.validate()
        return profile


@dataclass(slots=True)
class FanCalibrationPoint:
    pwm_percent: float
    rpm: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_w: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "pwm_percent": self.pwm_percent,
            "rpm": self.rpm,
            "voltage_v": self.voltage_v,
            "current_a": self.current_a,
            "power_w": self.power_w,
        }


@dataclass(slots=True)
class FanCalibration:
    """Persisted PWM -> response relationship (spec §41, §42, §121)."""

    device_id: str = ""
    fan_model: str = ""
    points: list[FanCalibrationPoint] = field(default_factory=list)
    minimum_reliable_pwm: float | None = None
    maximum_configured_pwm: float | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "fan_model": self.fan_model,
            "points": [p.to_dict() for p in self.points],
            "minimum_reliable_pwm": self.minimum_reliable_pwm,
            "maximum_configured_pwm": self.maximum_configured_pwm,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FanCalibration:
        points = [
            FanCalibrationPoint(
                pwm_percent=float(p.get("pwm_percent", 0.0)),
                rpm=_opt_float(p.get("rpm")),
                voltage_v=_opt_float(p.get("voltage_v")),
                current_a=_opt_float(p.get("current_a")),
                power_w=_opt_float(p.get("power_w")),
            )
            for p in data.get("points", [])
            if isinstance(p, dict)
        ]
        return cls(
            device_id=str(data.get("device_id", "")),
            fan_model=str(data.get("fan_model", "")),
            points=points,
            minimum_reliable_pwm=_opt_float(data.get("minimum_reliable_pwm")),
            maximum_configured_pwm=_opt_float(data.get("maximum_configured_pwm")),
            created_at=str(data.get("created_at", "")),
        )


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CURVE_SCHEMA_VERSION",
    "MAX_PWM",
    "MAX_TEMPERATURE_C",
    "MIN_PWM",
    "MIN_TEMPERATURE_C",
    "PROFILE_SCHEMA_VERSION",
    "CoolingProfile",
    "FanCalibration",
    "FanCalibrationPoint",
    "FanCurve",
    "FanCurvePoint",
]
