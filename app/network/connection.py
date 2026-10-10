"""ESP32 connection manager (spec §43, §47, §48, §207, §208).

Runs the asynchronous protocol session on a dedicated event-loop thread so the
Qt UI thread is never blocked.  It owns:

* a single authoritative :class:`~app.core.enums.ConnectionState` machine,
* connect / pair / disconnect / stop, all idempotent and cancellable,
* a heartbeat loop that measures *real* round-trip latency and detects a missed
  heartbeat,
* a telemetry loop that pushes laptop telemetry to the controller,
* a receive loop that validates inbound packets and updates status,
* packet-loss accounting from sequence gaps,
* bounded exponential reconnect backoff with jitter (no retry storms, spec §48),
* monotonic instrumentation (see :class:`ConnectionDiagnostics`).

The manager publishes state/status changes on the shared :class:`EventBus` and
exposes the latest fan metrics as :class:`~app.core.enums.MetricKey` values,
which the telemetry engine merges into its snapshots.  This is the clean seam
between *Windows telemetry* and *ESP32 control* required by spec §3.

Reliability guarantees
----------------------
Every session runs under a monotonic **epoch**.  ``_set_state`` from a session
whose epoch is no longer current is a no-op, so a cancelled session's ``finally``
can never overwrite the state of the session that replaced it.  A new explicit
target or a stop also bumps the epoch, which is what makes connect/stop
cancellable even while a connection attempt is in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app import PROTOCOL_VERSION
from app.core.clock import utc_now
from app.core.enums import (
    ACTIVE_CONNECTION_STATES,
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
from app.core.errors import NetworkError
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

_DISCONNECTED = ConnectionState.DISCONNECTED
_CONNECTING = ConnectionState.CONNECTING
_CONNECTED = ConnectionState.CONNECTED
_DEGRADED = ConnectionState.DEGRADED
_STALE = ConnectionState.STALE
_RECONNECTING = ConnectionState.RECONNECTING
_STOPPING = ConnectionState.STOPPING
_ERROR = ConnectionState.ERROR
_UNAUTHORIZED = ConnectionState.UNAUTHORIZED
_MISMATCH = ConnectionState.PROTOCOL_MISMATCH

#: Failure categories that a retry cannot fix; mapped to the terminal state the
#: manager should settle in so the UI shows an actionable reason, not a spin.
_FATAL_CATEGORIES: dict[str, ConnectionState] = {
    "protocol_mismatch": _MISMATCH,
    "authentication": _UNAUTHORIZED,
}
_DISCOVERING = ConnectionState.DISCOVERING
_PAIRING = ConnectionState.PAIRING

#: Legal state transitions.  STOPPING is terminal except for a final return to
#: DISCONNECTED; everything else is permissive because a reconnect attempt may
#: legitimately re-enter CONNECTING from any live state.
VALID_TRANSITIONS: dict[ConnectionState, frozenset[ConnectionState]] = {
    _DISCONNECTED: frozenset(
        {_DISCOVERING, _CONNECTING, _RECONNECTING, _STOPPING, _ERROR, _UNAUTHORIZED, _MISMATCH}
    ),
    _DISCOVERING: frozenset(
        {_CONNECTING, _DISCONNECTED, _RECONNECTING, _STOPPING, _ERROR}
    ),
    _CONNECTING: frozenset(
        {_PAIRING, _CONNECTED, _DEGRADED, _STALE, _DISCONNECTED, _RECONNECTING, _STOPPING, _ERROR, _UNAUTHORIZED, _MISMATCH}
    ),
    _PAIRING: frozenset(
        {_CONNECTED, _DEGRADED, _STALE, _DISCONNECTED, _RECONNECTING, _STOPPING, _ERROR, _UNAUTHORIZED, _MISMATCH}
    ),
    _CONNECTED: frozenset(
        {_DEGRADED, _STALE, _CONNECTING, _DISCONNECTED, _RECONNECTING, _STOPPING, _ERROR}
    ),
    _DEGRADED: frozenset(
        {_CONNECTED, _STALE, _CONNECTING, _DISCONNECTED, _RECONNECTING, _STOPPING, _ERROR, _UNAUTHORIZED, _MISMATCH}
    ),
    _STALE: frozenset(
        {_CONNECTED, _DEGRADED, _CONNECTING, _DISCONNECTED, _RECONNECTING, _STOPPING, _ERROR, _UNAUTHORIZED, _MISMATCH}
    ),
    _RECONNECTING: frozenset({_DISCOVERING, _CONNECTING, _DISCONNECTED, _STOPPING, _ERROR}),
    _STOPPING: frozenset({_DISCONNECTED, _STOPPING}),
    _ERROR: frozenset({_DISCOVERING, _CONNECTING, _RECONNECTING, _DISCONNECTED, _STOPPING, _ERROR}),
    _UNAUTHORIZED: frozenset({_DISCONNECTED, _CONNECTING, _STOPPING, _ERROR}),
    _MISMATCH: frozenset({_DISCONNECTED, _CONNECTING, _STOPPING, _ERROR}),
}


def can_transition(old: ConnectionState, new: ConnectionState) -> bool:
    """Whether ``old -> new`` is a legal transition."""
    if old is new:
        return True
    return new in VALID_TRANSITIONS.get(old, frozenset())


class _SessionAbort(Exception):
    """Internal: end the current session with a category and a human reason."""

    def __init__(self, reason: str, category: str = "transport") -> None:
        super().__init__(reason)
        self.category = category


@dataclass(slots=True)
class ConnectionDiagnostics:
    """Monotonic measurements for the connection pipeline (spec §106).

    Every duration is measured with :func:`time.monotonic` so a wall-clock
    adjustment cannot corrupt it.  Values that were never observed stay ``None``
    — the UI must show "—", never a fabricated zero.
    """

    transport: str = ""
    endpoint: str = ""
    discovery_ms: float | None = None
    connect_ms: float | None = None
    handshake_ms: float | None = None
    time_to_first_telemetry_ms: float | None = None
    last_packet_monotonic: float | None = None
    last_error: str = ""
    last_error_category: str = ""
    consecutive_failures: int = 0
    reconnect_count: int = 0
    sessions_started: int = 0
    malformed_packets: int = 0
    timeouts: int = 0
    heartbeat_timeouts: int = 0
    invalid_transitions: int = 0

    def telemetry_age_s(self) -> float | None:
        """Seconds since the last valid packet, or ``None`` if never seen."""
        if self.last_packet_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self.last_packet_monotonic)

    def to_dict(self) -> dict[str, object]:
        return {
            "transport": self.transport,
            "endpoint": self.endpoint,
            "discovery_ms": self.discovery_ms,
            "connect_ms": self.connect_ms,
            "handshake_ms": self.handshake_ms,
            "time_to_first_telemetry_ms": self.time_to_first_telemetry_ms,
            "telemetry_age_s": self.telemetry_age_s(),
            "last_error": self.last_error,
            "last_error_category": self.last_error_category,
            "consecutive_failures": self.consecutive_failures,
            "reconnect_count": self.reconnect_count,
            "sessions_started": self.sessions_started,
            "malformed_packets": self.malformed_packets,
            "timeouts": self.timeouts,
            "heartbeat_timeouts": self.heartbeat_timeouts,
            "invalid_transitions": self.invalid_transitions,
        }


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
        reconnect_jitter: float = 0.25,
        healthy_reset_s: float = 5.0,
        require_authentication: bool = True,
    ) -> None:
        self._bus = bus
        self._use_websocket = use_websocket
        self._heartbeat_interval_s = heartbeat_interval_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._backoff_schedule = backoff_schedule or [1.0, 2.0, 4.0, 8.0, 15.0, 30.0]
        self._reconnect_jitter = max(0.0, min(1.0, reconnect_jitter))
        self._healthy_reset_s = healthy_reset_s
        self._require_auth = require_authentication

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._desired: DiscoveredDevice | None = None
        self._device: Esp32Device | None = None
        self._status = Esp32Status()
        self._state = ConnectionState.DISCONNECTED
        self._last_detail = ""
        self._lock = threading.RLock()
        self._secret: bytes | None = None
        self._sequence = 0
        self._telemetry_supplier: Callable[[], dict[str, object]] | None = None
        self._mock: MockEsp32 | None = None
        self._peer_sequence: int | None = None
        self._received = 0
        self._lost = 0
        self._want_connect = False
        self._stopping = False
        #: Bumped on every explicit request AND every session attempt.  A
        #: publisher whose epoch is stale is silently dropped.
        self._epoch = 0
        self._backoff_attempt = 0
        self._healthy_since: float | None = None
        self._session_t0 = 0.0
        self._pending_heartbeats: deque[tuple[int, float]] = deque(maxlen=16)
        self._disconnect_event: asyncio.Event | None = None
        self._active_transport: Transport | None = None
        self._diag = ConnectionDiagnostics()
        self._rng = random.Random(0xC0FFEE)

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
    def diagnostics(self) -> ConnectionDiagnostics:
        return self._diag

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
            self._stopping = False
            self._thread = threading.Thread(
                target=self._thread_main, name="Esp32Connection", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the connection thread.  Idempotent and safe during shutdown."""
        with self._lock:
            already = self._stopping and self._thread is None
            self._stopping = True
            self._want_connect = False
            self._desired = None
            self._epoch += 1
        if already:
            return
        self._request_disconnect()
        self._set_state(_STOPPING, "", epoch=None)
        self._stop.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            mock = self._mock
            if mock is not None:
                with contextlib.suppress(RuntimeError):
                    future = asyncio.run_coroutine_threadsafe(mock.stop(), loop)
                    with contextlib.suppress(Exception):
                        future.result(timeout=timeout)
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(lambda: None)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
            self._mock = None

    def connect_device(self, device: DiscoveredDevice | Esp32Device) -> None:
        """Request a connection to a device (async; returns immediately)."""
        target = self._as_target(device)
        with self._lock:
            if (
                self._want_connect
                and self._desired is not None
                and self._desired.address == target.address
                and self._state in ACTIVE_CONNECTION_STATES
            ):
                # Duplicate request for the device we are already bringing up.
                return
            self._desired = target
            self._want_connect = True
            self._stopping = False
            self._epoch += 1
            self._diag.consecutive_failures = 0
        self.start()
        self._request_disconnect()  # pre-empt any in-flight attempt/session
        self._set_state(_CONNECTING, "", epoch=None)

    def disconnect(self) -> None:
        """Drop the current session and stop reconnecting.  Idempotent."""
        with self._lock:
            self._want_connect = False
            self._desired = None
            self._epoch += 1
        self._request_disconnect()
        self._set_state(_DISCONNECTED, "", epoch=None)

    def forget_device(self) -> None:
        self.disconnect()
        with self._lock:
            self._device = None

    @staticmethod
    def _as_target(device: DiscoveredDevice | Esp32Device) -> DiscoveredDevice:
        if isinstance(device, Esp32Device):
            return DiscoveredDevice(
                device_id=device.device_id,
                host=device.host,
                port=device.port,
                nickname=device.nickname,
                firmware_version=device.firmware_version,
                protocol_version=device.protocol_version,
                is_mock=device.is_mock,
            )
        return device

    def _request_disconnect(self) -> None:
        """Signal the running session to end (thread-safe)."""
        loop = self._loop
        event = self._disconnect_event
        if loop is not None and loop.is_running() and event is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(event.set)

    # -- commands ----------------------------------------------------------

    def send_command(self, command: str, **arguments: object) -> bool:
        """Queue a command to the controller.  Returns whether it was queued."""
        loop = self._loop
        if loop is None or not loop.is_running():
            _log.warning("cannot send command %s: connection loop is not running", command)
            return False
        coro = self._send_command_async(command, arguments)
        asyncio.run_coroutine_threadsafe(coro, loop)
        return True

    # -- mock hosting ------------------------------------------------------

    def enable_mock(self, *, host: str = "127.0.0.1", port: int = 8765) -> DiscoveredDevice:
        """Start an in-process mock controller and return its device handle.

        The server is started on the connection thread's event loop and this
        call does not return until the socket is actually listening, so the
        caller may connect immediately without racing the loop start-up.

        Calling this again replaces the previous mock; the old server is shut
        down first so its listening socket and simulation tasks cannot leak.
        """
        previous = self._mock
        if previous is not None:
            self._stop_mock(previous)

        mock = MockEsp32(host=host, port=port)
        self._mock = mock
        self.start()
        if not self._start_mock_server(mock):
            raise RuntimeError("the connection loop did not come up; mock ESP32 not listening")
        return DiscoveredDevice(
            device_id=mock.device_id,
            host=host,
            port=port,
            nickname=mock.nickname,
            firmware_version=mock.firmware,
            is_mock=True,
        )

    def _start_mock_server(self, mock: MockEsp32, timeout: float = 10.0) -> bool:
        """Run ``mock.start()`` on the connection loop and wait until it is bound."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            loop = self._loop
            if loop is not None and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(mock.start(), loop)
                try:
                    future.result(timeout=timeout)
                except Exception:
                    _log.exception("mock ESP32 failed to start")
                    return False
                return True
            time.sleep(0.02)
        return False

    def _stop_mock(self, mock: MockEsp32, timeout: float = 5.0) -> None:
        """Stop ``mock`` on the connection loop and wait for it to close.

        Used when a mock is replaced and during :meth:`stop`, so no listening
        socket or simulation task can survive its owner.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        with contextlib.suppress(RuntimeError):
            future = asyncio.run_coroutine_threadsafe(mock.stop(), loop)
            with contextlib.suppress(Exception):
                future.result(timeout=timeout)

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
        snapshot: dict[str, object] = {
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
        snapshot.update(self._diag.to_dict())
        return snapshot

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
        while not self._stop.is_set():
            with self._lock:
                target = self._desired
                want = self._want_connect
            if target is None or not want:
                await asyncio.sleep(0.2)
                continue

            attempt_started = time.monotonic()
            healthy = False
            fatal = ""
            try:
                await self._session(target)
                healthy = self._session_was_healthy(attempt_started)
            except asyncio.CancelledError:
                raise
            except _SessionAbort as abort:
                self._record_failure(str(abort), abort.category)
                fatal = abort.category
                healthy = self._session_was_healthy(attempt_started)
            except Exception as exc:  # one failed attempt must not kill the loop
                category = _categorise(exc)
                self._record_failure(str(exc), category)
                fatal = category
                _log.info("ESP32 session ended: %s", exc)

            # A protocol-version or authentication failure will not fix itself
            # by retrying: surface it and stop until the user acts, instead of
            # hammering a controller that can never accept us.
            if fatal in _FATAL_CATEGORIES:
                with self._lock:
                    self._want_connect = False
                    detail = self._diag.last_error
                self._set_state(_FATAL_CATEGORIES[fatal], detail, epoch=None)
                _log.warning("ESP32 link halted: %s", detail)
                continue

            with self._lock:
                still_wanted = self._want_connect and self._desired is not None
            if self._stop.is_set() or not still_wanted:
                continue

            if healthy:
                self._backoff_attempt = 0
            delay = self._next_backoff_delay()
            self._backoff_attempt += 1
            with self._lock:
                self._diag.reconnect_count += 1
            epoch = self._new_epoch()
            self._set_state(_RECONNECTING, f"retrying in {delay:.1f}s", epoch=epoch)
            await asyncio.sleep(delay)

    def _session_was_healthy(self, attempt_started: float) -> bool:
        since = self._healthy_since
        if since is None:
            return False
        # The session counts as healthy only if it stayed up past the reset
        # threshold; a connect-and-drop flapping loop then escalates backoff.
        return since >= attempt_started and (time.monotonic() - since) >= self._healthy_reset_s

    def _next_backoff_delay(self) -> float:
        index = min(self._backoff_attempt, len(self._backoff_schedule) - 1)
        base = self._backoff_schedule[index]
        jitter = 0.0
        if self._reconnect_jitter:
            jitter = self._rng.uniform(0.0, self._reconnect_jitter * base)
        return round(base + jitter, 3)

    async def _session(self, target: DiscoveredDevice) -> None:
        epoch = self._new_epoch()
        with self._lock:
            self._diag.sessions_started += 1
        self._set_state(_CONNECTING, "", epoch=epoch)
        transport = create_transport(self._use_websocket)
        self._diag.transport = "websocket" if self._use_websocket else "tcp"
        self._diag.endpoint = target.address

        disconnect = asyncio.Event()
        with self._lock:
            self._disconnect_event = disconnect

        connect_started = time.monotonic()
        try:
            await self._connect_with_cancel(transport, target, disconnect)
        except _SessionAbort:
            await transport.close()
            raise
        except Exception as exc:
            await transport.close()
            if isinstance(exc, TimeoutError):
                with self._lock:
                    self._diag.timeouts += 1
            raise
        self._diag.connect_ms = round((time.monotonic() - connect_started) * 1000.0, 2)

        with self._lock:
            self._active_transport = transport
            self._received = 0
            self._lost = 0
            self._peer_sequence = None
            self._healthy_since = None
            self._session_t0 = time.monotonic()
            self._pending_heartbeats.clear()
            # Do not let a previous session's freshness make a silent new
            # session look alive: clear the last-packet clock and latency.
            self._status.last_packet_at = None
            self._status.latency_ms = None

        handshake_started = time.monotonic()
        try:
            self._set_state(_PAIRING, "", epoch=epoch)
            await self._send(transport, make_hello(self._client_id(), self._next_seq()))
            self._register_device(target)
            self._diag.handshake_ms = round((time.monotonic() - handshake_started) * 1000.0, 2)

            # The transport is up, but a controller that never answers is *not*
            # connected.  Stay in DEGRADED until the first valid packet arrives
            # (``_on_packet`` promotes to CONNECTED); a silent peer then trips
            # the heartbeat timeout instead of showing a false green.
            self._set_state(_DEGRADED, "awaiting first telemetry", epoch=epoch)

            tasks = [
                asyncio.create_task(self._receive_loop(transport, disconnect, epoch)),
                asyncio.create_task(self._heartbeat_loop(transport, disconnect, epoch)),
                asyncio.create_task(self._telemetry_loop(transport, epoch)),
            ]
            disconnect_task = asyncio.create_task(disconnect.wait())
            try:
                done, _pending = await asyncio.wait(
                    [*tasks, disconnect_task], return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    if finished is disconnect_task or finished.cancelled():
                        continue
                    error = finished.exception()
                    if error is not None:
                        raise error
            finally:
                for task in [*tasks, disconnect_task]:
                    task.cancel()
                await asyncio.gather(*tasks, disconnect_task, return_exceptions=True)
        finally:
            await transport.close()
            with self._lock:
                self._active_transport = None
                if self._disconnect_event is disconnect:
                    self._disconnect_event = None
            # Do not clobber a newer session's state: honour the epoch guard.
            self._set_state(_DISCONNECTED, "", epoch=epoch)

    async def _connect_with_cancel(
        self, transport: Transport, target: DiscoveredDevice, disconnect: asyncio.Event
    ) -> None:
        """Connect, but abort immediately if a disconnect/stop is requested."""
        connect_task = asyncio.create_task(
            transport.connect(target.host, target.port, timeout=self._connect_timeout_s)
        )
        disconnect_task = asyncio.create_task(disconnect.wait())
        done: set[asyncio.Task] = set()
        try:
            done, _pending = await asyncio.wait(
                [connect_task, disconnect_task], return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (connect_task, disconnect_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(connect_task, disconnect_task, return_exceptions=True)
        if disconnect_task in done and disconnect.is_set():
            raise _SessionAbort("connect cancelled", "cancelled")
        if connect_task in done:
            return connect_task.result()
        # Only reachable if ``asyncio.wait`` returned without completing either
        # task, which it never does; treat it as a cancellation for safety.
        raise _SessionAbort("connect did not complete", "cancelled")

    # -- loops -------------------------------------------------------------

    async def _receive_loop(
        self, transport: Transport, disconnect: asyncio.Event, epoch: int
    ) -> None:
        while not self._stop.is_set():
            try:
                raw = await transport.receive(timeout=self._heartbeat_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _SessionAbort(f"receive failed: {exc}", "transport") from exc
            if raw is None:
                self._check_stale(epoch)
                continue
            self._on_packet(raw, epoch)
            if disconnect.is_set():
                return

    async def _heartbeat_loop(
        self, transport: Transport, disconnect: asyncio.Event, epoch: int
    ) -> None:
        degrade_after = self._heartbeat_timeout_s
        while not self._stop.is_set():
            sequence = self._next_seq()
            sent = time.monotonic()
            self._pending_heartbeats.append((sequence, sent))
            with contextlib.suppress(Exception):
                await self._send(
                    transport, make_heartbeat(self._client_id(), sequence, echo=sequence)
                )
            # Before the first packet, allow the connect budget for a slow
            # controller to come up; afterwards a missed heartbeat is a fault.
            if self._healthy_since is None:
                abort_after = self._connect_timeout_s + self._heartbeat_timeout_s
            else:
                abort_after = self._heartbeat_timeout_s + max(1.0, self._heartbeat_timeout_s)
            age = self._oldest_heartbeat_age()
            if age is not None and age >= abort_after:
                with self._lock:
                    self._diag.heartbeat_timeouts += 1
                raise _SessionAbort(f"heartbeat unanswered for {age:.1f}s", "heartbeat_timeout")
            if (
                age is not None
                and age >= degrade_after
                and self._healthy_since is not None
            ):
                with self._lock:
                    self._diag.heartbeat_timeouts += 1
                self._set_state(_DEGRADED, "heartbeat timeout", epoch=epoch)
            await asyncio.sleep(self._heartbeat_interval_s)

    async def _telemetry_loop(self, transport: Transport, epoch: int) -> None:
        while not self._stop.is_set():
            supplier = self._telemetry_supplier
            if supplier is not None:
                try:
                    values = supplier() or {}
                    if values:
                        packet = make_telemetry(self._client_id(), self._next_seq(), values)
                        await self._send(transport, packet)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log.exception("telemetry push failed")
            await asyncio.sleep(0.5)

    def _oldest_heartbeat_age(self) -> float | None:
        if not self._pending_heartbeats:
            return None
        _sequence, sent = self._pending_heartbeats[0]
        return time.monotonic() - sent

    # -- packet handling ---------------------------------------------------

    def _on_packet(self, raw: bytes, epoch: int) -> None:
        from app.core.errors import AuthenticationError, ProtocolMismatch

        try:
            packet = decode_packet(
                raw,
                key=self._secret,
                require_signature=bool(self._secret) and self._require_auth,
                check_clock=False,
            )
        except ProtocolMismatch as exc:
            # A peer we can never talk to: abort the session so the run loop
            # settles in a terminal state instead of silently retrying forever.
            raise _SessionAbort(str(exc), "protocol_mismatch") from exc
        except AuthenticationError as exc:
            raise _SessionAbort(str(exc), "authentication") from exc
        except Exception as exc:
            with self._lock:
                self._diag.malformed_packets += 1
            _log.debug("rejected inbound packet: %s", exc)
            self._set_state(_DEGRADED, f"invalid packet: {exc}", epoch=epoch)
            return

        now = time.monotonic()
        with self._lock:
            self._account_sequence(packet.sequence)
            self._received += 1
            self._resolve_latency(packet, now)
            self._status.last_packet_at = utc_now()
            self._status.packet_loss_percent = self.packet_loss_percent
            self._diag.last_packet_monotonic = now
            if self._healthy_since is None:
                self._healthy_since = now
                self._diag.time_to_first_telemetry_ms = round(
                    max(0.0, now - self._session_t0) * 1000.0, 2
                )

        # A peer-reported ERROR means the controller is up but unhealthy; it must
        # not be overwritten by the generic promotion below in the same call.
        promote = True
        if packet.message_type in {MessageType.STATUS, MessageType.PAIR_ACK, MessageType.HELLO}:
            self._apply_status(packet)
        elif packet.message_type is MessageType.ERROR:
            promote = False
            self._set_state(
                _DEGRADED, str(packet.data.get("message", "error")), epoch=epoch
            )

        # Only a *live* session may declare CONNECTED, and only for a packet that
        # actually carried a usable status.
        if promote and self._state is not ConnectionState.CONNECTED:
            self._set_state(_CONNECTED, "telemetry", epoch=epoch)
        self._bus.publish(TOPIC_STATUS, self._status)

    def _resolve_latency(self, packet: Packet, now: float) -> None:
        """Record a real round-trip time when a heartbeat is answered."""
        if not self._pending_heartbeats:
            return
        echo = packet.data.get("echo")
        if isinstance(echo, int):
            for index, (sequence, sent) in enumerate(self._pending_heartbeats):
                if sequence == echo:
                    del self._pending_heartbeats[index]
                    self._status.latency_ms = round((now - sent) * 1000.0, 2)
                    return
        if packet.message_type in {MessageType.STATUS, MessageType.HEARTBEAT}:
            _sequence, sent = self._pending_heartbeats.popleft()
            self._status.latency_ms = round((now - sent) * 1000.0, 2)

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

    def _check_stale(self, epoch: int) -> None:
        last = self._status.last_packet_at
        if last is None:
            return
        age = (utc_now() - last).total_seconds()
        if age >= STALE_AFTER_S and self._state is not ConnectionState.STALE:
            self._set_state(_STALE, "no packets received", epoch=epoch)
            self._status.health = HealthStatus.WARNING
        elif DEGRADED_AFTER_S <= age < STALE_AFTER_S and self._state is ConnectionState.CONNECTED:
            self._set_state(_DEGRADED, "packets delayed", epoch=epoch)
            self._status.health = HealthStatus.WARNING

    # -- helpers -----------------------------------------------------------

    async def _send(self, transport: Transport, packet: Packet) -> None:
        await transport.send(encode_packet(packet, self._secret))

    async def _send_command_async(self, command: str, arguments: dict[str, object]) -> None:
        from app.protocol.messages import make_command

        packet = make_command(self._client_id(), self._next_seq(), command, **arguments)
        transport = self._active_transport
        if transport is None:
            return
        with contextlib.suppress(Exception):
            await transport.send(encode_packet(packet, self._secret))

    def _client_id(self) -> str:
        return "windows-telemetry"

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    def _new_epoch(self) -> int:
        with self._lock:
            self._epoch += 1
            return self._epoch

    def _record_failure(self, message: str, category: str) -> None:
        with self._lock:
            self._diag.last_error = message[:200]
            self._diag.last_error_category = category
            self._diag.consecutive_failures += 1

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

    def _set_state(self, state: ConnectionState, detail: str = "", *, epoch: int | None) -> None:
        with self._lock:
            if epoch is not None and epoch != self._epoch:
                return
            if state is self._state and detail == self._last_detail:
                return
            if not can_transition(self._state, state):
                self._diag.invalid_transitions += 1
                _log.debug("ignored invalid transition %s -> %s", self._state.value, state.value)
                return
            self._state = state
            self._last_detail = detail
            self._status.connection = state
        self._bus.publish(TOPIC_STATE, {"state": state, "detail": detail})


def _categorise(exc: Exception) -> str:
    from app.core.errors import AuthenticationError, ProtocolMismatch

    if isinstance(exc, AuthenticationError):
        return "authentication"
    if isinstance(exc, ProtocolMismatch):
        return "protocol_mismatch"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, NetworkError):
        return "transport"
    return "internal"


def _opt(value: object, fallback: float | None) -> float | None:
    return fallback if value is None else float(value)  # type: ignore[arg-type]


__all__ = [
    "ConnectionManager",
    "ConnectionDiagnostics",
    "can_transition",
    "VALID_TRANSITIONS",
    "TOPIC_STATE",
    "TOPIC_STATUS",
    "TOPIC_DEVICE",
    "DEGRADED_AFTER_S",
    "STALE_AFTER_S",
]
