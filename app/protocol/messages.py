"""ESP32 wire protocol: message models, validation and serialisation.

Every packet shares a common envelope (spec §44)::

    {
      "v":   1,                 # protocol version
      "type":"TELEMETRY",       # MessageType
      "id":  "esp32-a1b2",      # device id
      "ts":  1727780000.123,    # UTC seconds
      "seq": 42,                # monotonically increasing sequence
      "mac": "…hex…",           # optional HMAC-SHA256 over the canonical body
      "data": { … }             # message specific payload
    }

Design rules
------------
* Unknown *optional* fields are ignored so a newer firmware never breaks an
  older client (spec §46, §234).
* An unknown *protocol version* is rejected with :class:`ProtocolMismatch`
  (spec §234).
* All inbound data is treated as untrusted: types, ranges and sizes are
  validated before use (spec §50, §155).
* The canonical byte representation used for HMAC is deterministic
  (sorted keys, no insignificant whitespace) so firmware can reproduce it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from app import PROTOCOL_VERSION
from app.core.enums import ControllerMode, FaultState, MessageType
from app.core.errors import AuthenticationError, ProtocolError, ProtocolMismatch

#: Hard cap on a single datagram/frame, protects against hostile input.
MAX_PACKET_BYTES = 8192
#: Reject packets whose timestamp is implausibly far from our clock.
MAX_CLOCK_SKEW_S = 120.0
#: Numeric ranges enforced for safety-relevant fields.
PWM_MIN, PWM_MAX = 0.0, 100.0
RPM_MIN, RPM_MAX = 0.0, 30000.0
VOLTAGE_MIN, VOLTAGE_MAX = 0.0, 30.0
CURRENT_MIN, CURRENT_MAX = 0.0, 20.0
TEMPERATURE_MIN, TEMPERATURE_MAX = -40.0, 150.0


@dataclass(slots=True)
class Packet:
    """A decoded protocol packet."""

    message_type: MessageType
    device_id: str = ""
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0
    protocol_version: int = PROTOCOL_VERSION
    data: dict[str, Any] = field(default_factory=dict)
    mac: str = ""

    def body(self) -> dict[str, Any]:
        """Envelope without the MAC — the material that is signed."""
        return {
            "v": self.protocol_version,
            "type": self.message_type.value,
            "id": self.device_id,
            "ts": round(float(self.timestamp), 3),
            "seq": int(self.sequence),
            "data": self.data,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic byte representation used for HMAC."""
        return json.dumps(
            self.body(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        payload = self.body()
        if self.mac:
            payload["mac"] = self.mac
        return payload

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")

    def sign(self, key: bytes) -> Packet:
        self.mac = hmac.new(key, self.canonical_bytes(), hashlib.sha256).hexdigest()
        return self

    def verify(self, key: bytes) -> None:
        if not self.mac:
            raise AuthenticationError("packet is not signed")
        expected = hmac.new(key, self.canonical_bytes(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, self.mac):
            raise AuthenticationError("packet signature mismatch")


# ---------------------------------------------------------------------------
# Encoding / decoding
# ---------------------------------------------------------------------------


def encode_packet(packet: Packet, key: bytes | None = None) -> bytes:
    """Serialise a packet, optionally signing it."""
    if key is not None:
        packet.sign(key)
    raw = packet.to_bytes()
    if len(raw) > MAX_PACKET_BYTES:
        raise ProtocolError(f"packet exceeds {MAX_PACKET_BYTES} bytes")
    return raw


def decode_packet(
    raw: bytes,
    *,
    key: bytes | None = None,
    require_signature: bool = False,
    check_clock: bool = True,
    max_clock_skew_s: float = MAX_CLOCK_SKEW_S,
) -> Packet:
    """Parse and validate an inbound packet.

    Raises :class:`ProtocolError` for anything malformed and
    :class:`AuthenticationError` when a required signature is missing/invalid.
    """
    if len(raw) > MAX_PACKET_BYTES:
        raise ProtocolError("packet too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"malformed packet: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("packet root must be an object")

    version = payload.get("v")
    if not isinstance(version, int):
        raise ProtocolError("missing protocol version")
    if version != PROTOCOL_VERSION:
        raise ProtocolMismatch(
            f"peer speaks protocol v{version}, this client speaks v{PROTOCOL_VERSION}"
        )

    message_type = payload.get("type")
    try:
        message = MessageType(message_type)
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"unknown message type: {message_type!r}") from exc

    device_id = payload.get("id", "")
    if not isinstance(device_id, str):
        raise ProtocolError("device id must be a string")

    timestamp = payload.get("ts", time.time())
    if not isinstance(timestamp, int | float) or math.isnan(float(timestamp)):
        raise ProtocolError("invalid timestamp")
    if check_clock and abs(time.time() - float(timestamp)) > max_clock_skew_s:
        raise ProtocolError("packet timestamp outside acceptable skew")

    sequence = payload.get("seq", 0)
    if not isinstance(sequence, int) or sequence < 0:
        raise ProtocolError("invalid sequence number")

    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ProtocolError("packet data must be an object")

    packet = Packet(
        message_type=message,
        device_id=device_id,
        timestamp=float(timestamp),
        sequence=sequence,
        protocol_version=version,
        data=data,
        mac=str(payload.get("mac", "")),
    )
    if key is not None and require_signature:
        packet.verify(key)
    return packet


# ---------------------------------------------------------------------------
# Validation of message payloads
# ---------------------------------------------------------------------------


def _number(value: Any, low: float, high: float, name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ProtocolError(f"{name} must be numeric")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ProtocolError(f"{name} is not finite")
    if not (low <= number <= high):
        raise ProtocolError(f"{name}={number} out of range [{low}, {high}]")
    return number


def validate_telemetry(packet: Packet) -> dict[str, Any]:
    """Validate and normalise a laptop-telemetry payload (spec §45)."""
    data = packet.data
    cleaned: dict[str, Any] = {}
    ranges = {
        "cpuTemperature": (TEMPERATURE_MIN, TEMPERATURE_MAX),
        "gpuTemperature": (TEMPERATURE_MIN, TEMPERATURE_MAX),
        "storageTemperature": (TEMPERATURE_MIN, TEMPERATURE_MAX),
        "cpuUsage": (0.0, 100.0),
        "gpuUsage": (0.0, 100.0),
        "ramUsage": (0.0, 100.0),
        "cpuPower": (0.0, 500.0),
        "gpuPower": (0.0, 1000.0),
        "batteryLevel": (0.0, 100.0),
        "thermalTrend": (-100.0, 100.0),
    }
    for name, (low, high) in ranges.items():
        if name in data:
            cleaned[name] = _number(data[name], low, high, name)
    if "batteryState" in data:
        state = data["batteryState"]
        if not isinstance(state, str):
            raise ProtocolError("batteryState must be a string")
        cleaned["batteryState"] = state[:32]
    return cleaned


def validate_status(packet: Packet) -> dict[str, Any]:
    """Validate an ESP32 status payload (spec §46)."""
    data = packet.data
    cleaned: dict[str, Any] = {}
    ranges = {
        "fanTargetPercent": (PWM_MIN, PWM_MAX),
        "fanPwmPercent": (PWM_MIN, PWM_MAX),
        "fanRpm": (RPM_MIN, RPM_MAX),
        "fanVoltage": (VOLTAGE_MIN, VOLTAGE_MAX),
        "fanCurrent": (CURRENT_MIN, CURRENT_MAX),
        "fanPower": (0.0, 500.0),
        "uptime": (0.0, 1e12),
        "wifiRssi": (-120.0, 0.0),
    }
    for name, (low, high) in ranges.items():
        if name in data:
            cleaned[name] = _number(data[name], low, high, name)
    if "controllerMode" in data:
        cleaned["controllerMode"] = ControllerMode.coerce(
            data["controllerMode"], ControllerMode.AUTO
        ).value
    if "faultState" in data:
        cleaned["faultState"] = FaultState.coerce(
            data["faultState"], FaultState.UNKNOWN
        ).value
    for name in ("firmwareVersion", "protocolVersion"):
        if name in data:
            cleaned[name] = data[name]
    return cleaned


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def make_telemetry(device_id: str, sequence: int, values: dict[str, Any]) -> Packet:
    """Build a laptop->ESP32 telemetry packet, omitting unavailable values."""
    clean = {k: v for k, v in values.items() if v is not None}
    return Packet(MessageType.TELEMETRY, device_id, time.time(), sequence, data=clean)


def make_command(
    device_id: str, sequence: int, command: str, **arguments: Any
) -> Packet:
    """Build a Windows->ESP32 command packet."""
    payload: dict[str, Any] = {"command": command}
    payload.update({k: v for k, v in arguments.items() if v is not None})
    return Packet(MessageType.COMMAND, device_id, time.time(), sequence, data=payload)


def make_heartbeat(device_id: str, sequence: int, *, echo: int | None = None) -> Packet:
    """Build a heartbeat.

    ``echo`` carries the client sequence number so a compliant peer can echo it
    back, which is what lets the client measure a *real* round-trip time instead
    of the spacing between unrelated packets.  Firmware that does not echo simply
    leaves the client's latency unknown rather than inventing a value.
    """
    data: dict[str, Any] = {} if echo is None else {"echo": int(echo)}
    return Packet(MessageType.HEARTBEAT, device_id, time.time(), sequence, data=data)


def make_hello(device_id: str, sequence: int, firmware: str = "") -> Packet:
    return Packet(
        MessageType.HELLO,
        device_id,
        time.time(),
        sequence,
        data={"firmwareVersion": firmware, "protocolVersion": PROTOCOL_VERSION},
    )


__all__ = [
    "Packet",
    "MessageType",
    "encode_packet",
    "decode_packet",
    "validate_telemetry",
    "validate_status",
    "make_telemetry",
    "make_command",
    "make_heartbeat",
    "make_hello",
    "MAX_PACKET_BYTES",
    "MAX_CLOCK_SKEW_S",
]
