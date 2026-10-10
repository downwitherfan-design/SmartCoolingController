"""ESP32 device and status models (spec §46, §123)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.clock import to_iso
from app.core.enums import ConnectionState, ControllerMode, FaultState, HealthStatus


@dataclass(slots=True)
class Esp32Device:
    """A remembered ESP32 controller."""

    device_id: str
    nickname: str = ""
    host: str = ""
    port: int = 8765
    firmware_version: str = ""
    protocol_version: int = 0
    last_seen: datetime | None = None
    trusted: bool = False
    #: True only for the in-process mock controller (spec §53, §296).
    is_mock: bool = False

    @property
    def display_name(self) -> str:
        base = self.nickname or self.device_id or "ESP32"
        return f"{base} (Mock)" if self.is_mock else base

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "nickname": self.nickname,
            "host": self.host,
            "port": self.port,
            "firmware_version": self.firmware_version,
            "protocol_version": self.protocol_version,
            "last_seen": to_iso(self.last_seen) if self.last_seen else None,
            "trusted": self.trusted,
            "is_mock": self.is_mock,
        }


@dataclass(slots=True)
class Esp32Status:
    """Live controller status as reported over the wire."""

    device_id: str = ""
    connection: ConnectionState = ConnectionState.DISCONNECTED
    health: HealthStatus = HealthStatus.UNKNOWN
    mode: ControllerMode = ControllerMode.AUTO
    fault: FaultState = FaultState.NONE
    fan_target_percent: float | None = None
    fan_pwm_percent: float | None = None
    fan_rpm: float | None = None
    fan_voltage_v: float | None = None
    fan_current_a: float | None = None
    fan_power_w: float | None = None
    uptime_s: float | None = None
    firmware_version: str = ""
    protocol_version: int = 0
    wifi_rssi_dbm: float | None = None
    latency_ms: float | None = None
    packet_loss_percent: float | None = None
    last_packet_at: datetime | None = None
    is_mock: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def fan_available(self) -> bool:
        # Available only once the controller has actually delivered a packet;
        # a transport that is up but silent must not publish fan metrics.
        return (
            self.connection in {ConnectionState.CONNECTED, ConnectionState.DEGRADED}
            and self.last_packet_at is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "connection": self.connection.value,
            "health": self.health.value,
            "mode": self.mode.value,
            "fault": self.fault.value,
            "fan_target_percent": self.fan_target_percent,
            "fan_pwm_percent": self.fan_pwm_percent,
            "fan_rpm": self.fan_rpm,
            "fan_voltage_v": self.fan_voltage_v,
            "fan_current_a": self.fan_current_a,
            "fan_power_w": self.fan_power_w,
            "uptime_s": self.uptime_s,
            "firmware_version": self.firmware_version,
            "protocol_version": self.protocol_version,
            "wifi_rssi_dbm": self.wifi_rssi_dbm,
            "latency_ms": self.latency_ms,
            "packet_loss_percent": self.packet_loss_percent,
            "last_packet_at": to_iso(self.last_packet_at) if self.last_packet_at else None,
            "is_mock": self.is_mock,
            "extra": dict(self.extra),
        }


__all__ = ["Esp32Device", "Esp32Status"]
