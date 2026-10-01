"""ESP32 discovery over UDP broadcast (spec §43, §49).

The desktop broadcasts a ``DISCOVER`` datagram to the LAN broadcast address and
collects ``DISCOVER_RESPONSE`` replies for a short window.  Manual IP entry is
always available as a fallback, and mDNS is noted as a future transport.

This module only *finds* devices; establishing a reliable session is the
connection manager's job.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from dataclasses import dataclass, field

from app import PROTOCOL_VERSION
from app.core.clock import utc_timestamp
from app.core.enums import MessageType
from app.core.errors import NetworkError
from app.core.logging_setup import get_logger
from app.protocol.messages import Packet, decode_packet, encode_packet

_log = get_logger(__name__)

DEFAULT_DISCOVERY_PORT = 8766
DISCOVERY_MAGIC = "SMARTCOOLING-DISCOVER"


@dataclass(slots=True)
class DiscoveredDevice:
    device_id: str
    host: str
    port: int
    nickname: str = ""
    firmware_version: str = ""
    protocol_version: int = PROTOCOL_VERSION
    discovered_at: float = field(default_factory=utc_timestamp)
    is_mock: bool = False

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


class UdpDiscovery:
    """Broadcasts discovery requests and gathers responses."""

    def __init__(
        self,
        *,
        port: int = DEFAULT_DISCOVERY_PORT,
        client_id: str = "windows-telemetry",
    ) -> None:
        self.port = port
        self.client_id = client_id

    def discover(self, *, timeout_s: float = 2.0) -> list[DiscoveredDevice]:
        """Return devices that answered within ``timeout_s``."""
        found: dict[str, DiscoveredDevice] = {}
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.25)
            sock.bind(("", 0))
        except OSError as exc:
            raise NetworkError(f"could not open discovery socket: {exc}") from exc

        request = Packet(
            MessageType.DISCOVER,
            self.client_id,
            utc_timestamp(),
            0,
            data={"magic": DISCOVERY_MAGIC},
        )
        payload = encode_packet(request)
        try:
            for target in self._broadcast_targets():
                with contextlib.suppress(OSError):
                    sock.sendto(payload, (target, self.port))
        except OSError as exc:
            sock.close()
            raise NetworkError(f"discovery broadcast failed: {exc}") from exc

        deadline = utc_timestamp() + timeout_s
        while utc_timestamp() < deadline:
            try:
                raw, address = sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                packet = decode_packet(raw, check_clock=False)
            except Exception:
                continue
            if packet.message_type is not MessageType.DISCOVER_RESPONSE:
                continue
            device_id = packet.device_id or address[0]
            found[device_id] = DiscoveredDevice(
                device_id=device_id,
                host=address[0],
                port=int(packet.data.get("port", 8765)),
                nickname=str(packet.data.get("nickname", "")),
                firmware_version=str(packet.data.get("firmwareVersion", "")),
                protocol_version=int(packet.data.get("protocolVersion", PROTOCOL_VERSION)),
            )
        sock.close()
        _log.info("UDP discovery found %d device(s)", len(found))
        return list(found.values())

    def discover_async(self, callback, *, timeout_s: float = 2.0) -> threading.Thread:
        """Run discovery on a worker thread and invoke ``callback`` with results."""
        def _worker() -> None:
            try:
                callback(self.discover(timeout_s=timeout_s))
            except Exception as exc:
                _log.warning("async discovery failed: %s", exc)
                callback([])

        thread = threading.Thread(target=_worker, name="UdpDiscovery", daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _broadcast_targets() -> list[str]:
        targets = ["255.255.255.255"]
        with contextlib.suppress(OSError):
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                address = info[4][0]
                if address and not address.startswith("127."):
                    targets.append(address)
        return targets


def manual_device(host: str, port: int = 8765, device_id: str = "") -> DiscoveredDevice:
    """Create a device descriptor for a manually entered address."""
    return DiscoveredDevice(
        device_id=device_id or f"manual-{host}",
        host=host,
        port=port,
    )


__all__ = ["UdpDiscovery", "DiscoveredDevice", "manual_device", "DEFAULT_DISCOVERY_PORT"]
