"""Telemetry engine (spec §14, §15, §16, §207, §208).

Responsibilities
----------------
* Sample hardware at a configurable interval on a dedicated worker thread so
  the UI thread is never blocked.
* Normalise readings into logical metrics and assemble an immutable
  :class:`TelemetrySnapshot`.
* Run the analytics engine on each snapshot.
* Publish snapshots and anomaly events on the shared :class:`EventBus`.
* Hand snapshots to a bounded queue for the persistence writer.

It deliberately knows nothing about Qt.  The Qt bridge in
``app.application.qt_bridge`` subscribes to the bus and marshals onto the GUI
thread.

Concurrency
-----------
The sampler owns exactly one thread.  :meth:`start` is idempotent, and
:meth:`stop` joins the thread so no duplicate workers can accumulate across
sleep/resume cycles (spec §60, §112).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from app.analytics.thermal import ThermalAnalyser
from app.core.buffers import DropOldestQueue, RingBuffer
from app.core.clock import utc_now
from app.core.enums import BackendKind, DataProvenance, MetricKey, MetricState
from app.core.errors import TelemetryError
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.hardware.normalisation import SensorNormaliser
from app.hardware.registry import HardwareRegistry
from app.models.sensors import NormalisedMetric, SensorDescriptor
from app.models.telemetry import (
    BatteryTelemetry,
    CoolingPadTelemetry,
    CpuTelemetry,
    FanTelemetry,
    GpuTelemetry,
    MemoryTelemetry,
    StorageTelemetry,
    TelemetrySnapshot,
)

_log = get_logger(__name__)

#: Topics published on the bus.
TOPIC_SNAPSHOT = "telemetry.snapshot"
TOPIC_ANOMALY = "telemetry.anomaly"
TOPIC_STATE = "telemetry.state"
TOPIC_SENSORS = "telemetry.sensors"

#: The default number of snapshots kept in memory for live charts.
DEFAULT_HISTORY = 7200


class TelemetryEngine:
    """Samples hardware, normalises, analyses and publishes snapshots."""

    def __init__(
        self,
        registry: HardwareRegistry,
        bus: EventBus,
        normaliser: SensorNormaliser | None = None,
        analyser: ThermalAnalyser | None = None,
        *,
        history_size: int = DEFAULT_HISTORY,
        persist_queue_size: int = 4096,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._normaliser = normaliser or SensorNormaliser()
        self._analyser = analyser or ThermalAnalyser()
        self._history: RingBuffer[TelemetrySnapshot] = RingBuffer(history_size)
        self._persist_queue: DropOldestQueue[TelemetrySnapshot] = DropOldestQueue(
            persist_queue_size
        )
        self._sensors: list[SensorDescriptor] = []

        self._interval_s = 0.5
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = threading.Event()
        self._lock = threading.RLock()
        self._sequence = 0
        self._last_snapshot: TelemetrySnapshot | None = None
        self._esp32_provider: Callable[[], dict[object, object]] | None = None
        self._metrics_provider: Callable[[], dict[MetricKey, NormalisedMetric]] | None = None

    # -- configuration -----------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def sequence(self) -> int:
        return self._sequence

    @property
    def last_snapshot(self) -> TelemetrySnapshot | None:
        return self._last_snapshot

    @property
    def persist_queue(self) -> DropOldestQueue[TelemetrySnapshot]:
        return self._persist_queue

    @property
    def sensors(self) -> list[SensorDescriptor]:
        return list(self._sensors)

    @property
    def analyser(self) -> ThermalAnalyser:
        return self._analyser

    @property
    def normaliser(self) -> SensorNormaliser:
        return self._normaliser

    def set_interval(self, interval_s: float) -> None:
        if interval_s <= 0:
            raise TelemetryError("sample interval must be positive")
        with self._lock:
            self._interval_s = float(interval_s)

    def set_esp32_provider(self, provider: Callable[[], dict[object, object]] | None) -> None:
        """Register a callable returning the latest ESP32 fan/status values."""
        self._esp32_provider = provider

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._running.set()
            self._thread = threading.Thread(
                target=self._run, name="TelemetrySampler", daemon=True
            )
            self._thread.start()
        self._bus.publish(TOPIC_STATE, {"running": True})
        _log.info("telemetry engine started (%.3fs)", self._interval_s)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
            self._running.clear()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
        self._bus.publish(TOPIC_STATE, {"running": False})
        _log.info("telemetry engine stopped")

    def sample_once(self) -> TelemetrySnapshot:
        """Produce and publish a single snapshot synchronously.

        Used by tests and preview screens: unlike the background loop it runs on
        the caller's thread, but it goes through the same emit path so history,
        persistence and event subscribers all observe the sample.
        """
        snapshot = self._build_snapshot()
        self._emit(snapshot)
        return snapshot

    # -- loop --------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = self._build_snapshot()
                self._emit(snapshot)
            except Exception:
                _log.exception("telemetry sample failed")
            # Sleep in small slices so stop() is responsive.
            self._stop.wait(self._interval_s)

    def _emit(self, snapshot: TelemetrySnapshot) -> None:
        self._history.append(snapshot)
        self._persist_queue.put(snapshot)
        self._bus.publish(TOPIC_SNAPSHOT, snapshot)
        for anomaly in self._analyser.last_anomalies:
            self._bus.publish(TOPIC_ANOMALY, anomaly)

    # -- snapshot assembly -------------------------------------------------

    def _build_snapshot(self) -> TelemetrySnapshot:
        readings = self._registry.read()
        self._sensors = _describe_sensors(readings, self._normaliser)
        result = self._normaliser.normalise(readings)
        metrics = dict(result.metrics)

        # ESP32 values (fan/pad) are injected by the connection manager so that
        # Windows telemetry and ESP32 control stay cleanly separated (spec §3).
        if self._esp32_provider is not None:
            try:
                metrics.update(_coerce_metrics(self._esp32_provider() or {}))
            except Exception:
                _log.exception("ESP32 metric provider failed")

        snapshot = TelemetrySnapshot(
            sequence=self._sequence,
            timestamp=utc_now(),
            backend=self._registry.selected_backend(),
            metrics=metrics,
            simulated=any(
                m.state is MetricState.SIMULATED for m in metrics.values()
            ),
        )
        snapshot.cpu = _cpu_from(metrics)
        snapshot.gpu = _gpu_from(metrics)
        snapshot.memory = _memory_from(metrics)
        snapshot.storage = _storage_from(metrics)
        snapshot.battery = _battery_from(metrics)
        snapshot.fan = _fan_from(metrics)
        snapshot.pad = _pad_from(metrics)
        snapshot.validity = _validity(snapshot)

        snapshot.thermal = self._analyser.analyse(snapshot)
        self._sequence += 1
        self._last_snapshot = snapshot
        return snapshot

    # -- history -----------------------------------------------------------

    def history(self) -> list[TelemetrySnapshot]:
        return self._history.snapshot()

    def history_since(self, timestamp: float) -> list[TelemetrySnapshot]:
        return [s for s in self._history.snapshot() if s.timestamp.timestamp() >= timestamp]

    def clear_history(self) -> None:
        self._history.clear()


def _describe_sensors(
    readings: list, normaliser: SensorNormaliser
) -> list[SensorDescriptor]:
    """Build a stable descriptor list with running min/max/average."""

    descriptors: list[SensorDescriptor] = []
    for reading in readings:
        descriptors.append(
            SensorDescriptor(
                sensor_id=reading.sensor_id,
                hardware_name=reading.hardware_name,
                hardware_category=reading.hardware_category,
                sensor_name=reading.sensor_name,
                sensor_type=reading.sensor_type,
                unit=reading.unit,
                backend=reading.backend,
                current=reading.value,
                minimum=reading.minimum,
                maximum=reading.maximum,
                state=MetricState.VALID
                if not reading.unavailable
                else MetricState.UNAVAILABLE,
            )
        )
    return descriptors


def _metric(metrics: dict[MetricKey, NormalisedMetric], key: MetricKey) -> float | None:
    metric = metrics.get(key)
    return metric.value if metric and metric.available else None


def _coerce_metrics(raw: dict[object, object]) -> dict[MetricKey, NormalisedMetric]:
    """Accept either :class:`NormalisedMetric` values or bare numbers.

    The ESP32 connection manager may hand us raw floats for convenience; this
    keeps the engine tolerant without weakening the model contract.
    """
    from app.core.units import canonical_unit
    from app.hardware.normalisation import _metric_sensor_type

    result: dict[MetricKey, NormalisedMetric] = {}
    for key, value in raw.items():
        try:
            metric_key = key if isinstance(key, MetricKey) else MetricKey(key)
        except ValueError:
            continue
        if isinstance(value, NormalisedMetric):
            result[metric_key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            # ``None``, strings and anything else are not a usable reading.
            continue
        sensor_type = _metric_sensor_type(metric_key)
        result[metric_key] = NormalisedMetric(
            key=metric_key,
            value=float(value),
            unit=canonical_unit(sensor_type),
            state=MetricState.VALID,
            provenance=DataProvenance.MEASURED,
            source_backend=BackendKind.NONE,
            source_hardware="ESP32",
            source_sensor_name=metric_key.value,
        )
    return result


def _state(metrics: dict[MetricKey, NormalisedMetric], *keys: MetricKey) -> MetricState:
    for key in keys:
        metric = metrics.get(key)
        if metric is not None and metric.available:
            return metric.state
    return MetricState.UNAVAILABLE


def _cpu_from(metrics: dict[MetricKey, NormalisedMetric]) -> CpuTelemetry:
    return CpuTelemetry(
        temperature=_metric(metrics, MetricKey.CPU_TEMPERATURE),
        temperature_package=_metric(metrics, MetricKey.CPU_TEMPERATURE_PACKAGE),
        temperature_core_avg=_metric(metrics, MetricKey.CPU_TEMPERATURE_CORE_AVG),
        temperature_core_max=_metric(metrics, MetricKey.CPU_TEMPERATURE_CORE_MAX),
        usage=_metric(metrics, MetricKey.CPU_USAGE),
        clock_mhz=_metric(metrics, MetricKey.CPU_CLOCK),
        power_w=_metric(metrics, MetricKey.CPU_POWER),
        voltage_v=_metric(metrics, MetricKey.CPU_VOLTAGE),
        state=_state(
            metrics,
            MetricKey.CPU_TEMPERATURE,
            MetricKey.CPU_USAGE,
        ),
    )


def _gpu_from(metrics: dict[MetricKey, NormalisedMetric]) -> GpuTelemetry:
    name = ""
    for key in (MetricKey.GPU_TEMPERATURE, MetricKey.GPU_USAGE):
        metric = metrics.get(key)
        if metric and metric.source_hardware:
            name = metric.source_hardware
            break
    return GpuTelemetry(
        name=name,
        temperature=_metric(metrics, MetricKey.GPU_TEMPERATURE),
        hotspot_temperature=_metric(metrics, MetricKey.GPU_HOTSPOT_TEMPERATURE),
        usage=_metric(metrics, MetricKey.GPU_USAGE),
        clock_mhz=_metric(metrics, MetricKey.GPU_CLOCK),
        power_w=_metric(metrics, MetricKey.GPU_POWER),
        memory_usage=_metric(metrics, MetricKey.GPU_MEMORY_USAGE),
        memory_total_mb=_metric(metrics, MetricKey.GPU_MEMORY_TOTAL),
        state=_state(metrics, MetricKey.GPU_TEMPERATURE, MetricKey.GPU_USAGE),
    )


def _memory_from(metrics: dict[MetricKey, NormalisedMetric]) -> MemoryTelemetry:
    return MemoryTelemetry(
        total_mb=_metric(metrics, MetricKey.RAM_TOTAL),
        used_mb=_metric(metrics, MetricKey.RAM_USED),
        available_mb=_metric(metrics, MetricKey.RAM_AVAILABLE),
        usage_percent=_metric(metrics, MetricKey.RAM_USAGE),
        state=_state(metrics, MetricKey.RAM_USAGE),
    )


def _storage_from(metrics: dict[MetricKey, NormalisedMetric]) -> StorageTelemetry:
    metric = metrics.get(MetricKey.STORAGE_TEMPERATURE)
    return StorageTelemetry(
        name=metric.source_hardware if metric else "",
        temperature=_metric(metrics, MetricKey.STORAGE_TEMPERATURE),
        activity_percent=_metric(metrics, MetricKey.STORAGE_ACTIVITY),
        state=_state(metrics, MetricKey.STORAGE_TEMPERATURE, MetricKey.STORAGE_ACTIVITY),
    )


def _battery_from(metrics: dict[MetricKey, NormalisedMetric]) -> BatteryTelemetry:
    level = _metric(metrics, MetricKey.BATTERY_LEVEL)
    plugged = _metric(metrics, MetricKey.BATTERY_STATE)
    charging: bool | None = None
    if level is not None and plugged is not None:
        charging = plugged >= 0.5 and level < 100
    return BatteryTelemetry(
        present=level is not None,
        level_percent=level,
        charging=charging,
        plugged=None if plugged is None else plugged >= 0.5,
        power_w=_metric(metrics, MetricKey.BATTERY_POWER),
        state=_state(metrics, MetricKey.BATTERY_LEVEL),
    )


def _fan_from(metrics: dict[MetricKey, NormalisedMetric]) -> FanTelemetry:
    from app.core.enums import DataProvenance, FanState

    rpm = _metric(metrics, MetricKey.FAN_RPM)
    pwm = _metric(metrics, MetricKey.FAN_PWM)
    target = _metric(metrics, MetricKey.FAN_TARGET)
    available = any(v is not None for v in (rpm, pwm, target))
    if not available:
        return FanTelemetry(available=False)
    provenance = DataProvenance.MEASURED
    rpm_metric = metrics.get(MetricKey.FAN_RPM)
    if rpm_metric is not None and rpm_metric.state is MetricState.SIMULATED:
        provenance = DataProvenance.SIMULATED
    state = FanState.RUNNING if (rpm or 0) > 0 else FanState.STOPPED
    if target is not None and pwm is not None and pwm + 5 < target:
        state = FanState.STARTING
    return FanTelemetry(
        target_percent=target,
        pwm_percent=pwm,
        rpm=rpm,
        voltage_v=_metric(metrics, MetricKey.FAN_VOLTAGE),
        current_a=_metric(metrics, MetricKey.FAN_CURRENT),
        power_w=_metric(metrics, MetricKey.FAN_POWER),
        state=state,
        available=True,
        provenance=provenance,
    )


def _pad_from(metrics: dict[MetricKey, NormalisedMetric]) -> CoolingPadTelemetry:
    temperature = _metric(metrics, MetricKey.PAD_TEMPERATURE)
    voltage = _metric(metrics, MetricKey.PAD_VOLTAGE)
    current = _metric(metrics, MetricKey.PAD_CURRENT)
    power = _metric(metrics, MetricKey.PAD_POWER)
    return CoolingPadTelemetry(
        temperature=temperature,
        voltage_v=voltage,
        current_a=current,
        power_w=power,
        available=any(v is not None for v in (temperature, voltage, current, power)),
    )


def _validity(snapshot: TelemetrySnapshot) -> MetricState:
    if snapshot.simulated:
        return MetricState.SIMULATED
    has_real = (
        snapshot.cpu.available
        or snapshot.gpu.available
        or snapshot.memory.usage_percent is not None
        or snapshot.storage.temperature is not None
    )
    return MetricState.VALID if has_real else MetricState.UNAVAILABLE


__all__ = [
    "TelemetryEngine",
    "TOPIC_SNAPSHOT",
    "TOPIC_ANOMALY",
    "TOPIC_STATE",
    "TOPIC_SENSORS",
    "DEFAULT_HISTORY",
]
