"""Unit tests for the ESP32 wire protocol and its security envelope.

The protocol is the app's untrusted boundary: every inbound byte must be
treated as hostile until decoded, range-checked and (when configured) verified
against an HMAC (spec §44-§47, §92, §93, §114).
"""

from __future__ import annotations

import json
import time

import pytest

from app.core.enums import MessageType
from app.core.errors import AuthenticationError, ProtocolError
from app.protocol.messages import (
    MAX_PACKET_BYTES,
    Packet,
    decode_packet,
    encode_packet,
    make_command,
    make_hello,
    make_telemetry,
    validate_status,
    validate_telemetry,
)

KEY = b"a-very-secret-pre-shared-key"


def test_packet_round_trips_through_encode_decode() -> None:
    packet = make_telemetry("dev-1", 7, {"cpuTemperature": 61.5, "cpuUsage": 33.0})
    decoded = decode_packet(encode_packet(packet))
    assert decoded.message_type is MessageType.TELEMETRY
    assert decoded.device_id == "dev-1"
    assert decoded.sequence == 7
    assert decoded.data["cpuTemperature"] == pytest.approx(61.5)


def test_canonical_bytes_are_deterministic_regardless_of_key_order() -> None:
    a = Packet(MessageType.TELEMETRY, "d", data={"b": 1, "a": 2})
    b = Packet(MessageType.TELEMETRY, "d", data={"a": 2, "b": 1})
    assert a.canonical_bytes() == b.canonical_bytes()


def test_signed_packet_verifies_with_the_same_key() -> None:
    packet = make_command("dev-1", 1, "setPwm", pwm=55)
    packet.sign(KEY)
    packet.verify(KEY)  # must not raise


def test_signature_verification_fails_with_a_wrong_key() -> None:
    packet = make_command("dev-1", 1, "setPwm", pwm=55)
    packet.sign(KEY)
    with pytest.raises(AuthenticationError):
        packet.verify(b"the-wrong-key")


def test_unsigned_packet_cannot_be_verified() -> None:
    with pytest.raises(AuthenticationError):
        Packet(MessageType.COMMAND, "d").verify(KEY)


def test_tampering_after_signing_is_detected() -> None:
    packet = make_command("dev-1", 1, "setPwm", pwm=30)
    packet.sign(KEY)
    packet.data["pwm"] = 100  # an attacker bumps the fan to full
    with pytest.raises(AuthenticationError):
        packet.verify(KEY)


def test_required_signature_rejects_an_unsigned_packet() -> None:
    raw = encode_packet(make_command("dev-1", 1, "setPwm", pwm=30))
    with pytest.raises(AuthenticationError):
        decode_packet(raw, key=KEY, require_signature=True)


def test_signed_packet_accepted_when_signature_required() -> None:
    raw = encode_packet(make_command("dev-1", 1, "setPwm", pwm=30), KEY)
    decoded = decode_packet(raw, key=KEY, require_signature=True)
    assert decoded.data["pwm"] == pytest.approx(30)


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_packet(b"{not json")


def test_non_object_root_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_packet(json.dumps([1, 2, 3]).encode())


def test_unknown_message_type_is_rejected() -> None:
    payload = {"v": 1, "type": "NONSENSE", "id": "d", "ts": time.time(), "seq": 1, "data": {}}
    with pytest.raises(ProtocolError):
        decode_packet(json.dumps(payload).encode())


def test_missing_protocol_version_is_rejected() -> None:
    payload = {"type": "TELEMETRY", "id": "d", "ts": time.time(), "seq": 1, "data": {}}
    with pytest.raises(ProtocolError):
        decode_packet(json.dumps(payload).encode())


def test_oversized_packet_is_rejected_on_encode() -> None:
    packet = make_telemetry("d", 1, {"blob": "x" * (MAX_PACKET_BYTES + 100)})
    with pytest.raises(ProtocolError):
        encode_packet(packet)


def test_oversized_packet_is_rejected_on_decode() -> None:
    with pytest.raises(ProtocolError):
        decode_packet(b"x" * (MAX_PACKET_BYTES + 1))


def test_out_of_range_temperature_is_rejected() -> None:
    packet = make_telemetry("d", 1, {"cpuTemperature": 5000.0})
    with pytest.raises(ProtocolError):
        validate_telemetry(packet)


def test_out_of_range_usage_is_rejected() -> None:
    packet = make_telemetry("d", 1, {"cpuUsage": 150.0})
    with pytest.raises(ProtocolError):
        validate_telemetry(packet)


def test_non_finite_number_is_rejected() -> None:
    packet = Packet(MessageType.TELEMETRY, "d", data={"cpuTemperature": float("inf")})
    with pytest.raises(ProtocolError):
        validate_telemetry(packet)


def test_boolean_is_not_accepted_as_a_number() -> None:
    packet = Packet(MessageType.TELEMETRY, "d", data={"cpuUsage": True})
    with pytest.raises(ProtocolError):
        validate_telemetry(packet)


def test_valid_telemetry_is_cleaned_and_kept() -> None:
    packet = make_telemetry(
        "d", 1, {"cpuTemperature": 62.0, "ramUsage": 44.0, "unknownKey": "ignored"}
    )
    cleaned = validate_telemetry(packet)
    assert cleaned == {"cpuTemperature": 62.0, "ramUsage": 44.0}


def test_battery_state_is_truncated_to_a_safe_length() -> None:
    packet = Packet(MessageType.TELEMETRY, "d", data={"batteryState": "c" * 500})
    cleaned = validate_telemetry(packet)
    assert len(cleaned["batteryState"]) == 32


def test_battery_state_must_be_a_string() -> None:
    packet = Packet(MessageType.TELEMETRY, "d", data={"batteryState": 42})
    with pytest.raises(ProtocolError):
        validate_telemetry(packet)


def test_status_cleaning_coerces_enum_like_strings() -> None:
    packet = Packet(
        MessageType.STATUS,
        "d",
        data={"fanRpm": 1450.0, "fanPwmPercent": 40.0, "controllerMode": "manual"},
    )
    cleaned = validate_status(packet)
    assert cleaned["fanRpm"] == pytest.approx(1450.0)
    assert cleaned["controllerMode"] == "manual"


def test_status_rejects_an_impossible_rpm() -> None:
    packet = Packet(MessageType.STATUS, "d", data={"fanRpm": 10_000_000.0})
    with pytest.raises(ProtocolError):
        validate_status(packet)


def test_hello_carries_the_current_protocol_version() -> None:
    from app import PROTOCOL_VERSION

    packet = make_hello("d", 1, firmware="1.2.3")
    assert packet.data["protocolVersion"] == PROTOCOL_VERSION
    assert packet.data["firmwareVersion"] == "1.2.3"


def test_decode_rejects_clock_skew_by_default() -> None:
    packet = make_command("d", 1, "ping")
    packet.timestamp = time.time() - 10_000  # ten thousand seconds in the past
    raw = encode_packet(packet)
    with pytest.raises(ProtocolError):
        decode_packet(raw, check_clock=True)


def test_decode_can_skip_the_clock_check() -> None:
    packet = make_command("d", 1, "ping")
    packet.timestamp = time.time() - 10_000
    raw = encode_packet(packet)
    decoded = decode_packet(raw, check_clock=False)
    assert decoded.data["command"] == "ping"
