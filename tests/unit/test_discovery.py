"""Unit tests for endpoint validation (manual connect)."""

from __future__ import annotations

import pytest

from app.protocol.discovery import manual_device, validate_endpoint


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("192.168.1.50", 8765),
        ("10.0.0.1", 1),
        ("127.0.0.1", 65535),
        ("esp32.local", 8765),
        ("cooling-pad", 8765),
        ("::1", 8765),
        ("fe80::1", 8765),
    ],
)
def test_valid_endpoints_are_accepted(host: str, port: int) -> None:
    ok, reason = validate_endpoint(host, port)
    assert ok, reason
    assert reason == ""


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("", 8765),
        ("   ", 8765),
        ("ws://192.168.1.5", 8765),
        ("192.168.1.5/status", 8765),
        ("bad host", 8765),
        ("192.168.1.5", 0),
        ("192.168.1.5", 70000),
        ("-leading.example", 8765),
        ("trailing-.example", 8765),
        ("bad_char!.example", 8765),
        ("a" * 64 + ".example", 8765),
    ],
)
def test_invalid_endpoints_are_rejected_with_a_reason(host: str, port: int) -> None:
    ok, reason = validate_endpoint(host, port)
    assert not ok
    assert reason


def test_manual_device_defaults() -> None:
    device = manual_device("192.168.1.5")
    assert device.host == "192.168.1.5"
    assert device.port == 8765
    assert device.device_id == "manual-192.168.1.5"

    named = manual_device("10.0.0.9", port=9000, device_id="pad-01")
    assert named.port == 9000
    assert named.device_id == "pad-01"
