"""Application service container (spec §4, §5, §107).

:class:`ApplicationServices` is the single object the UI holds.  It owns every
long-lived component — configuration, hardware registry, telemetry engine,
persistence, networking, cooling control, alerts, sessions, export and health —
and wires them together through the event bus.

Two rules shape this file:

* **Construction never blocks the UI.**  Hardware discovery, the database and
  the network thread are started explicitly, so the window can paint first and
  show progress while the system comes up (spec §5).
* **Everything degrades.**  If the database cannot be opened the app still runs
  with an in-memory fallback; if hardware discovery fails the simulated backend
  is used; if the ESP32 is absent the UI says so instead of pretending.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app import APP_NAME, APP_VERSION
from app.core.buffers import DropOldestQueue
from app.core.config import AppConfig, ConfigStore
from app.core.enums import (
    AlertCategory,
    AlertEventType,
    AlertSeverity,
    BackendKind,
    ConnectionState,
    ControllerMode,
    DataMode,
    HealthStatus,
)
from app.core.errors import PersistenceError
from app.core.events import EventBus
from app.core.logging_setup import configure_logging, get_logger
from app.core.paths import AppPaths
from app.hardware.registry import HardwareRegistry
from app.network.connection import TOPIC_STATE, ConnectionManager
from app.persistence import Persistence
from app.services.alerts import AlertService, default_rules
from app.services.cooling_control import CoolingController
from app.services.export import ExportService
from app.services.health import DiagnosticsService, HealthService
from app.services.profiles import ProfileService
from app.services.sessions import SessionRecorder
from app.simulation import SimulationBackend, SimulationKnobs
from app.telemetry.engine import TelemetryEngine

_log = get_logger(__name__)

#: Number of snapshots kept in the persistence queue before the oldest drop.
PERSIST_QUEUE_SIZE = 4096


class ApplicationServices:
    """Owns and coordinates every application service."""

    def __init__(self, *, mode: DataMode | None = None, base_dir: Path | None = None) -> None:
        self.paths: AppPaths = AppPaths.resolve(mode=mode, base_dir=base_dir)

        self.bus = EventBus()
        self.config_store = ConfigStore(self.paths.config)
        self.config: AppConfig = self.config_store.load()
        configure_logging(self.paths.logs, level=self.config.diagnostics.log_level)

        self.registry = HardwareRegistry()
        self.telemetry = TelemetryEngine(
            self.registry,
            self.bus,
            history_size=self._history_size(),
        )

        self.persistence: Persistence | None = None
        self._persistence_error: str = ""
        self._open_persistence()

        self.connection = ConnectionManager(
            self.bus,
            use_websocket=self.config.esp32.use_websocket,
            heartbeat_interval_s=self.config.esp32.heartbeat_interval_s,
            heartbeat_timeout_s=self.config.esp32.heartbeat_timeout_s,
            connect_timeout_s=self.config.esp32.connect_timeout_s,
            backoff_schedule=list(self.config.esp32.reconnect_backoff_s),
            reconnect_jitter=self.config.esp32.reconnect_jitter,
            healthy_reset_s=self.config.esp32.healthy_reset_s,
            require_authentication=self.config.esp32.require_authentication,
        )
        self.connection.set_telemetry_supplier(self._telemetry_supplier)

        self.profiles = ProfileService(self._require_persistence().profiles, self.bus)
        self.alerts = AlertService(
            self._require_persistence().alerts,
            self.bus,
            rules=default_rules(
                cpu_warning=self.config.alerts.cpu_warning_c,
                cpu_critical=self.config.alerts.cpu_critical_c,
                gpu_warning=self.config.alerts.gpu_warning_c,
                gpu_critical=self.config.alerts.gpu_critical_c,
                storage_warning=self.config.alerts.storage_warning_c,
            ),
            cooldown_s=self.config.alerts.notification_cooldown_s,
            notifications_enabled=self.config.alerts.windows_notifications,
        )
        self.sessions = SessionRecorder(
            self._require_persistence().sessions,
            self._require_persistence().baselines,
            self.bus,
        )
        self.export = ExportService(
            self._require_persistence().snapshots,
            self._require_persistence().sessions,
            self._require_persistence().exports,
            self.bus,
            export_dir=self.paths.exports,
        )
        self.cooling = CoolingController(self.bus)
        self.cooling.set_apply_command(self.connection.send_command)
        self.health = HealthService(self.bus)
        self.diagnostics = DiagnosticsService(
            paths=self.paths,
            persistence=self._require_persistence(),
            registry=self.registry,
            connection=self.connection,
        )

        self._persist_queue: DropOldestQueue = DropOldestQueue(PERSIST_QUEUE_SIZE)
        self._simulation: SimulationBackend | None = None
        self._started = False

        self._seed_state()
        self._register_health_probes()
        self._wire_events()

    # -- construction helpers ---------------------------------------------

    def _history_size(self) -> int:
        return max(600, self.config.charts.max_live_points * 4)

    def _open_persistence(self) -> None:
        try:
            self.persistence = Persistence(
                self.paths.database_file,
                backup_dir=self.paths.backups,
                retention_days=max(1, self.config.data.aggregate_5m_retention_days),
            )
            self.persistence.set_retention(
                max(1, self.config.data.aggregate_1m_retention_days)
            )
        except Exception as exc:  # noqa: BLE001 - never block startup on storage
            self._persistence_error = str(exc)
            _log.exception("persistence unavailable, continuing in memory: %s", exc)

    def _require_persistence(self) -> Persistence:
        if self.persistence is None:
            raise PersistenceError(self._persistence_error or "persistence unavailable")
        return self.persistence

    def _seed_state(self) -> None:
        persistence = self.persistence
        if persistence is None:
            return
        self.profiles.seed_builtins()
        self.profiles.set_active(self.config.cooling.active_profile or "balanced")
        self.sessions.recover_interrupted()

    def _register_health_probes(self) -> None:
        self.health.register("cpu", self._probe_cpu)
        self.health.register("gpu", self._probe_gpu)
        self.health.register("fan", self._probe_fan)
        self.health.register("esp32", self._probe_esp32)
        self.health.register("sensors", self._probe_sensors)
        self.health.register("database", self._probe_database)
        self.health.register("application", self._probe_application)

    def _wire_events(self) -> None:
        self.bus.subscribe("telemetry.snapshot", self._on_snapshot)
        self.bus.subscribe(TOPIC_STATE, self._on_connection_state)

    # -- lifecycle ---------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def persistence_error(self) -> str:
        return self._persistence_error

    def start(self, *, simulate: bool = False) -> None:
        """Bring the application up. Safe to call once."""
        if self._started:
            return
        self._started = True

        self.registry.discover()
        if simulate or self.registry.selected_backend() is BackendKind.NONE:
            self.enable_simulation()

        if self.persistence is not None:
            self.persistence.start_writer(self._persist_queue, on_error=self._on_write_error)

        self.cooling.set_profile(self.profiles.active)
        self.cooling.set_mode(ControllerMode.AUTO)

        if self.config.general.start_telemetry:
            self.telemetry.start()

        self.connection.start()
        if self.config.general.auto_connect_esp32 and not simulate:
            self._try_auto_connect()

        _log.info(
            "%s %s started (mode=%s, backend=%s)",
            APP_NAME,
            APP_VERSION,
            self.paths.mode.value,
            self.registry.selected_backend().value,
        )

    def enable_simulation(self, knobs: SimulationKnobs | None = None) -> None:
        """Switch to the simulated hardware backend (spec §29, §104)."""
        self._simulation = SimulationBackend(knobs or SimulationKnobs())
        self.registry.set_simulation(self._simulation)
        _log.info("simulation backend enabled")

    def enable_mock_device(self, *, host: str = "127.0.0.1", port: int = 8765):
        """Start the in-process mock ESP32 and return its device descriptor."""
        device = self.connection.enable_mock(host=host, port=port)
        self.config_store.update(esp32={"last_host": host, "port": port})
        return device

    def connect_device(self, device) -> None:
        self.connection.connect_device(device)

    def connect_manual(self, host: str, port: int) -> tuple[bool, str]:
        """Validate and connect to a user-entered endpoint.

        Returns ``(ok, reason)`` so the UI can show *why* an address was
        rejected instead of failing silently.  A valid address is remembered as
        the last-known host so the next launch can try it first.
        """
        from app.protocol.discovery import manual_device, validate_endpoint

        ok, reason = validate_endpoint(host, port)
        if not ok:
            return False, reason
        device = manual_device(host.strip(), int(port))
        self.config_store.update(esp32={"manual_host": host.strip(), "port": int(port)})
        self.connection.connect_device(device)
        return True, ""

    def last_known_device(self):
        """Return the cached last-known endpoint, or ``None``.

        Used to attempt a deterministic connection before an expensive
        discovery scan; discovery only runs when nothing is cached or the cached
        endpoint fails.
        """
        host = self.config.esp32.last_host or self.config.esp32.manual_host
        if not host:
            return None
        from app.models.devices import Esp32Device

        return Esp32Device(
            device_id=self.config.esp32.last_device_id or "cached",
            nickname="Last known",
            host=host,
            port=self.config.esp32.port,
        )

    def discover_devices(self, *, timeout_s: float | None = None):
        """Run a bounded UDP discovery scan and return the devices found.

        Discovery is best-effort and never blocks the caller for longer than the
        configured budget.  It is separate from connection failure handling: an
        empty result means "nothing answered", not "the connection failed".
        """
        from app.protocol.discovery import UdpDiscovery

        if not self.config.esp32.discovery_enabled:
            return []
        budget = self.config.esp32.discovery_timeout_s if timeout_s is None else timeout_s
        discovery = UdpDiscovery(port=self.config.esp32.discovery_port)
        return discovery.discover(timeout_s=max(0.1, budget))

    def _try_auto_connect(self) -> None:
        # Prefer the deterministic last-known endpoint, then a remembered
        # device, then the manual host — never a broadcast scan on start-up.
        device = self.last_known_device()
        if device is None and self.config.esp32.last_device_id and self.persistence is not None:
            device = self.persistence.devices.get(self.config.esp32.last_device_id)
        if device is not None:
            self.connection.connect_device(device)

    def shutdown(self) -> None:
        """Stop everything in the correct order and flush data to disk.

        Each step is isolated: a failure in one must not skip the rest, or a
        misbehaving subsystem could leave the writer thread or the network
        thread alive (a leaked thread is worse than a logged error).
        """
        if not self._started:
            return
        self._started = False
        steps: tuple[tuple[str, Callable[[], None]], ...] = (
            ("sessions", self._stop_sessions),
            ("telemetry", self.telemetry.stop),
            ("connection", self.connection.stop),
            ("window-state", self._save_window_state),
            ("persistence", self._close_persistence),
        )
        for name, step in steps:
            try:
                step()
            except Exception:  # noqa: BLE001 - shutdown must always complete
                _log.exception("error during shutdown: %s", name)
        _log.info("application services stopped")

    def _stop_sessions(self) -> None:
        if self.sessions.recording:
            self.sessions.stop()

    def _close_persistence(self) -> None:
        if self.persistence is not None:
            self.persistence.stop_writer()
            self.persistence.close()

    # -- runtime hooks -----------------------------------------------------

    def _on_snapshot(self, event) -> None:
        snapshot = event.payload
        if self.persistence is not None and self.config.telemetry.record_history:
            self._persist_queue.put(snapshot)
        self.cooling.update(snapshot)
        self.sessions.observe(snapshot)
        self.alerts.evaluate(snapshot)

    def _on_connection_state(self, event) -> None:
        state: ConnectionState = event.payload.get("state")
        if state is ConnectionState.CONNECTED:
            self.alerts.raise_event(
                category=AlertCategory.CONNECTION,
                severity=AlertSeverity.INFORMATION,
                title="ESP32 connected",
                message="The cooling controller is online.",
                event_type=AlertEventType.ESP32_RECONNECT,
                dedup_key="esp32.connect",
                source="network",
            )
        elif state is ConnectionState.DISCONNECTED:
            self.alerts.raise_event(
                category=AlertCategory.CONNECTION,
                severity=AlertSeverity.WARNING,
                title="ESP32 disconnected",
                message="The cooling controller stopped responding. Cooling is unchanged.",
                event_type=AlertEventType.ESP32_DISCONNECT,
                dedup_key="esp32.disconnect",
                source="network",
            )

    def _on_write_error(self, exc: Exception) -> None:
        self.bus.publish("persistence.error", str(exc))

    def _telemetry_supplier(self) -> dict[str, object]:
        snapshot = self.telemetry.last_snapshot
        if snapshot is None:
            return {}
        payload: dict[str, object] = {"cpuTempC": snapshot.cpu.temperature}
        if snapshot.gpu.temperature is not None:
            payload["gpuTempC"] = snapshot.gpu.temperature
        if snapshot.cpu.usage is not None:
            payload["cpuUsagePercent"] = snapshot.cpu.usage
        if snapshot.pad.temperature is not None:
            payload["padTempC"] = snapshot.pad.temperature
        # In automatic mode the firmware holds the fan authority (spec §47), so
        # the desktop *recommends* a duty and the controller applies it while it
        # stays in AUTO.  Without this the whole automatic loop is inert: the
        # controller computes a target that never reaches the device, and even
        # SAFE mode and the critical-temperature override command nothing.
        recommended = self.cooling.recommended_pwm()
        if recommended is not None:
            payload["recommendedPwm"] = round(recommended, 1)
        return payload

    # -- health probes -----------------------------------------------------

    def _probe_cpu(self):
        snapshot = self.telemetry.last_snapshot
        if snapshot is None:
            return HealthStatus.UNKNOWN, "health.detail.no_telemetry"
        if snapshot.cpu.temperature is None and snapshot.cpu.usage is None:
            return HealthStatus.WARNING, "health.detail.cpu_unavailable"
        return HealthStatus.HEALTHY, f"{snapshot.cpu.temperature or 0:.0f}°C"

    def _probe_gpu(self):
        snapshot = self.telemetry.last_snapshot
        if snapshot is None or snapshot.gpu.temperature is None:
            return HealthStatus.UNKNOWN, "health.detail.no_gpu"
        return HealthStatus.HEALTHY, f"{snapshot.gpu.temperature:.0f}°C"

    def _probe_fan(self):
        status = self.connection.status
        if not status.fan_available:
            return HealthStatus.UNKNOWN, "health.detail.no_fan"
        if status.fault.value not in ("none", ""):
            return HealthStatus.WARNING, status.fault.value
        return HealthStatus.HEALTHY, f"{status.fan_rpm or 0:.0f} RPM"

    def _probe_esp32(self):
        state = self.connection.state
        if state is ConnectionState.CONNECTED:
            return HealthStatus.HEALTHY, "health.detail.connected"
        if state in (ConnectionState.CONNECTING, ConnectionState.PAIRING):
            return HealthStatus.WARNING, state.value
        return HealthStatus.UNKNOWN, "health.detail.not_connected"

    def _probe_sensors(self):
        try:
            readings = self.registry.read()
        except Exception as exc:  # noqa: BLE001
            return HealthStatus.ERROR, str(exc)
        if not readings:
            return HealthStatus.WARNING, "health.detail.no_sensors"
        return HealthStatus.HEALTHY, f"{len(readings)} sensors"

    def _probe_database(self):
        if self.persistence is None:
            return HealthStatus.ERROR, self._persistence_error or "health.detail.unavailable"
        ok = self.persistence.integrity_check()
        size_mb = self.persistence.database.size_bytes() / (1024 * 1024)
        return (
            (HealthStatus.HEALTHY if ok else HealthStatus.ERROR),
            f"{size_mb:.1f} MiB",
        )

    def _probe_application(self):
        return HealthStatus.HEALTHY, f"{APP_NAME} {APP_VERSION}"

    # -- window state ------------------------------------------------------

    def _save_window_state(self) -> None:
        try:
            self.config_store.save()
        except Exception:  # noqa: BLE001
            _log.exception("failed to save configuration")

    def update_config(self, **sections) -> AppConfig:
        self.config = self.config_store.update(**sections)
        return self.config


__all__ = ["ApplicationServices", "PERSIST_QUEUE_SIZE"]
