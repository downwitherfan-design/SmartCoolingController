"""Dynamic sensor normalisation and deterministic metric mapping.

Back-ends report sensors with vendor-specific names ("CPU Package",
"Tctl", "GPU Core", "GPU Hotspot", "Composite").  This module converts those
into stable logical metrics (``cpu_temperature``, ``gpu_hotspot_temperature``,
…) using **deterministic priority rules** — never hard-coded indices or
hardware names (spec §10, §11).

The algorithm
-------------
For every metric we define a :class:`MetricRule`: a hardware category, a sensor
type, and regex include/exclude patterns with associated scores.  Each reading
is scored against every rule; the highest-scoring reading wins the metric.
Ties are broken by sensor id so the result is stable across runs.  A few
metrics are *derived* (for example ``cpu_temperature`` prefers the package
sensor, then the core average, then the core maximum).

User overrides are applied on top: a persisted :class:`SensorMapping` pins a
metric to an exact sensor id and outranks every heuristic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.enums import (
    BackendKind,
    DataProvenance,
    HardwareCategory,
    MetricKey,
    MetricState,
    SensorType,
)
from app.core.units import canonical_unit, normalise_value
from app.models.sensors import (
    NormalisedMetric,
    SensorMapping,
    SensorReading,
)

#: Sensor types whose units are physically convertible and should be forced to
#: the canonical unit.  Percentages, data sizes and "other" pass through.
_CONVERTIBLE_TYPES = frozenset(
    {
        SensorType.TEMPERATURE,
        SensorType.CLOCK,
        SensorType.POWER,
        SensorType.CURRENT,
        SensorType.VOLTAGE,
    }
)


@dataclass(frozen=True, slots=True)
class MetricRule:
    """A scoring rule that maps readings onto one logical metric."""

    metric: MetricKey
    category: HardwareCategory | None
    sensor_type: SensorType
    include: tuple[tuple[str, int], ...] = ()
    exclude: tuple[str, ...] = ()
    base_score: int = 0

    def score(self, reading: SensorReading) -> int | None:
        if self.category is not None and reading.hardware_category is not self.category:
            return None
        if reading.sensor_type is not self.sensor_type:
            return None
        name = reading.sensor_name.lower()
        for pattern in self.exclude:
            if re.search(pattern, name):
                return None
        score = self.base_score
        if self.include:
            for pattern, bonus in self.include:
                if re.search(pattern, name):
                    score += bonus
                    break
            else:
                return None
        return score


#: Ordered, documented priority rules.  Pattern bonuses encode the preference
#: order from the specification (package > core average > core max, core >
#: hotspot, etc.).
METRIC_RULES: tuple[MetricRule, ...] = (
    # --- CPU temperatures -------------------------------------------------
    MetricRule(
        MetricKey.CPU_TEMPERATURE_PACKAGE,
        HardwareCategory.CPU,
        SensorType.TEMPERATURE,
        include=((r"package|tctl|tdie", 100),),
        base_score=50,
    ),
    MetricRule(
        MetricKey.CPU_TEMPERATURE_CORE_AVG,
        HardwareCategory.CPU,
        SensorType.TEMPERATURE,
        include=((r"core average|average|avg|cores average", 100),),
        base_score=40,
    ),
    MetricRule(
        MetricKey.CPU_TEMPERATURE_CORE_MAX,
        HardwareCategory.CPU,
        SensorType.TEMPERATURE,
        include=((r"core max|max", 100),),
        base_score=35,
    ),
    # --- CPU other --------------------------------------------------------
    MetricRule(
        MetricKey.CPU_USAGE,
        HardwareCategory.CPU,
        SensorType.LOAD,
        include=((r"total|cpu|package|usage", 100),),
        base_score=40,
    ),
    MetricRule(
        MetricKey.CPU_CLOCK,
        HardwareCategory.CPU,
        SensorType.CLOCK,
        include=((r"core|clock|bus", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.CPU_POWER,
        HardwareCategory.CPU,
        SensorType.POWER,
        include=((r"package|cores|power", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.CPU_VOLTAGE,
        HardwareCategory.CPU,
        SensorType.VOLTAGE,
        include=((r"core|vcore|voltage", 100),),
        base_score=20,
    ),
    # --- GPU --------------------------------------------------------------
    MetricRule(
        MetricKey.GPU_HOTSPOT_TEMPERATURE,
        HardwareCategory.GPU,
        SensorType.TEMPERATURE,
        include=((r"hot\s*spot|hotspot|junction", 100),),
        base_score=60,
    ),
    MetricRule(
        MetricKey.GPU_TEMPERATURE,
        HardwareCategory.GPU,
        SensorType.TEMPERATURE,
        include=((r"core|gpu", 100),),
        exclude=(r"hot\s*spot|hotspot|junction|memory|vram",),
        base_score=50,
    ),
    MetricRule(
        MetricKey.GPU_USAGE,
        HardwareCategory.GPU,
        SensorType.LOAD,
        include=((r"core|gpu|d3d|usage", 100),),
        base_score=40,
    ),
    MetricRule(
        MetricKey.GPU_CLOCK,
        HardwareCategory.GPU,
        SensorType.CLOCK,
        include=((r"core|graphics|gpu", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.GPU_POWER,
        HardwareCategory.GPU,
        SensorType.POWER,
        include=((r"package|gpu|board|power", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.GPU_MEMORY_USAGE,
        HardwareCategory.GPU,
        SensorType.DATA,
        include=((r"memory used|vram|dedicated", 100),),
        base_score=25,
    ),
    MetricRule(
        MetricKey.GPU_MEMORY_TOTAL,
        HardwareCategory.GPU,
        SensorType.DATA,
        include=((r"memory total|vram total", 100),),
        base_score=25,
    ),
    # --- Memory -----------------------------------------------------------
    MetricRule(
        MetricKey.RAM_USAGE,
        HardwareCategory.MEMORY,
        SensorType.LOAD,
        include=((r"load|usage|memory", 100),),
        base_score=40,
    ),
    MetricRule(
        MetricKey.RAM_USED,
        HardwareCategory.MEMORY,
        SensorType.DATA,
        include=((r"used", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.RAM_AVAILABLE,
        HardwareCategory.MEMORY,
        SensorType.DATA,
        include=((r"available|free", 100),),
        base_score=30,
    ),
    # --- Storage ----------------------------------------------------------
    MetricRule(
        MetricKey.STORAGE_TEMPERATURE,
        HardwareCategory.STORAGE,
        SensorType.TEMPERATURE,
        include=((r"temperature|composite|drive", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.STORAGE_ACTIVITY,
        HardwareCategory.STORAGE,
        SensorType.LOAD,
        include=((r"activity|usage|load", 100),),
        base_score=20,
    ),
    # --- Battery ----------------------------------------------------------
    MetricRule(
        MetricKey.BATTERY_LEVEL,
        HardwareCategory.BATTERY,
        SensorType.LEVEL,
        include=((r"level|charge|percent", 100),),
        base_score=40,
    ),
    # --- Cooling pad / fan ------------------------------------------------
    MetricRule(
        MetricKey.FAN_TARGET,
        HardwareCategory.COOLER,
        SensorType.LOAD,
        include=((r"target|set", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.FAN_PWM,
        HardwareCategory.COOLER,
        SensorType.LOAD,
        include=((r"pwm|duty|control", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.FAN_RPM,
        HardwareCategory.COOLER,
        SensorType.FAN,
        include=((r"fan|rpm|speed", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.FAN_VOLTAGE,
        HardwareCategory.COOLER,
        SensorType.VOLTAGE,
        include=((r"fan|voltage|12v", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.FAN_CURRENT,
        HardwareCategory.COOLER,
        SensorType.CURRENT,
        include=((r"fan|current|ina", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.FAN_POWER,
        HardwareCategory.COOLER,
        SensorType.POWER,
        include=((r"fan|power", 100),),
        base_score=30,
    ),
    MetricRule(
        MetricKey.PAD_TEMPERATURE,
        HardwareCategory.COOLER,
        SensorType.TEMPERATURE,
        include=((r"pad|ds18b20|ambient", 100),),
        base_score=25,
    ),
)

#: Every metric the normaliser will emit, so the UI can render "—" honestly.
ALL_METRICS: tuple[MetricKey, ...] = (
    *dict.fromkeys(rule.metric for rule in METRIC_RULES),
    MetricKey.CPU_TEMPERATURE,
    MetricKey.RAM_TOTAL,
)

#: Derived-metric preference chains, highest priority first.
_CPU_TEMPERATURE_CHAIN = (
    MetricKey.CPU_TEMPERATURE_PACKAGE,
    MetricKey.CPU_TEMPERATURE_CORE_AVG,
    MetricKey.CPU_TEMPERATURE_CORE_MAX,
)


@dataclass(slots=True)
class NormalisationResult:
    """Normalised metrics plus the winning reading for each."""

    metrics: dict[MetricKey, NormalisedMetric] = field(default_factory=dict)
    winners: dict[MetricKey, SensorReading] = field(default_factory=dict)
    #: Metrics that were requested by rules but had no candidate reading.
    missing: list[MetricKey] = field(default_factory=list)


class SensorNormaliser:
    """Maps raw readings to logical metrics using deterministic priority."""

    def __init__(self, overrides: dict[MetricKey, str] | None = None) -> None:
        #: metric -> exact sensor id, from persisted user mappings.
        self._overrides: dict[MetricKey, str] = dict(overrides or {})

    def set_overrides(self, mappings: list[SensorMapping]) -> None:
        self._overrides = {
            mapping.metric: mapping.sensor_id
            for mapping in mappings
            if mapping.sensor_id
        }

    def normalise(self, readings: list[SensorReading]) -> NormalisationResult:
        result = NormalisationResult()
        by_id = {reading.sensor_id: reading for reading in readings}

        best: dict[MetricKey, tuple[int, SensorReading]] = {}
        for reading in readings:
            if reading.unavailable:
                continue
            for rule in METRIC_RULES:
                score = rule.score(reading)
                if score is None:
                    continue
                current = best.get(rule.metric)
                if current is None or self._better(score, reading, current):
                    best[rule.metric] = (score, reading)

        # User overrides win outright.
        for metric, sensor_id in self._overrides.items():
            override = by_id.get(sensor_id)
            if override is not None and not override.unavailable:
                best[metric] = (10_000, override)

        for metric in ALL_METRICS:
            winner = best.get(metric)
            if winner is None:
                result.metrics[metric] = NormalisedMetric.unavailable(
                    metric, canonical_unit(_metric_sensor_type(metric))
                )
                result.missing.append(metric)
                continue
            reading = winner[1]
            result.winners[metric] = reading
            result.metrics[metric] = self._to_metric(metric, reading)

        # --- derived metrics ---------------------------------------------
        cpu_temperature = self._derive_cpu_temperature(result)
        result.metrics[MetricKey.CPU_TEMPERATURE] = cpu_temperature
        if cpu_temperature.value is None:
            result.missing.append(MetricKey.CPU_TEMPERATURE)

        ram_total = _find_total(readings, HardwareCategory.MEMORY)
        if ram_total is not None:
            result.metrics[MetricKey.RAM_TOTAL] = self._to_metric(
                MetricKey.RAM_TOTAL, ram_total
            )
        else:
            result.metrics[MetricKey.RAM_TOTAL] = NormalisedMetric.unavailable(
                MetricKey.RAM_TOTAL, "MiB"
            )

        result.missing = sorted(set(result.missing))
        return result

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _better(score: int, reading: SensorReading, current: tuple[int, SensorReading]) -> bool:
        current_score, current_reading = current
        if score != current_score:
            return score > current_score
        # Deterministic tie-break: prefer the shorter, more specific name then
        # the lexicographically smaller sensor id.
        return (len(reading.sensor_name), reading.sensor_id) < (
            len(current_reading.sensor_name),
            current_reading.sensor_id,
        )

    def _to_metric(self, metric: MetricKey, reading: SensorReading) -> NormalisedMetric:
        sensor_type = _metric_sensor_type(metric)
        if sensor_type in _CONVERTIBLE_TYPES:
            value = normalise_value(sensor_type, reading.value, reading.unit)
            unit = canonical_unit(sensor_type)
        else:
            # Percentages, data sizes and other pass-through quantities keep
            # the unit the back-end reported so the label always matches.
            value = reading.value
            unit = reading.unit or canonical_unit(sensor_type)
        state = MetricState.SIMULATED if reading.backend is BackendKind.SIMULATION else MetricState.VALID
        provenance = (
            DataProvenance.SIMULATED
            if reading.backend is BackendKind.SIMULATION
            else DataProvenance.MEASURED
        )
        return NormalisedMetric(
            key=metric,
            value=value,
            unit=unit,
            state=state,
            provenance=provenance,
            source_sensor_id=reading.sensor_id,
            source_backend=reading.backend,
            source_hardware=reading.hardware_name,
            source_sensor_name=reading.sensor_name,
            timestamp=reading.timestamp,
        )

    def _derive_cpu_temperature(self, result: NormalisationResult) -> NormalisedMetric:
        for key in _CPU_TEMPERATURE_CHAIN:
            metric = result.metrics.get(key)
            if metric is not None and metric.available:
                return NormalisedMetric(
                    key=MetricKey.CPU_TEMPERATURE,
                    value=metric.value,
                    unit="°C",
                    state=metric.state,
                    provenance=(
                        metric.provenance
                        if metric.provenance is DataProvenance.SIMULATED
                        else DataProvenance.DERIVED
                    ),
                    source_sensor_id=metric.source_sensor_id,
                    source_backend=metric.source_backend,
                    source_hardware=metric.source_hardware,
                    source_sensor_name=metric.source_sensor_name,
                    timestamp=metric.timestamp,
                )
        # No CPU temperature sensor was found at all.
        return NormalisedMetric.unavailable(
            MetricKey.CPU_TEMPERATURE, "°C", state=MetricState.UNAVAILABLE
        )


def _metric_sensor_type(metric: MetricKey) -> SensorType:
    mapping = {
        MetricKey.CPU_TEMPERATURE: SensorType.TEMPERATURE,
        MetricKey.CPU_TEMPERATURE_PACKAGE: SensorType.TEMPERATURE,
        MetricKey.CPU_TEMPERATURE_CORE_AVG: SensorType.TEMPERATURE,
        MetricKey.CPU_TEMPERATURE_CORE_MAX: SensorType.TEMPERATURE,
        MetricKey.CPU_USAGE: SensorType.LOAD,
        MetricKey.CPU_CLOCK: SensorType.CLOCK,
        MetricKey.CPU_POWER: SensorType.POWER,
        MetricKey.CPU_VOLTAGE: SensorType.VOLTAGE,
        MetricKey.GPU_TEMPERATURE: SensorType.TEMPERATURE,
        MetricKey.GPU_HOTSPOT_TEMPERATURE: SensorType.TEMPERATURE,
        MetricKey.GPU_USAGE: SensorType.LOAD,
        MetricKey.GPU_CLOCK: SensorType.CLOCK,
        MetricKey.GPU_POWER: SensorType.POWER,
        MetricKey.GPU_MEMORY_USAGE: SensorType.DATA,
        MetricKey.GPU_MEMORY_TOTAL: SensorType.DATA,
        MetricKey.RAM_USAGE: SensorType.LOAD,
        MetricKey.RAM_USED: SensorType.DATA,
        MetricKey.RAM_AVAILABLE: SensorType.DATA,
        MetricKey.RAM_TOTAL: SensorType.DATA,
        MetricKey.STORAGE_TEMPERATURE: SensorType.TEMPERATURE,
        MetricKey.STORAGE_ACTIVITY: SensorType.LOAD,
        MetricKey.BATTERY_LEVEL: SensorType.LEVEL,
        MetricKey.BATTERY_POWER: SensorType.POWER,
        MetricKey.BATTERY_VOLTAGE: SensorType.VOLTAGE,
        MetricKey.BATTERY_STATE: SensorType.OTHER,
        MetricKey.FAN_TARGET: SensorType.LOAD,
        MetricKey.FAN_PWM: SensorType.LOAD,
        MetricKey.FAN_RPM: SensorType.FAN,
        MetricKey.FAN_VOLTAGE: SensorType.VOLTAGE,
        MetricKey.FAN_CURRENT: SensorType.CURRENT,
        MetricKey.FAN_POWER: SensorType.POWER,
        MetricKey.PAD_TEMPERATURE: SensorType.TEMPERATURE,
        MetricKey.PAD_CURRENT: SensorType.CURRENT,
        MetricKey.PAD_VOLTAGE: SensorType.VOLTAGE,
        MetricKey.PAD_POWER: SensorType.POWER,
    }
    return mapping.get(metric, SensorType.OTHER)


def _find_total(readings: list[SensorReading], category: HardwareCategory) -> SensorReading | None:
    for reading in readings:
        if reading.hardware_category is category and "total" in reading.sensor_name.lower():
            return reading
    return None


__all__ = [
    "ALL_METRICS",
    "METRIC_RULES",
    "MetricRule",
    "NormalisationResult",
    "SensorNormaliser",
]
