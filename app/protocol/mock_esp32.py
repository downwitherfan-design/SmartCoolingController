"""Mock ESP32 controller (spec §53, §164, §296).

This is a fully in-process implementation of the ESP32 side of the protocol.
It speaks exactly the same envelopes as a real controller and runs a real
WebSocket server, so the connection manager, heartbeats, telemetry push and
command handling are all exercised without hardware.

It is *always* visibly marked: the device id begins with ``mock-``, the
:class:`~app.models.devices.Esp32Device` sets ``is_mock=True`` and the UI shows
a MOCK badge.  It never pretends to be a real controller.

The mock models a 4-wire PWM fan with a start-up dead band and a linear
PWM->RPM response, plus an INA219-style voltage/current monitor and a DS18B20
pad temperature sensor.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import struct
import threading
import time

from app import PROTOCOL_VERSION
from app.core.enums import ControllerMode, FaultState, MessageType
from app.core.logging_setup import get_logger
from app.protocol.messages import Packet, decode_packet, encode_packet

_log = get_logger(__name__)

MOCK_DEVICE_PREFIX = "mock-"


class MockEsp32:
    """A programmable fake controller that serves the real protocol."""

    def __init__(
        self,
        *,
        device_id: str = "mock-esp32-01",
        nickname: str = "Mock Controller",
        host: str = "127.0.0.1",
        port: int = 8765,
        firmware: str = "mock-1.0.0",
        secret: bytes | None = None,
        packet_loss: float = 0.0,
        latency_ms: float = 5.0,
    ) -> None:
        self.device_id = device_id
        self.nickname = nickname
        self.host = host
        self.port = port
        self.firmware = firmware
        self.secret = secret
        self.packet_loss = max(0.0, min(1.0, packet_loss))
        self.latency_ms = max(0.0, latency_ms)

        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = asyncio.Event()
        self._stop = asyncio.Event()

        self._rng = random.Random(0x5EED)
        self._tasks: set[asyncio.Task] = set()
        self._sequence = 0
        self._start_time = time.time()
        self._target_pwm = 0.0
        self._pwm = 0.0
        self._rpm = 0.0
        self._mode = ControllerMode.AUTO
        self._fault = FaultState.NONE
        self._last_command = time.time()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the WebSocket server on the current event loop."""
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        self._started.set()
        _log.info("mock ESP32 listening on ws://%s:%d", self.host, self.port)
        self._spawn(self._simulation_loop())
        self._spawn(self._heartbeat_loop())

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                client.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        _log.info("mock ESP32 stopped")

    # -- thread helpers ----------------------------------------------------

    def start_in_thread(self) -> None:
        """Run the mock server on a dedicated thread (for GUI embedding)."""
        import threading

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._spawn(self.start())
            with contextlib.suppress(Exception):
                loop.run_forever()

        self._thread = threading.Thread(target=_runner, name="MockEsp32", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.stop(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop)

    # -- controls ----------------------------------------------------------

    def set_packet_loss(self, fraction: float) -> None:
        self.packet_loss = max(0.0, min(1.0, fraction))

    def inject_fault(self, fault: FaultState) -> None:
        self._fault = fault

    def set_mode(self, mode: ControllerMode) -> None:
        self._mode = mode

    # -- server ------------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Minimal WebSocket server handshake + frame loop."""
        self._clients.add(writer)
        try:
            await self._websocket_handshake(reader, writer)
            await self._frame_loop(reader, writer)
        except Exception as exc:
            _log.debug("mock client ended: %s", exc)
        finally:
            self._clients.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    async def _websocket_handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        import base64
        import hashlib

        request = await reader.readuntil(b"\r\n\r\n")
        headers: dict[str, str] = {}
        for line in request.decode("latin-1").split("\r\n")[1:]:
            if ":" in line:
                header, value = line.split(":", 1)
                headers[header.strip().lower()] = value.strip()
        key = headers.get("sec-websocket-key")
        if key is None:
            raise ConnectionError("not a websocket handshake")
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        writer.write(response.encode("ascii"))
        await writer.drain()

    async def _frame_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while not self._stop.is_set():
            payload = await self._read_frame(reader)
            if payload is None:
                return
            await self._handle_payload(payload, writer)

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes | None:
        try:
            header = await reader.readexactly(2)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None
        first, second = header[0], header[1]
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", await reader.readexactly(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", await reader.readexactly(8))
        mask = await reader.readexactly(4) if masked else b""
        data = await reader.readexactly(length)
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 0x8:  # close
            return None
        if opcode == 0x9:  # ping -> pong handled by caller writing
            return b""
        return data

    async def _handle_payload(self, payload: bytes, writer: asyncio.StreamWriter) -> None:
        if not payload:
            return
        try:
            packet = decode_packet(
                payload, key=self.secret, require_signature=self.secret is not None,
                check_clock=False,
            )
        except Exception as exc:
            _log.debug("mock rejected packet: %s", exc)
            await self._send_error(writer, str(exc))
            return

        if packet.message_type is MessageType.HELLO:
            await self._send(writer, self._status_packet())
        elif packet.message_type is MessageType.PAIR:
            ack = Packet(
                MessageType.PAIR_ACK, self.device_id, time.time(), self._next_seq(),
                data={"accepted": True, "firmwareVersion": self.firmware},
            )
            await self._send(writer, ack)
        elif packet.message_type is MessageType.TELEMETRY:
            self._apply_telemetry(packet.data)
            await self._send(writer, self._status_packet())
        elif packet.message_type is MessageType.COMMAND:
            self._apply_command(packet.data)
            await self._send(writer, self._status_packet())
        elif packet.message_type is MessageType.HEARTBEAT or packet.message_type is MessageType.CONFIG:
            await self._send(writer, self._status_packet())
        elif packet.message_type is MessageType.GOODBYE:
            return

    async def _send(self, writer: asyncio.StreamWriter, packet: Packet) -> None:
        if self.packet_loss and self._rng.random() < self.packet_loss:
            return
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
        frame = _encode_ws_frame(encode_packet(packet, self.secret))
        writer.write(frame)
        with contextlib.suppress(Exception):
            await writer.drain()

    async def _send_error(self, writer: asyncio.StreamWriter, message: str) -> None:
        packet = Packet(
            MessageType.ERROR, self.device_id, time.time(), self._next_seq(),
            data={"message": message[:200]},
        )
        await self._send(writer, packet)

    async def _broadcast(self, packet: Packet) -> None:
        for writer in list(self._clients):
            with contextlib.suppress(Exception):
                await self._send(writer, packet)

    # -- simulation --------------------------------------------------------

    async def _simulation_loop(self) -> None:
        while not self._stop.is_set():
            self._step(0.1)
            await asyncio.sleep(0.1)

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            if self._clients:
                await self._broadcast(self._status_packet())

    def _step(self, dt: float) -> None:
        error = self._target_pwm - self._pwm
        self._pwm = max(0.0, min(100.0, self._pwm + max(-300.0 * dt, min(300.0 * dt, error))))
        if self._pwm < 8.0:
            target_rpm = 0.0
        else:
            target_rpm = 900.0 + (self._pwm - 8.0) / 92.0 * 6100.0
        self._rpm += (target_rpm - self._rpm) * min(1.0, dt * 3.0)
        self._rpm += self._rng.gauss(0, 8.0)
        if self._fault is FaultState.STALL:
            self._rpm = 0.0

    def _apply_telemetry(self, data: dict) -> None:
        # A real controller would adjust the fan here.  The mock simply records
        # the recommendation so the automatic mode is observable.
        if self._mode is ControllerMode.AUTO and "recommendedPwm" in data:
            with contextlib.suppress(TypeError, ValueError):
                self._target_pwm = float(data["recommendedPwm"])

    def _apply_command(self, data: dict) -> None:
        command = str(data.get("command", ""))
        self._last_command = time.time()
        if command == "setPwm":
            with contextlib.suppress(TypeError, ValueError):
                self._target_pwm = max(0.0, min(100.0, float(data.get("pwm", 0.0))))
            self._mode = ControllerMode.MANUAL
        elif command == "setMode":
            self._mode = ControllerMode.coerce(data.get("mode"), ControllerMode.AUTO)
        elif command == "clearFault":
            self._fault = FaultState.NONE
        elif command == "stop":
            self._target_pwm = 0.0
            self._mode = ControllerMode.MANUAL

    # -- packet builders ---------------------------------------------------

    def _status_packet(self) -> Packet:
        voltage = 12.0 * (self._pwm / 100.0)
        current = 0.08 + (self._pwm / 100.0) ** 1.6 * 0.62
        data = {
            "fanTargetPercent": round(self._target_pwm, 2),
            "fanPwmPercent": round(self._pwm, 2),
            "fanRpm": round(max(0.0, self._rpm), 1),
            "fanVoltage": round(voltage, 3),
            "fanCurrent": round(current, 3),
            "fanPower": round(voltage * current, 3),
            "controllerMode": self._mode.value,
            "faultState": self._fault.value,
            "uptime": round(time.time() - self._start_time, 1),
            "firmwareVersion": self.firmware,
            "protocolVersion": PROTOCOL_VERSION,
            "wifiRssi": round(-45 + self._rng.gauss(0, 3), 1),
            "nickname": self.nickname,
        }
        return Packet(MessageType.STATUS, self.device_id, time.time(), self._next_seq(), data=data)

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence


def _encode_ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """Encode a single, unmasked (server->client) WebSocket text frame."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + payload


__all__ = ["MockEsp32", "MOCK_DEVICE_PREFIX"]
