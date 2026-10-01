"""Unit tests for the fan-curve maths (spec §18, §19)."""

from __future__ import annotations

import pytest

from app.models.cooling import FanCurve, FanCurvePoint


def _curve(points: list[tuple[float, float]]) -> FanCurve:
    return FanCurve(name="t", points=[FanCurvePoint(t, d) for t, d in points])


def test_duty_below_first_point_clamps_to_first_duty() -> None:
    curve = _curve([(40, 10), (60, 50), (80, 100)])
    assert curve.duty_for(20) == pytest.approx(10)


def test_duty_above_last_point_clamps_to_last_duty() -> None:
    curve = _curve([(40, 10), (60, 50), (80, 100)])
    assert curve.duty_for(120) == pytest.approx(100)


def test_duty_interpolates_linearly_between_points() -> None:
    curve = _curve([(40, 0), (80, 100)])
    assert curve.duty_for(60) == pytest.approx(50)
    assert curve.duty_for(50) == pytest.approx(25)
    assert curve.duty_for(70) == pytest.approx(75)


def test_duty_hits_exact_knots() -> None:
    curve = _curve([(30, 20), (50, 40), (70, 80), (90, 100)])
    for temp, duty in [(30, 20), (50, 40), (70, 80), (90, 100)]:
        assert curve.duty_for(temp) == pytest.approx(duty)


def test_duty_respects_min_and_max_envelope() -> None:
    curve = _curve([(40, 0), (80, 100)])
    # A curve that would ask for 0 must still honour a 30% floor.
    assert curve.duty_for(40, min_pwm=30) == pytest.approx(30)
    # A curve that would ask for 100 must still honour an 80% ceiling.
    assert curve.duty_for(80, max_pwm=80) == pytest.approx(80)


def test_duty_is_monotonic_for_a_monotonic_curve() -> None:
    curve = _curve([(30, 0), (50, 20), (65, 45), (80, 75), (95, 100)])
    previous = -1.0
    for temp in range(20, 105):
        duty = curve.duty_for(float(temp))
        assert duty >= previous
        previous = duty


def test_unsorted_points_are_normalised_before_interpolation() -> None:
    curve = _curve([(80, 100), (40, 0), (60, 50)])
    assert [p.temperature_c for p in curve.normalised().points] == [40, 60, 80]
    assert curve.duty_for(60) == pytest.approx(50)


def test_curve_round_trips_through_dict() -> None:
    curve = _curve([(35, 0), (55, 25), (75, 70), (95, 100)])
    restored = FanCurve.from_dict(curve.to_dict())
    assert restored.to_dict() == curve.to_dict()


def test_from_points_helper_builds_expected_curve() -> None:
    curve = FanCurve.from_points([(40, 0), (80, 100)], name="helper")
    assert curve.name == "helper"
    assert curve.duty_for(60) == pytest.approx(50)


def test_single_point_curve_is_rejected() -> None:
    from app.core.errors import ProfileValidationError

    with pytest.raises(ProfileValidationError):
        _curve([(50, 42)]).validate()


def test_duplicate_temperature_is_rejected() -> None:
    from app.core.errors import ProfileValidationError

    with pytest.raises(ProfileValidationError):
        _curve([(50, 10), (50, 20)]).validate()


def test_non_monotonic_curve_is_rejected() -> None:
    from app.core.errors import ProfileValidationError

    with pytest.raises(ProfileValidationError):
        _curve([(40, 50), (60, 20), (80, 90)]).validate()


def test_out_of_range_duty_is_rejected() -> None:
    from app.core.errors import ProfileValidationError

    with pytest.raises(ProfileValidationError):
        _curve([(40, 0), (80, 140)]).validate()
