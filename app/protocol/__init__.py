"""ESP32 protocol: envelopes, validation, discovery, transports and the mock."""

from app.protocol.discovery import (
    DEFAULT_DISCOVERY_PORT,
    DiscoveredDevice,
    UdpDiscovery,
    manual_device,
)
from app.protocol.messages import (
    MAX_PACKET_BYTES,
    Packet,
    decode_packet,
    encode_packet,
    make_command,
    make_heartbeat,
    make_hello,
    make_telemetry,
    validate_status,
    validate_telemetry,
)
from app.protocol.mock_esp32 import MOCK_DEVICE_PREFIX, MockEsp32
from app.protocol.transport import (
    TcpTransport,
    Transport,
    WebSocketTransport,
    create_transport,
)

__all__ = [
    "Packet",
    "encode_packet",
    "decode_packet",
    "validate_telemetry",
    "validate_status",
    "make_telemetry",
    "make_command",
    "make_heartbeat",
    "make_hello",
    "MAX_PACKET_BYTES",
    "UdpDiscovery",
    "DiscoveredDevice",
    "manual_device",
    "DEFAULT_DISCOVERY_PORT",
    "Transport",
    "WebSocketTransport",
    "TcpTransport",
    "create_transport",
    "MockEsp32",
    "MOCK_DEVICE_PREFIX",
]
