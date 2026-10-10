"""Networking: the ESP32 connection manager and its event-loop bridge."""

from app.network.connection import (
    DEGRADED_AFTER_S,
    STALE_AFTER_S,
    TOPIC_DEVICE,
    TOPIC_STATE,
    TOPIC_STATUS,
    VALID_TRANSITIONS,
    ConnectionDiagnostics,
    ConnectionManager,
    can_transition,
)

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
