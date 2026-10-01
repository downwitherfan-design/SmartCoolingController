"""Thermal analytics (spec §17, §18, §20, §21).

Everything computed here is *derived* and is labelled as such.  The analyser
maintains bounded sliding windows for CPU and GPU temperature so it can report
min/max/average, a moving average, a least-squares thermal rate in °C/min and a
hysteretic thermal state, without unbounded memory growth.

Hysteresis
----------
States escalate as soon as a threshold is crossed, but only de-escalate once
the temperature falls ``hysteresis`` degrees below the threshold that produced
the state.  This prevents the fan and the UI from oscillating around a
threshold (spec §17).

The analyser also computes a *weighted thermal input* and, when a cooling
profile is supplied, the corresponding fan recommendation.  Applying the ramp
limit is the cooling service's job; here we only compute the target.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.buffers import SlidingWindow
from app.core.enums import (
    AnomalyKind,
    DataProvenance,
    FanControlSource,
    ThermalState,
    ThermalTrend,
)
from app.models.alerts import Anomaly
from app.models.cooling import CoolingProfile
from app.models.telemetry import TelemetrySnapshot, ThermalAnalysis


@dataclass(slots=True)
class ThermalThresholds:
    """Temperature boundaries (in °C) that define the thermal states."""

    cool_below: float = 45.0
    cpu_warm: float = 70.0
    cpu_high: float = 80.0
    cpu_critical: float = 92.0
    gpu_warm: float = 68.0
    gpu_high: float = 78.0
    gpu_critical: float = 90.0

    def for_cpu(self, temperature: float, hysteresis: float, previous: ThermalState) -> ThermalState:
        return _classify(
            temperature,
            self.cool_below,
            self.cpu_warm,
            self.cpu_high,
            self.cpu_critical,
            hysteresis,
            previous,
        )

    def for_gpu(self, temperature: float, hysteresis: float, previous: ThermalState) -> ThermalState:
        return _classify(
            temperature,
            self.cool_below,
            self.gpu_warm,
            self.gpu_high,
            self.gpu_critical,
            hysteresis,
            previous,
        )


_ORDER = [
    ThermalState.COOL,
    ThermalState.NORMAL,
    ThermalState.WARM,
    ThermalState.HIGH,
    ThermalState.CRITICAL,
]


def _classify(
    temperature: float,
    cool_below: float,
    warm: float,
    high: float,
    critical: float,
    hysteresis: float,
    previous: ThermalState,
) -> ThermalState:
    if temperature >= critical:
        target = ThermalState.CRITICAL
    elif temperature >= high:
        target = ThermalState.HIGH
    elif temperature >= warm:
        target = ThermalState.WARM
    elif temperature >= cool_below:
        target = ThermalState.NORMAL
    else:
        target = ThermalState.COOL

    # De-escalate only with hysteresis margin.
    if _ORDER.index(target) >= _ORDER.index(previous):
        return target
    boundaries = {
        ThermalState.CRITICAL: critical,
        ThermalState.HIGH: high,
        ThermalState.WARM: warm,
        ThermalState.NORMAL: cool_below,
        ThermalState.COOL: -1e9,
    }
    boundary = boundaries[previous]
    if temperature >= boundary - hysteresis:
        return previous
    return target


class ThermalAnalyser:
    """Computes :class:`ThermalAnalysis` for each snapshot."""

    def __init__(
        self,
        thresholds: ThermalThresholds | None = None,
        *,
        window_capacity: int = 4096,
        hysteresis_c: float = 3.0,
        trend_window_s: float = 60.0,
        rate_window_s: float = 30.0,
        stable_band_c_per_min: float = 0.6,
    ) -> None:
        self.thresholds = thresholds or ThermalThresholds()
        self.hysteresis_c = hysteresis_c
        self._trend_window_s = trend_window_s
        self._rate_window_s = rate_window_s
        self._stable_band = stable_band_c_per_min
        self._cpu = SlidingWindow(window_capacity)
        self._gpu = SlidingWindow(window_capacity)
        self._cpu_state = ThermalState.NORMAL
        self._gpu_state = ThermalState.NORMAL
        self._time_above_threshold_s = 0.0
        self._time_in_state_s: dict[ThermalState, float] = dict.fromkeys(_ORDER, 0.0)
        self._last_timestamp: float | None = None
        self._profile: CoolingProfile | None = None
        self._last_anomalies: list[Anomaly] = []
        self._last_cpu_rate: float | None = None

    # -- configuration -----------------------------------------------------

    def set_profile(self, profile: CoolingProfile | None) -> None:
        self._profile = profile

    def set_hysteresis(self, hysteresis_c: float) -> None:
        self.hysteresis_c = max(0.0, float(hysteresis_c))

    @property
    def last_anomalies(self) -> list[Anomaly]:
        return list(self._last_anomalies)

    @property
    def time_above_threshold_s(self) -> float:
        return self._time_above_threshold_s

    @property
    def time_in_state_s(self) -> dict[ThermalState, float]:
        return dict(self._time_in_state_s)

    # -- analysis ----------------------------------------------------------

    def analyse(self, snapshot: TelemetrySnapshot) -> ThermalAnalysis:
        timestamp = snapshot.timestamp.timestamp()
        dt = 0.0 if self._last_timestamp is None else max(0.0, timestamp - self._last_timestamp)
        self._last_timestamp = timestamp

        cpu_temp = snapshot.cpu.temperature
        gpu_temp = snapshot.gpu.temperature
        if cpu_temp is not None:
            self._cpu.add(timestamp, cpu_temp)
        if gpu_temp is not None:
            self._gpu.add(timestamp, gpu_temp)

        if cpu_temp is not None:
            self._cpu_state = self.thresholds.for_cpu(
                cpu_temp, self.hysteresis_c, self._cpu_state
            )
        if gpu_temp is not None:
            self._gpu_state = self.thresholds.for_gpu(
                gpu_temp, self.hysteresis_c, self._gpu_state
            )

        # Time accounting.
        self._time_in_state_s[self._cpu_state] += dt
        if (cpu_temp is not None and cpu_temp >= self.thresholds.cpu_warm) or (gpu_temp is not None and gpu_temp >= self.thresholds.gpu_warm):
            self._time_above_threshold_s += dt

        cpu_rate = self._cpu.slope_per_minute(self._rate_window_s)
        gpu_rate = self._gpu.slope_per_minute(self._rate_window_s)
        self._last_cpu_rate = cpu_rate

        analysis = ThermalAnalysis(
            cpu_state=self._cpu_state,
            gpu_state=self._gpu_state,
            cpu_trend=_trend(cpu_rate, self._stable_band),
            gpu_trend=_trend(gpu_rate, self._stable_band),
            cpu_rate_c_per_min=cpu_rate,
            gpu_rate_c_per_min=gpu_rate,
            cpu_average_c=self._cpu.mean(),
            cpu_min_c=self._cpu.minimum(),
            cpu_max_c=self._cpu.maximum(),
            gpu_average_c=self._gpu.mean(),
            gpu_min_c=self._gpu.minimum(),
            gpu_max_c=self._gpu.maximum(),
            provenance=DataProvenance.DERIVED,
        )
        analysis.headroom_c = self._headroom(cpu_temp, gpu_temp)
        analysis.weighted_input_c = self._weighted_input(
            cpu_temp, gpu_temp, snapshot
        )
        analysis.recommendation_pwm = self._recommendation(analysis.weighted_input_c)
        snapshot.cpu.thermal_state = self._cpu_state
        snapshot.gpu.thermal_state = self._gpu_state

        self._last_anomalies = self._detect_anomalies(snapshot, cpu_rate, gpu_rate)
        return analysis

    # -- helpers -----------------------------------------------------------

    def _headroom(self, cpu_temp: float | None, gpu_temp: float | None) -> float | None:
        candidates: list[float] = []
        if cpu_temp is not None:
            candidates.append(self.thresholds.cpu_critical - cpu_temp)
        if gpu_temp is not None:
            candidates.append(self.thresholds.gpu_critical - gpu_temp)
        if not candidates:
            return None
        return min(candidates)

    def _weighted_input(
        self,
        cpu_temp: float | None,
        gpu_temp: float | None,
        snapshot: TelemetrySnapshot,
    ) -> float | None:
        profile = self._profile
        source = profile.source if profile else FanControlSource.MAX
        if cpu_temp is None and gpu_temp is None:
            return None
        if cpu_temp is None:
            return gpu_temp
        if gpu_temp is None:
            return cpu_temp
        if source is FanControlSource.CPU:
            return cpu_temp
        if source is FanControlSource.GPU:
            return gpu_temp
        if source is FanControlSource.WEIGHTED and profile is not None:
            weight_sum = max(1e-6, profile.thermal_weight_cpu + profile.thermal_weight_gpu)
            return (
                cpu_temp * profile.thermal_weight_cpu
                + gpu_temp * profile.thermal_weight_gpu
            ) / weight_sum
        return max(cpu_temp, gpu_temp)

    def _recommendation(self, weighted_input: float | None) -> float | None:
        if weighted_input is None or self._profile is None:
            return None
        return self._profile.curve.duty_for(
            weighted_input,
            min_pwm=self._profile.min_pwm,
            max_pwm=self._profile.max_pwm,
        )

    def _detect_anomalies(
        self,
        snapshot: TelemetrySnapshot,
        cpu_rate: float | None,
        gpu_rate: float | None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []
        rapid_rise = self._stable_band * 6.0
        for label, rate, state in (
            ("CPU", cpu_rate, self._cpu_state),
            ("GPU", gpu_rate, self._gpu_state),
        ):
            if rate is not None and rate >= rapid_rise:
                anomalies.append(
                    Anomaly(
                        kind=AnomalyKind.RAPID_THERMAL_RISE,
                        summary=f"{label} temperature is rising quickly",
                        detail=(
                            f"Estimated rate {rate:+.1f} °C/min over the last "
                            f"{int(self._rate_window_s)}s. This is an application "
                            "heuristic, not a diagnosis."
                        ),
                        confidence="medium",
                        value=rate,
                        unit="°C/min",
                    )
                )
            if state is ThermalState.CRITICAL:
                anomalies.append(
                    Anomaly(
                        kind=AnomalyKind.RAPID_THERMAL_RISE
                        if rate and rate > 0
                        else AnomalyKind.STALE_DATA,
                        summary=f"{label} has reached a critical temperature",
                        detail=f"Current {label} thermal state: CRITICAL.",
                        confidence="high",
                    )
                )
        return anomalies


def _trend(rate_c_per_min: float | None, stable_band: float) -> ThermalTrend:
    if rate_c_per_min is None:
        return ThermalTrend.STABLE
    if rate_c_per_min >= stable_band:
        return ThermalTrend.RISING
    if rate_c_per_min <= -stable_band:
        return ThermalTrend.FALLING
    return ThermalTrend.STABLE


__all__ = ["ThermalAnalyser", "ThermalThresholds"]
