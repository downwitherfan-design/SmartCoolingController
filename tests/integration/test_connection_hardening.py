"""V2.1 connection-reliability hardening tests.

These complement ``test_connection.py`` with the fault scenarios the V2.1 audit
identified.  Every test drives the **real** :class:`ConnectionManager`, the
**real** transports and the **real** in-process mock ESP32 over a real loopback
socket.  Faults come from the mock's deterministic knobs, never from
monkeypatching the manager (a test that asserts the manager's own assumption is
worthless).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from app.core.enums import ConnectionState, MessageType
from app.core.events import EventBus
from app.network.connection import ConnectionManager
from app.protocol.messages import Packet


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(bus: EventBus):
    mgr = ConnectionManager(
        bus,
        use_websocket=True,
        heartbeat_interval_s=0.15,
        heartbeat_timeout_s=0.4,
        connect_timeout_s=1.0,
        backoff_schedule=[0.05, 0.1, 0.2],
        reconnect_jitter=0.0,
        healthy_reset_s=0.05,
    )
    try:
        yield mgr
    finally:
        mgr.stop(timeout=5.0)


def _start_mock(manager: ConnectionManager, *, port: int, **knobs):
    device = manager.enable_mock(host="127.0.0.1", port=port)
    mock = manager._mock
    assert mock is not None
    for key, value in knobs.items():
        setattr(mock, key, value)
    return device, mock


def test_error_packet_is_not_overridden_by_a_connected_promotion(
    manager: ConnectionManager,
) -> None:
    """An inbound ERROR must be surfaced, not immediately relabelled CONNECTED.

    Regression: ``_on_packet`` used to end with an unconditional promotion to
    CONNECTED, which overwrote the DEGRADED set by the ERROR branch in the same
    call.  A controller reporting a fault was therefore shown as a healthy link.
    """
    device, _mock = _start_mock(manager, port=8841)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)

    error = Packet(
        MessageType.ERROR,
        device.device_id,
        time.time(),
        sequence=10_000,
        data={"message": "overcurrent"},
    )
    manager._on_packet(error.to_bytes(), manager._epoch)

    assert manager.state is ConnectionState.DEGRADED
    assert "overcurrent" in manager._last_detail


def test_oversized_frame_is_rejected_and_link_stays_up(
    manager: ConnectionManager,
) -> None:
    """A peer sending oversized frames must not look healthy from those frames.

    The client must reject the frame on its own size guard, count it, keep the
    link up (it is a malformed packet, not a disconnect), and recover as soon as
    well-formed frames resume.
    """
    device, _mock = _start_mock(manager, port=8842, oversized_every=2)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)
    assert _wait_for(lambda: manager.diagnostics.malformed_packets > 0, timeout=6.0)
    # It is not a disconnect: the session survives and reconnects to CONNECTED.
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED, timeout=6.0)
    assert manager.diagnostics.sessions_started == 1


def test_connect_to_a_new_device_replaces_the_active_session(
    manager: ConnectionManager,
) -> None:
    """Connecting to a different endpoint must switch, not cling to the old one."""
    first, _ = _start_mock(manager, port=8843)
    manager.connect_device(first)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)

    second = manager.enable_mock(host="127.0.0.1", port=8844)
    try:
        manager.connect_device(second)
        assert _wait_for(
            lambda: manager.device is not None and manager.device.port == 8844,
            timeout=8.0,
        )
        assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED, timeout=8.0)
    finally:
        manager.stop(timeout=5.0)


def _thread_count() -> int:
    import threading

    return threading.active_count()


def test_repeated_faults_do_not_leak_threads_or_sessions(bus: EventBus) -> None:
    """Many connect/disconnect cycles across a flapping peer stay bounded.

    A stress guard for the reliability path: the manager must not spawn a
    thread or a session per fault, and each cycle must still settle to
    DISCONNECTED.  This is the "more commands, transitions and long-duration
    operation" requirement without a real multi-hour soak.
    """
    before = _thread_count()
    mgr = ConnectionManager(
        bus,
        use_websocket=True,
        heartbeat_interval_s=0.1,
        heartbeat_timeout_s=0.35,
        connect_timeout_s=1.0,
        backoff_schedule=[0.02, 0.05],
        reconnect_jitter=0.0,
        healthy_reset_s=0.05,
    )
    try:
        device = mgr.enable_mock(host="127.0.0.1", port=8845)
        mock = mgr._mock
        assert mock is not None
        mock.malformed_every = 4
        for cycle in range(12):
            mgr.connect_device(device)
            assert _wait_for(lambda: mgr.state is ConnectionState.CONNECTED, timeout=6.0)
            # Fault: half the cycles flap the peer before we disconnect.
            if cycle % 2 == 0:
                mock.silent = True
                assert _wait_for(
                    lambda: mgr.diagnostics.heartbeat_timeouts > 0, timeout=6.0
                )
                mock.silent = False
            mgr.disconnect()
            assert _wait_for(lambda: mgr.state is ConnectionState.DISCONNECTED, timeout=5.0)
        assert mock.has_listener
        assert mock.client_count <= 1
    finally:
        mgr.stop(timeout=5.0)
    # The connection thread and the mock's server tasks must be released,
    # allowing for thread-scheduler latency in CI.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _thread_count() > before:
        time.sleep(0.05)
    assert _thread_count() <= before + 1
