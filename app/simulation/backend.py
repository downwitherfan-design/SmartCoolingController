"""Simulation back-end: a physically-motivated laptop thermal model.

This is the *only* place in the application allowed to synthesise telemetry
(spec §314).  Every value it produces is tagged
``MetricState.SIMULATED`` and the UI shows a persistent SIMULATION MODE badge;
simulated data is never mixed into real history without the user's explicit
choice (spec §52, §106, §295).

The model is deliberately simple but behaves plausibly:

* load is driven by a workload intensity plus bounded noise,
* temperature integrates ``heat_in - heat_out`` with a first-order thermal
  time constant, where cooling depends on fan airflow,
* the fan follows its target with a rate limit and maps PWM to RPM through a
  response curve with a start-up dead band,
* power, current and voltage are derived from load and fan duty.

It supports injected faults: sensor failure, packet loss and disconnect, so
anomaly detection and the UI empty states can be exercised without hardware.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.core.clock import utc_now
from app.core.enums import (
    BackendKind,
    HardwareCategory,
    SensorType,
)
from app.core.units import clamp
from app.hardware.base import HardwareMonitorBackend
from app.models.sensors import HardwareProfile, SensorReading


@dataclass
class SimulationKnobs:
    """User-controllable simulation parameters (spec §52)."""

    cpu_load: float = 35.0          # 0..100 %
    gpu_load: float = 20.0          # 0..100 %
    thermal_intensity: float = 1.0  # multiplier on heat generation
    fan_response: float = 1.0       # multiplier on cooling efficiency
    ambient_c: float = 24.0
    network_latency_ms: float = 12.0
    packet_loss_percent: float = 0.0
    sensor_fault: bool = False
    disconnected: bool = False

    def clamped(self) -> SimulationKnobs:
        return SimulationKnobs(
            cpu_load=clamp(self.cpu_load, 0.0, 100.0),
            gpu_load=clamp(self.gpu_load, 0.0, 100.0),
            thermal_intensity=clamp(self.thermal_intensity, 0.1, 3.0),
            fan_response=clamp(self.fan_response, 0.1, 2.0),
            ambient_c=clamp(self.ambient_c, 5.0, 45.0),
            network_latency_ms=clamp(self.network_latency_ms, 0.0, 2000.0),
            packet_loss_percent=clamp(self.packet_loss_percent, 0.0, 100.0),
            sensor_fault=self.sensor_fault,
            disconnected=self.disconnected,
        )


class SimulationBackend(HardwareMonitorBackend):
    """A self-contained thermal simulator implementing the back-end contract."""

    kind = BackendKind.SIMULATION

    def __init__(self, knobs: SimulationKnobs | None = None) -> None:
        super().__init__()
        self.knobs = (knobs or SimulationKnobs()).clamped()
        self._rng = random.Random(0xC0FFEE)
        self._cpu_temp = 42.0
        self._gpu_temp = 38.0
        self._storage_temp = 33.0
        self._fan_pwm = 0.0
        self._fan_rpm = 0.0
        self._fan_target = 0.0
        self._last_time: float | None = None

    # -- lifecycle ---------------------------------------------------------

    def is_available(self) -> bool:
        return True

    def open(self) -> None:
        self._mark_ready()

    # -- knobs -------------------------------------------------------------

    def set_knobs(self, knobs: SimulationKnobs) -> None:
        self.knobs = knobs.clamped()

    def set_fan_target(self, percent: float) -> None:
        self._fan_target = clamp(percent, 0.0, 100.0)

    # -- data --------------------------------------------------------------

    def read(self) -> list[SensorReading]:
        import time

        now_wall = time.monotonic()
        dt = 1.0 if self._last_time is None else max(1e-3, now_wall - self._last_time)
        self._last_time = now_wall
        knobs = self.knobs

        cpu_load = clamp(knobs.cpu_load + self._rng.gauss(0, 2.0), 0.0, 100.0)
        gpu_load = clamp(knobs.gpu_load + self._rng.gauss(0, 2.0), 0.0, 100.0)

        self._step_fan(dt)
        airflow = (self._fan_pwm / 100.0) ** 1.2

        self._cpu_temp = self._integrate(
            self._cpu_temp, cpu_load / 100.0 * 55.0 * knobs.thermal_intensity,
            airflow, knobs, dt, 45.0,
        )
        self._gpu_temp = self._integrate(
            self._gpu_temp, gpu_load / 100.0 * 48.0 * knobs.thermal_intensity,
            airflow, knobs, dt, 60.0,
        )
        self._storage_temp = self._integrate(
            self._storage_temp, cpu_load / 100.0 * 10.0,
            airflow, knobs, dt, 200.0,
        )

        cpu_power = 8.0 + cpu_load / 100.0 * 55.0
        gpu_power = 5.0 + gpu_load / 100.0 * 90.0
        fan_voltage = 12.0 * (self._fan_pwm / 100.0)
        fan_current = 0.08 + (self._fan_pwm / 100.0) ** 1.6 * 0.62
        fan_power = fan_voltage * fan_current

        now = utc_now()
        readings: list[SensorReading] = [
            self._r("sim.cpu.temp", "Simulated CPU", HardwareCategory.CPU, "CPU Package",
                    SensorType.TEMPERATURE, self._cpu_temp, "°C", now),
            self._r("sim.cpu.load", "Simulated CPU", HardwareCategory.CPU, "CPU Load",
                    SensorType.LOAD, cpu_load, "%", now),
            self._r("sim.cpu.clock", "Simulated CPU", HardwareCategory.CPU, "CPU Clock",
                    SensorType.CLOCK, 1200.0 + cpu_load * 28.0, "MHz", now),
            self._r("sim.cpu.power", "Simulated CPU", HardwareCategory.CPU, "CPU Power",
                    SensorType.POWER, cpu_power, "W", now),
            self._r("sim.gpu.temp", "Simulated GPU", HardwareCategory.GPU, "GPU Core",
                    SensorType.TEMPERATURE, self._gpu_temp, "°C", now),
            self._r("sim.gpu.hotspot", "Simulated GPU", HardwareCategory.GPU, "GPU Hotspot",
                    SensorType.TEMPERATURE, self._gpu_temp + 7.5, "°C", now),
            self._r("sim.gpu.load", "Simulated GPU", HardwareCategory.GPU, "GPU Load",
                    SensorType.LOAD, gpu_load, "%", now),
            self._r("sim.gpu.power", "Simulated GPU", HardwareCategory.GPU, "GPU Power",
                    SensorType.POWER, gpu_power, "W", now),
            self._r("sim.storage.temp", "Simulated NVMe", HardwareCategory.STORAGE,
                    "Storage Temperature", SensorType.TEMPERATURE, self._storage_temp, "°C", now),
        ]
        if not knobs.sensor_fault:
            readings.extend(
                [
                    self._r("sim.fan.pwm", "Simulated Fan", HardwareCategory.COOLER,
                            "Fan PWM", SensorType.LOAD, self._fan_pwm, "%", now),
                    self._r("sim.fan.rpm", "Simulated Fan", HardwareCategory.COOLER,
                            "Fan Speed", SensorType.FAN, self._fan_rpm, "RPM", now),
                    self._r("sim.fan.voltage", "Simulated Fan", HardwareCategory.COOLER,
                            "Fan Voltage", SensorType.VOLTAGE, fan_voltage, "V", now),
                    self._r("sim.fan.current", "Simulated Fan", HardwareCategory.COOLER,
                            "Fan Current", SensorType.CURRENT, fan_current, "A", now),
                    self._r("sim.fan.power", "Simulated Fan", HardwareCategory.COOLER,
                            "Fan Power", SensorType.POWER, fan_power, "W", now),
                    self._r("sim.pad.temp", "Simulated Pad", HardwareCategory.COOLER,
                            "Pad Temperature", SensorType.TEMPERATURE, self._storage_temp + 4.0, "°C", now),
                ]
            )
        self._record_read()
        return readings

    def profile(self) -> HardwareProfile:
        return HardwareProfile(
            cpu_model="Simulated CPU (12 cores)",
            gpu_models=["Simulated GPU"],
            ram_total_gb=16.0,
            storage_models=["Simulated NVMe 1TB"],
            os_name="Simulation Environment",
            os_version="sim",
            architecture="x64",
            hostname="simulated-host",
            machine_fingerprint="simulation",
        )

    # -- model -------------------------------------------------------------

    def _step_fan(self, dt: float) -> None:
        # First-order approach to the target with a modest rate limit.
        error = self._fan_target - self._fan_pwm
        step = clamp(error, -250.0 * dt, 250.0 * dt)
        self._fan_pwm = clamp(self._fan_pwm + step, 0.0, 100.0)
        # Start-up dead band, then a near-linear RPM response.
        if self._fan_pwm < 8.0:
            target_rpm = 0.0
        else:
            target_rpm = 900.0 + (self._fan_pwm - 8.0) / 92.0 * 6100.0
        target_rpm *= self.knobs.fan_response
        self._fan_rpm = max(0.0, self._fan_rpm + (target_rpm - self._fan_rpm) * min(1.0, dt * 3.0))

    def _integrate(
        self,
        current: float,
        heat: float,
        airflow: float,
        knobs: SimulationKnobs,
        dt: float,
        time_constant_s: float,
    ) -> float:
        cooling = (current - knobs.ambient_c) * (0.35 + 0.9 * airflow)
        equilibrium = knobs.ambient_c + max(0.0, (heat - cooling * 0.05)) / 1.6
        alpha = min(1.0, dt / time_constant_s)
        next_value = current + (equilibrium - current) * alpha
        # Small sensor noise keeps the charts alive without being unrealistic.
        next_value += self._rng.gauss(0, 0.12)
        return clamp(next_value, knobs.ambient_c - 2.0, 110.0)

    @staticmethod
    def _r(
        sensor_id: str,
        hardware: str,
        category: HardwareCategory,
        name: str,
        sensor_type: SensorType,
        value: float,
        unit: str,
        now: object,
    ) -> SensorReading:
        return SensorReading(
            sensor_id=sensor_id,
            hardware_name=hardware,
            hardware_category=category,
            sensor_name=name,
            sensor_type=sensor_type,
            value=round(float(value), 3),
            unit=unit,
            backend=BackendKind.SIMULATION,
            timestamp=now,
            metadata={"simulated": True},
        )


__all__ = ["SimulationBackend", "SimulationKnobs"]
