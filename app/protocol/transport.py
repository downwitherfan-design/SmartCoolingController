"""Transport abstraction for the ESP32 link (spec §43, §207).

Reliable communication is available over two interchangeable transports:

* **WebSocket** (preferred) — framed, ordered, with a well understood
  handshake; a natural fit for an ESP32 running an AsyncWebSocket server.
* **TCP** — a length-prefixed frame stream for firmware that prefers raw
  sockets.

Both speak the same :mod:`app.protocol.messages` envelopes, so the connection
manager is transport-agnostic and the transport can be swapped later without
touching the protocol or the UI.

The transports are ``asyncio`` based; the connection manager runs them inside a
dedicated event-loop thread so the Qt UI thread is never blocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.core.errors import NetworkError

if TYPE_CHECKING:  # websockets ships these types but we keep the runtime import optional
    from websockets.asyncio.client import ClientConnection

try:  # websockets >= 13 exposes the asyncio client here
    from websockets.asyncio.client import connect as _ws_connect
except ImportError:  # pragma: no cover - legacy websockets layout
    from websockets import connect as _ws_connect  # type: ignore[assignment]

#: TCP frames are prefixed with a 4-byte big-endian length.
_FRAME_HEADER = struct.Struct(">I")
_MAX_FRAME = 1 << 20


class Transport(ABC):
    """A bidirectional, message oriented link to a peer."""

    @abstractmethod
    async def connect(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        """Open the link, raising :class:`NetworkError` on failure."""

    @abstractmethod
    async def send(self, payload: bytes) -> None:
        """Send one message."""

    @abstractmethod
    async def receive(self, *, timeout: float | None = None) -> bytes | None:
        """Receive one message, or ``None`` on timeout/closure."""

    @abstractmethod
    async def close(self) -> None:
        """Close the link."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether the link is currently usable."""


class WebSocketTransport(Transport):
    """WebSocket client transport."""

    def __init__(self) -> None:
        self._connection: ClientConnection | None = None

    @property
    def connected(self) -> bool:
        return self._connection is not None

    async def connect(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        url = f"ws://{host}:{port}/"
        try:
            self._connection = await asyncio.wait_for(
                _ws_connect(url, open_timeout=timeout, max_size=_MAX_FRAME), timeout=timeout
            )
        except Exception as exc:
            self._connection = None
            raise NetworkError(f"websocket connect to {url} failed: {exc}") from exc

    async def send(self, payload: bytes) -> None:
        if self._connection is None:
            raise NetworkError("websocket is not connected")
        try:
            await self._connection.send(payload)
        except Exception as exc:
            raise NetworkError(f"websocket send failed: {exc}") from exc

    async def receive(self, *, timeout: float | None = None) -> bytes | None:
        if self._connection is None:
            return None
        try:
            message = await asyncio.wait_for(self._connection.recv(), timeout)
        except TimeoutError:
            return None
        except Exception as exc:
            raise NetworkError(f"websocket receive failed: {exc}") from exc
        if isinstance(message, str):
            return message.encode("utf-8")
        return message

    async def close(self) -> None:
        if self._connection is not None:
            with contextlib.suppress(Exception):
                await self._connection.close()
            self._connection = None


class TcpTransport(Transport):
    """Length-prefixed TCP client transport."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
        except Exception as exc:
            self._reader = self._writer = None
            raise NetworkError(f"tcp connect to {host}:{port} failed: {exc}") from exc

    async def send(self, payload: bytes) -> None:
        if self._writer is None:
            raise NetworkError("tcp is not connected")
        if len(payload) > _MAX_FRAME:
            raise NetworkError("frame too large")
        try:
            self._writer.write(_FRAME_HEADER.pack(len(payload)) + payload)
            await self._writer.drain()
        except Exception as exc:
            raise NetworkError(f"tcp send failed: {exc}") from exc

    async def receive(self, *, timeout: float | None = None) -> bytes | None:
        if self._reader is None:
            return None
        try:
            header = await asyncio.wait_for(self._reader.readexactly(4), timeout)
            (length,) = _FRAME_HEADER.unpack(header)
            if length > _MAX_FRAME:
                raise NetworkError("declared frame exceeds maximum")
            return await asyncio.wait_for(self._reader.readexactly(length), timeout)
        except TimeoutError:
            return None
        except asyncio.IncompleteReadError:
            return None
        except NetworkError:
            raise
        except Exception as exc:
            raise NetworkError(f"tcp receive failed: {exc}") from exc

    async def close(self) -> None:
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None


def create_transport(use_websocket: bool) -> Transport:
    return WebSocketTransport() if use_websocket else TcpTransport()


__all__ = ["Transport", "WebSocketTransport", "TcpTransport", "create_transport"]
