"""ESP32 connection manager (spec §43, §47, §48, §207, §208).

Runs the asynchronous protocol session on a dedicated event-loop thread so the
Qt UI thread is never blocked.  It owns:

* connect / pair / disconnect,
* a heartbeat loop that measures round-trip latency,
* a telemetry loop that pushes laptop telemetry to the controller,
* a receive loop that validates inbound packets and updates status,
* packet-loss accounting from sequence gaps,
* controlled reconnect with exponential backoff (no retry storms, spec §48).

The manager publishes state/status changes on the shared :class:`EventBus` and
exposes the latest fan metrics as :class:`~app.core.enums.MetricKey` values,
which the telemetry engine merges into its snapshots.  This is the clean seam
between *Windows telemetry* and *ESP32 control* required by spec §3.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable

from app import PROTOCOL_VERSION
from app.core.clock import utc_now
from app.core.enums import (
    BackendKind,
    ConnectionState,
    ControllerMode,
    DataProvenance,
    FaultState,
    HealthStatus,
    MessageType,
    MetricKey,
    MetricState,
)
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.models.devices import Esp32Device, Esp32Status
from app.models.sensors import NormalisedMetric
from app.protocol.discovery import DiscoveredDevice
from app.protocol.messages import (
    Packet,
    decode_packet,
    encode_packet,
    make_heartbeat,
    make_hello,
    make_telemetry,
    validate_status,
)
from app.protocol.mock_esp32 import MockEsp32
from app.protocol.transport import Transport, create_transport

_log = get_logger(__name__)

TOPIC_STATE = "esp32.state"
TOPIC_STATUS = "esp32.status"
TOPIC_DEVICE = "esp32.device"

#: Seconds without a packet before the link is considered stale/degraded.
DEGRADED_AFTER_S = 2.0
STALE_AFTER_S = 4.0


class ConnectionManager:
    """Manages the lifecycle of the ESP32 link."""

    def __init__(
        self,
        bus: EventBus,
        *,
        use_websocket: bool = True,
        heartbeat_interval_s: float = 1.0,
        heartbeat_timeout_s: float = 3.0,
        connect_timeout_s: float = 5.0,
        backoff_schedule: list[float] | None = None,
        require_authentication: bool = True,
    ) -> None:
        self._bus = bus
        self._use_websocket = use_websocket
        self._heartbeat_interval_s = heartbeat_interval_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._backoff_schedule = backoff_schedule or [1.0, 2.0, 4.0, 8.0, 15.0, 30.0]
        self._require_auth = require_authentication

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._desired: DiscoveredDevice | None = None
        self._device: Esp32Device | None = None
        self._status = Esp32Status()
        self._state = ConnectionState.DISCONNECTED
        self._lock = threading.RLock()
        self._secret: bytes | None = None
        self._sequence = 0
        self._telemetry_supplier: Callable[[], dict[str, object]] | None = None
        self._mock: MockEsp32 | None = None
        self._peer_sequence: int | None = None
        self._received = 0
        self._lost = 0
        self._pending_heartbeat_at: float | None = None
        self._disconnect_event: asyncio.Event | None = None
        self._active_transport: Transport | None = None

    # -- configuration -----------------------------------------------------

    def set_secret(self, secret: bytes | None) -> None:
        self._secret = secret

    def set_telemetry_supplier(self, supplier: Callable[[], dict[str, object]] | None) -> None:
        self._telemetry_supplier = supplier

    def set_use_websocket(self, use_websocket: bool) -> None:
        self._use_websocket = use_websocket

    # -- state -------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def status(self) -> Esp32Status:
        return self._status

    @property
    def device(self) -> Esp32Device | None:
        return self._device

    @property
    def packet_loss_percent(self) -> float | None:
        total = self._received + self._lost
        if total == 0:
            return None
        return round(self._lost / total * 100.0, 2)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._thread_main, name="Esp32Connection", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._loop is not None and self._loop.is_running():
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(lambda: None)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None

    def connect_device(self, device: DiscoveredDevice | Esp32Device) -> None:
        """Request a connection to a device (async; returns immediately)."""
        if isinstance(device, Esp32Device):
            target = DiscoveredDevice(
                device_id=device.device_id,
                host=device.host,
                port=device.port,
                nickname=device.nickname,
                firmware_version=device.firmware_version,
                protocol_version=device.protocol_version,
                is_mock=device.is_mock,
            )
        else:
            target = device
        with self._lock:
            self._desired = target
        self.start()
        self._set_state(ConnectionState.CONNECTING)

    def disconnect(self) -> None:
        with self._lock:
            self._desired = None
        if self._loop is not None and self._loop.is_running() and self._disconnect_event is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._disconnect_event.set)
        self._set_state(ConnectionState.DISCONNECTED)

    def forget_device(self) -> None:
        self.disconnect()
        with self._lock:
            self._device = None

    # -- commands ----------------------------------------------------------

    def send_command(self, command: str, **arguments: object) -> None:
        """Queue a command to the controller (fire and forget)."""
        if self._loop is None or not self._loop.is_running():
            _log.warning("cannot send command %s: connection loop is not running", command)
            return
        coro = self._send_command_async(command, arguments)
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # -- mock hosting ------------------------------------------------------

    def enable_mock(self, *, host: str = "127.0.0.1", port: int = 8765) -> DiscoveredDevice:
        """Start an in-process mock controller and return its device handle."""
        mock = MockEsp32(host=host, port=port)
        self._mock = mock
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(mock.start(), self._loop)
        return DiscoveredDevice(
            device_id=mock.device_id,
            host=host,
            port=port,
            nickname=mock.nickname,
            firmware_version=mock.firmware,
            is_mock=True,
        )

    # -- metrics for the telemetry engine ----------------------------------

    def latest_metrics(self) -> dict[MetricKey, NormalisedMetric]:
        status = self._status
        if not status.fan_available:
            return {}
        provenance = DataProvenance.SIMULATED if status.is_mock else DataProvenance.MEASURED
        state = MetricState.SIMULATED if status.is_mock else MetricState.VALID
        values = {
            MetricKey.FAN_TARGET: (status.fan_target_percent, "%"),
            MetricKey.FAN_PWM: (status.fan_pwm_percent, "%"),
            MetricKey.FAN_RPM: (status.fan_rpm, "RPM"),
            MetricKey.FAN_VOLTAGE: (status.fan_voltage_v, "V"),
            MetricKey.FAN_CURRENT: (status.fan_current_a, "A"),
            MetricKey.FAN_POWER: (status.fan_power_w, "W"),
        }
        metrics: dict[MetricKey, NormalisedMetric] = {}
        for key, (value, unit) in values.items():
            if value is None:
                continue
            metrics[key] = NormalisedMetric(
                key=key,
                value=float(value),
                unit=unit,
                state=state,
                provenance=provenance,
                source_backend=BackendKind.NONE,
                source_hardware="ESP32" + (" (Mock)" if status.is_mock else ""),
                source_sensor_name=key.value,
            )
        return metrics

    def diagnostics_snapshot(self) -> dict[str, object]:
        """Return a redaction-safe summary of the link for support bundles."""
        status = self._status
        device = self._device
        return {
            "state": self._state.value,
            "use_websocket": self._use_websocket,
            "require_authentication": self._require_auth,
            "device": {
                "device_id": status.device_id or (device.device_id if device else ""),
                "firmware": status.firmware_version,
                "protocol_version": status.protocol_version,
                "host": device.host if device else "",
                "port": device.port if device else None,
                "is_mock": status.is_mock,
            },
            "latency_ms": status.latency_ms,
            "packet_loss_percent": self.packet_loss_percent,
            "received": self._received,
            "lost": self._lost,
            "fan_available": status.fan_available,
            "fault": status.fault.value if hasattr(status.fault, "value") else str(status.fault),
        }

    # -- thread + event loop ----------------------------------------------

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception:
            _log.exception("connection thread crashed")
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._loop = None

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            target = self._desired
            if target is None:
                await asyncio.sleep(0.2)
                attempt = 0
                continue
            try:
                await self._session(target)
                attempt = 0
            except Exception as exc:
                _log.info("ESP32 session ended: %s", exc)
                self._set_state(ConnectionState.ERROR, str(exc))
            if self._stop.is_set() or self._desired is None:
                continue
            delay = self._backoff_schedule[min(attempt, len(self._backoff_schedule) - 1)]
            attempt += 1
            self._set_state(ConnectionState.CONNECTING, f"retrying in {delay:.0f}s")
            await asyncio.sleep(delay)

    async def _session(self, target: DiscoveredDevice) -> None:
        self._set_state(ConnectionState.CONNECTING)
        transport = create_transport(self._use_websocket)
        await transport.connect(target.host, target.port, timeout=self._connect_timeout_s)
        self._active_transport = transport
        self._disconnect_event = asyncio.Event()
        self._received = 0
        self._lost = 0
        self._peer_sequence = None
        try:
            self._set_state(ConnectionState.PAIRING)
            await self._send(transport, make_hello(self._client_id(), self._next_seq()))
            self._set_state(ConnectionState.CONNECTED)
            self._register_device(target)
            tasks = [
                asyncio.create_task(self._receive_loop(transport)),
                asyncio.create_task(self._heartbeat_loop(transport)),
                asyncio.create_task(self._telemetry_loop(transport)),
            ]
            try:
                await self._disconnect_event.wait()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await transport.close()
            self._active_transport = None
            self._set_state(ConnectionState.DISCONNECTED)
            self._status.connection = ConnectionState.DISCONNECTED

    # -- loops -------------------------------------------------------------

    async def _receive_loop(self, transport: Transport) -> None:
        while not self._stop.is_set():
            try:
                raw = await transport.receive(timeout=self._heartbeat_interval_s)
            except Exception as exc:
                self._set_state(ConnectionState.ERROR, str(exc))
                break
            if raw is None:
                self._check_stale()
                continue
            self._on_packet(raw)

    async def _heartbeat_loop(self, transport: Transport) -> None:
        while not self._stop.is_set():
            self._pending_heartbeat_at = time.monotonic()
            with contextlib.suppress(Exception):
                await self._send(transport, make_heartbeat(self._client_id(), self._next_seq()))
            await asyncio.sleep(self._heartbeat_interval_s)

    async def _telemetry_loop(self, transport: Transport) -> None:
        while not self._stop.is_set():
            supplier = self._telemetry_supplier
            if supplier is not None:
                try:
                    values = supplier() or {}
                    if values:
                        packet = make_telemetry(self._client_id(), self._next_seq(), values)
                        await self._send(transport, packet)
                except Exception:
                    _log.exception("telemetry push failed")
            await asyncio.sleep(0.5)

    # -- packet handling ---------------------------------------------------

    def _on_packet(self, raw: bytes) -> None:
        try:
            packet = decode_packet(
                raw,
                key=self._secret,
                require_signature=bool(self._secret) and self._require_auth,
                check_clock=False,
            )
        except Exception as exc:
            _log.debug("rejected inbound packet: %s", exc)
            self._set_state(ConnectionState.DEGRADED, f"invalid packet: {exc}")
            return

        self._account_sequence(packet.sequence)
        self._received += 1
        if self._pending_heartbeat_at is not None:
            self._status.latency_ms = round(
                (time.monotonic() - self._pending_heartbeat_at) * 1000.0, 2
            )
            self._pending_heartbeat_at = None
        self._status.last_packet_at = utc_now()
        self._status.packet_loss_percent = self.packet_loss_percent

        if packet.message_type in {MessageType.STATUS, MessageType.PAIR_ACK, MessageType.HELLO}:
            self._apply_status(packet)
        elif packet.message_type is MessageType.ERROR:
            self._set_state(ConnectionState.DEGRADED, str(packet.data.get("message", "error")))

        self._set_state(ConnectionState.CONNECTED)
        self._bus.publish(TOPIC_STATUS, self._status)

    def _apply_status(self, packet: Packet) -> None:
        try:
            data = validate_status(packet)
        except Exception as exc:
            _log.debug("invalid status payload: %s", exc)
            return
        status = self._status
        status.device_id = packet.device_id or status.device_id
        status.fan_target_percent = _opt(data.get("fanTargetPercent"), status.fan_target_percent)
        status.fan_pwm_percent = _opt(data.get("fanPwmPercent"), status.fan_pwm_percent)
        status.fan_rpm = _opt(data.get("fanRpm"), status.fan_rpm)
        status.fan_voltage_v = _opt(data.get("fanVoltage"), status.fan_voltage_v)
        status.fan_current_a = _opt(data.get("fanCurrent"), status.fan_current_a)
        status.fan_power_w = _opt(data.get("fanPower"), status.fan_power_w)
        status.uptime_s = _opt(data.get("uptime"), status.uptime_s)
        status.wifi_rssi_dbm = _opt(data.get("wifiRssi"), status.wifi_rssi_dbm)
        if "firmwareVersion" in data:
            status.firmware_version = str(data["firmwareVersion"])
        if "controllerMode" in data:
            status.mode = ControllerMode.coerce(data["controllerMode"], status.mode)
        if "faultState" in data:
            status.fault = FaultState.coerce(data["faultState"], status.fault)
        status.protocol_version = int(data.get("protocolVersion", PROTOCOL_VERSION))
        status.is_mock = bool(self._device and self._device.is_mock)
        status.connection = ConnectionState.CONNECTED
        status.health = (
            HealthStatus.ERROR
            if status.fault not in {FaultState.NONE, FaultState.UNKNOWN}
            else HealthStatus.HEALTHY
        )

    def _account_sequence(self, sequence: int) -> None:
        if self._peer_sequence is None:
            self._peer_sequence = sequence
            return
        if sequence <= self._peer_sequence:
            return
        gap = sequence - self._peer_sequence - 1
        if gap > 0:
            self._lost += min(gap, 1000)
        self._peer_sequence = sequence

    def _check_stale(self) -> None:
        last = self._status.last_packet_at
        if last is None:
            return
        age = (utc_now() - last).total_seconds()
        if age >= STALE_AFTER_S and self._state is not ConnectionState.STALE:
            self._set_state(ConnectionState.STALE, "no packets received")
            self._status.health = HealthStatus.WARNING
        elif DEGRADED_AFTER_S <= age < STALE_AFTER_S and self._state is ConnectionState.CONNECTED:
            self._set_state(ConnectionState.DEGRADED, "packets delayed")
            self._status.health = HealthStatus.WARNING

    # -- helpers -----------------------------------------------------------

    async def _send(self, transport: Transport, packet: Packet) -> None:
        await transport.send(encode_packet(packet, self._secret))

    async def _send_command_async(self, command: str, arguments: dict[str, object]) -> None:
        from app.protocol.messages import make_command

        packet = make_command(self._client_id(), self._next_seq(), command, **arguments)
        transport = getattr(self, "_active_transport", None)
        if transport is None:
            return
        with contextlib.suppress(Exception):
            await transport.send(encode_packet(packet, self._secret))

    def _client_id(self) -> str:
        return "windows-telemetry"

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    def _register_device(self, target: DiscoveredDevice) -> None:
        device = Esp32Device(
            device_id=target.device_id,
            nickname=target.nickname,
            host=target.host,
            port=target.port,
            firmware_version=target.firmware_version,
            protocol_version=target.protocol_version,
            last_seen=utc_now(),
            trusted=True,
            is_mock=target.is_mock,
        )
        with self._lock:
            self._device = device
        self._status.device_id = device.device_id
        self._status.is_mock = device.is_mock
        self._bus.publish(TOPIC_DEVICE, device)

    def _set_state(self, state: ConnectionState, detail: str = "") -> None:
        if state is self._state and not detail:
            return
        self._state = state
        self._status.connection = state
        self._bus.publish(TOPIC_STATE, {"state": state, "detail": detail})


def _opt(value: object, fallback: float | None) -> float | None:
    return fallback if value is None else float(value)  # type: ignore[arg-type]


__all__ = [
    "ConnectionManager",
    "TOPIC_STATE",
    "TOPIC_STATUS",
    "TOPIC_DEVICE",
    "DEGRADED_AFTER_S",
    "STALE_AFTER_S",
]
