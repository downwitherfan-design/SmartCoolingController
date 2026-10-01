"""Unit conversion and value formatting helpers.

All internal values are stored in canonical SI-ish units: degrees Celsius,
percent, megahertz, watts, volts, amperes and revolutions per minute.  This
module converts vendor-specific units on ingest and renders values for the UI.
"""

from __future__ import annotations

from app.core.enums import SensorType

_CANONICAL_UNITS: dict[SensorType, str] = {
    SensorType.TEMPERATURE: "°C",
    SensorType.LOAD: "%",
    SensorType.POWER: "W",
    SensorType.VOLTAGE: "V",
    SensorType.CURRENT: "A",
    SensorType.CLOCK: "MHz",
    SensorType.FAN: "RPM",
    SensorType.DATA: "B",
    SensorType.LEVEL: "%",
    SensorType.THROUGHPUT: "B/s",
    SensorType.TIME: "s",
    SensorType.OTHER: "",
}


def canonical_unit(sensor_type: SensorType) -> str:
    return _CANONICAL_UNITS.get(sensor_type, "")


def to_celsius(value: float, source_unit: str) -> float:
    """Convert a temperature to Celsius from a known source unit."""
    unit = source_unit.strip().lower().replace("°", "").replace(" ", "")
    if unit in {"c", "celsius", "degc"}:
        return value
    if unit in {"f", "fahrenheit", "degf"}:
        return (value - 32.0) * 5.0 / 9.0
    if unit in {"k", "kelvin"}:
        return value - 273.15
    return value


def to_mhz(value: float, source_unit: str) -> float:
    unit = source_unit.strip().lower()
    if unit in {"ghz", "g hz"}:
        return value * 1000.0
    if unit in {"khz"}:
        return value / 1000.0
    if unit in {"hz"}:
        return value / 1_000_000.0
    return value


def to_watts(value: float, source_unit: str) -> float:
    unit = source_unit.strip().lower()
    if unit in {"mw", "milliwatt", "milliwatts"}:
        return value / 1000.0
    if unit in {"kw", "kilowatt", "kilowatts"}:
        return value * 1000.0
    return value


def to_amps(value: float, source_unit: str) -> float:
    unit = source_unit.strip().lower()
    if unit in {"ma", "milliamp", "milliamps"}:
        return value / 1000.0
    return value


def to_volts(value: float, source_unit: str) -> float:
    unit = source_unit.strip().lower()
    if unit in {"mv", "millivolt", "millivolts"}:
        return value / 1000.0
    return value


def normalise_value(sensor_type: SensorType, value: float, source_unit: str) -> float:
    """Convert a raw sensor reading into its canonical unit."""
    if sensor_type is SensorType.TEMPERATURE:
        return to_celsius(value, source_unit)
    if sensor_type is SensorType.CLOCK:
        return to_mhz(value, source_unit)
    if sensor_type is SensorType.POWER:
        return to_watts(value, source_unit)
    if sensor_type is SensorType.CURRENT:
        return to_amps(value, source_unit)
    if sensor_type is SensorType.VOLTAGE:
        return to_volts(value, source_unit)
    return value


def format_value(
    value: float | int | None,
    unit: str = "",
    *,
    decimals: int | None = None,
    unavailable: str = "—",
) -> str:
    """Format a numeric value with a unit, or an em-dash when unavailable."""
    if value is None:
        return unavailable
    number = float(value)
    if decimals is None:
        decimals = 0 if abs(number) >= 100 else (1 if abs(number) >= 10 else 2)
    text = f"{number:,.{decimals}f}"
    return f"{text} {unit}".strip() if unit else text


def format_temperature(value: float | None, *, decimals: int = 1) -> str:
    return format_value(value, "°C", decimals=decimals)


def format_percent(value: float | None, *, decimals: int = 0) -> str:
    return format_value(value, "%", decimals=decimals)


def format_rate(celsius_per_minute: float | None) -> str:
    """Format a thermal rate with an explicit sign, e.g. ``+2.4 °C/min``."""
    if celsius_per_minute is None:
        return "—"
    sign = "+" if celsius_per_minute >= 0 else "−"
    return f"{sign}{abs(celsius_per_minute):.1f} °C/min"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


__all__ = [
    "canonical_unit",
    "clamp",
    "format_percent",
    "format_rate",
    "format_temperature",
    "format_value",
    "normalise_value",
    "to_amps",
    "to_celsius",
    "to_mhz",
    "to_volts",
    "to_watts",
]
