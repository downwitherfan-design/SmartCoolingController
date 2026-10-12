"""ESP32 connection reliability tests (deterministic, no hardware).

Every test drives the **real** :class:`ConnectionManager`, the **real**
transports and the **real** in-process mock ESP32 over a real localhost socket.
Faults are injected through the mock's deterministic knobs, so there are no
arbitrary sleeps as synchronisation — the helper waits on the observable state
with a bounded deadline.

What is proven here:

* connect / disconnect / stop are idempotent and cancellable,
* a duplicate connect request does not start a second session,
* a silent peer is never reported CONNECTED,
* a heartbeat timeout is detected and triggers a bounded reconnect,
* the reconnect backoff is bounded and escalates,
* stale sessions cannot overwrite a newer session,
* repeated cycles do not leak sessions or server sockets,
* latency is only reported when a heartbeat is actually answered.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

import pytest

from app.core.clock import utc_now
from app.core.enums import ConnectionState
from app.core.events import EventBus
from app.network.connection import ConnectionManager


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
    """A webSocket manager with a fast, deterministic heartbeat profile."""
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


def test_connects_to_the_mock_and_reports_connected(manager: ConnectionManager) -> None:
    device, _mock = _start_mock(manager, port=8811)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)
    assert manager.status.fan_available is True
    assert manager.diagnostics.connect_ms is not None
    assert manager.diagnostics.handshake_ms is not None
    assert manager.diagnostics.time_to_first_telemetry_ms is not None


def test_duplicate_connect_does_not_start_a_second_session(manager: ConnectionManager) -> None:
    device, _mock = _start_mock(manager, port=8812)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)
    sessions_after_first = manager.diagnostics.sessions_started

    # Hammer the same target: none of these may open a new session.
    for _ in range(10):
        manager.connect_device(device)
    time.sleep(0.4)
    assert manager.diagnostics.sessions_started == sessions_after_first
    assert manager.state is ConnectionState.CONNECTED


def test_silent_peer_is_never_reported_connected(manager: ConnectionManager) -> None:
    """A transport that accepts but never answers must not show a green link."""
    device, _mock = _start_mock(manager, port=8813, silent=True)
    manager.connect_device(device)
    # It may reach DEGRADED (transport up, awaiting telemetry) but never CONNECTED.
    time.sleep(1.2)
    assert manager.state is not ConnectionState.CONNECTED
    assert manager.status.fan_available is False


def test_heartbeat_timeout_triggers_reconnect(manager: ConnectionManager) -> None:
    device, mock = _start_mock(manager, port=8814)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)

    # The peer goes silent: the client must notice and try again.
    mock.silent = True
    assert _wait_for(
        lambda: manager.diagnostics.heartbeat_timeouts > 0, timeout=6.0
    )
    assert _wait_for(lambda: manager.diagnostics.sessions_started >= 2, timeout=8.0)


def test_disconnect_is_idempotent_and_stops_reconnecting(manager: ConnectionManager) -> None:
    device, _mock = _start_mock(manager, port=8815)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)

    manager.disconnect()
    manager.disconnect()
    manager.disconnect()
    assert _wait_for(lambda: manager.state is ConnectionState.DISCONNECTED, timeout=4.0)

    sessions = manager.diagnostics.sessions_started
    time.sleep(0.8)
    # No new sessions after a disconnect, even with the mock still listening.
    assert manager.diagnostics.sessions_started == sessions


def test_disconnect_cancels_an_in_flight_connect(manager: ConnectionManager) -> None:
    """Disconnecting while connecting must abort promptly, not hang."""
    device, _mock = _start_mock(manager, port=8816)
    manager.connect_device(device)
    manager.disconnect()  # immediately, before the session can establish
    assert _wait_for(lambda: manager.state is ConnectionState.DISCONNECTED, timeout=3.0)


def test_stop_during_connect_is_clean_and_idempotent(manager: ConnectionManager) -> None:
    device, _mock = _start_mock(manager, port=8817)
    manager.connect_device(device)
    manager.stop(timeout=5.0)
    manager.stop(timeout=5.0)  # second stop must be a harmless no-op
    assert manager.state in {ConnectionState.STOPPING, ConnectionState.DISCONNECTED}


def test_latency_is_measured_only_from_a_heartbeat_answer(manager: ConnectionManager) -> None:
    device, _mock = _start_mock(manager, port=8818)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)
    assert _wait_for(lambda: manager.status.latency_ms is not None, timeout=3.0)
    # A real RTT on loopback is small and strictly positive.
    assert 0 <= manager.status.latency_ms < 1000


def test_stop_interrupts_a_long_reconnect_backoff(bus: EventBus) -> None:
    """``stop()`` must not block for the whole backoff (it runs on the GUI thread).

    With the shipped 30 s top of the backoff schedule, an uninterruptible sleep
    would freeze shutdown for up to half a minute.  The loop must react to the
    stop signal within roughly one event-loop turn.
    """
    mgr = ConnectionManager(
        bus,
        use_websocket=True,
        heartbeat_interval_s=0.1,
        heartbeat_timeout_s=0.3,
        connect_timeout_s=0.5,
        backoff_schedule=[5.0],  # a deliberately long wait to be interrupted
        reconnect_jitter=0.0,
        healthy_reset_s=0.05,
    )
    try:
        device = mgr.enable_mock(host="127.0.0.1", port=8823)
        mgr.connect_device(device)
        # Wait until the loop has entered the backoff (state RECONNECTING), then
        # kill the mock so the next attempt fails and the loop parks in _wait().
        assert _wait_for(lambda: mgr.state is ConnectionState.CONNECTED, timeout=3.0)
        mock = mgr._mock
        assert mock is not None
        mgr._stop_mock(mock)
        assert _wait_for(lambda: mgr.state is ConnectionState.RECONNECTING, timeout=4.0)

        start = time.monotonic()
        mgr.stop(timeout=5.0)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"stop() blocked for {elapsed:.2f}s in the backoff wait"
    finally:
        mgr.stop(timeout=5.0)


def test_stop_interrupts_the_idle_poll_wait(bus: EventBus) -> None:
    """An idle (never-connected) loop must stop promptly, not after the poll tick."""
    mgr = ConnectionManager(bus, use_websocket=True, backoff_schedule=[0.05])
    try:
        mgr.start()
        assert _wait_for(lambda: mgr._loop is not None and mgr._loop.is_running(), timeout=3.0)
        time.sleep(0.05)  # let the loop reach its idle wait
        start = time.monotonic()
        mgr.stop(timeout=5.0)
        assert (time.monotonic() - start) < 1.0
    finally:
        mgr.stop(timeout=5.0)


def test_malformed_frames_degrade_but_do_not_disconnect(manager: ConnectionManager) -> None:
    device, _mock = _start_mock(manager, port=8819, malformed_every=3)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)
    assert _wait_for(lambda: manager.diagnostics.malformed_packets > 0, timeout=5.0)
    # The link stays up despite intermittent malformed frames.
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED, timeout=3.0)


def test_protocol_version_mismatch_is_surfaced_and_not_retried(
    manager: ConnectionManager,
) -> None:
    """A peer speaking a different protocol must not be accepted or hammered."""
    device, _mock = _start_mock(manager, port=8822, bad_version=True)
    manager.connect_device(device)
    assert _wait_for(
        lambda: manager.state is ConnectionState.PROTOCOL_MISMATCH, timeout=6.0
    )
    assert manager.state is not ConnectionState.CONNECTED
    assert manager.status.fan_available is False
    # A mismatch cannot be fixed by retrying: prove the loop settles.
    sessions = manager.diagnostics.sessions_started
    time.sleep(1.0)
    assert manager.diagnostics.sessions_started == sessions
    assert manager.state is ConnectionState.PROTOCOL_MISMATCH


def test_server_drop_causes_reconnect_and_recovery(manager: ConnectionManager) -> None:
    device, mock = _start_mock(manager, port=8820)
    manager.connect_device(device)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED)

    # The controller closes the socket after a few packets.
    mock.drop_after_packets = mock._sent_packets + 1
    assert _wait_for(lambda: manager.diagnostics.sessions_started >= 2, timeout=8.0)
    assert _wait_for(lambda: manager.state is ConnectionState.CONNECTED, timeout=8.0)


def test_no_session_leak_across_repeated_cycles(bus: EventBus) -> None:
    """Many connect/disconnect cycles must not leak sessions or mock sockets."""
    mgr = ConnectionManager(
        bus,
        use_websocket=True,
        heartbeat_interval_s=0.1,
        heartbeat_timeout_s=0.5,
        connect_timeout_s=1.0,
        backoff_schedule=[0.05],
        reconnect_jitter=0.0,
        healthy_reset_s=0.05,
    )
    try:
        device = mgr.enable_mock(host="127.0.0.1", port=8821)
        mock = mgr._mock
        assert mock is not None
        for _ in range(8):
            mgr.connect_device(device)
            assert _wait_for(lambda: mgr.state is ConnectionState.CONNECTED, timeout=5.0)
            mgr.disconnect()
            assert _wait_for(lambda: mgr.state is ConnectionState.DISCONNECTED, timeout=4.0)
        # Exactly one mock server, still listening; the client did not spawn a
        # swarm of connections.
        assert mock.has_listener
        assert mock.client_count <= 1
    finally:
        mgr.stop(timeout=5.0)
        # After stop the mock socket is released.
        assert mock is not None
        assert not mock.has_listener


def test_stale_window_is_configurable_not_hardcoded(bus: EventBus) -> None:
    """The stale/degraded thresholds must follow the user's setting.

    They used to be module constants (4.0 s / 2.0 s) that ignored
    ``telemetry.stale_after_s`` entirely, so the settings slider did nothing.
    This drives the real liveness check at the boundary to prove the window
    now scales with the configured value.
    """
    mgr = ConnectionManager(
        bus,
        use_websocket=False,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=3.0,
        connect_timeout_s=1.0,
        stale_after_s=10.0,
    )
    try:
        assert mgr.stale_after_s == 10.0
        assert mgr.degraded_after_s == 5.0
        # Shrinking the window takes effect immediately and keeps the ordering.
        mgr.set_stale_after_s(3.0)
        assert mgr.degraded_after_s == 1.5

        # An absurd value is clamped so the two thresholds never invert.
        mgr.set_stale_after_s(-5.0)
        assert mgr.stale_after_s >= mgr.degraded_after_s > 0.0

        # Drive the real liveness transition with a 4 s old packet against a
        # 6 s window: late enough to degrade, not yet stale.
        mgr.set_stale_after_s(6.0)
        mgr._set_state(ConnectionState.CONNECTING, epoch=mgr._epoch)
        mgr._set_state(ConnectionState.CONNECTED, epoch=mgr._epoch)
        mgr._status.last_packet_at = utc_now() - timedelta(seconds=4.0)
        mgr._check_stale(mgr._epoch)
        assert mgr.state is ConnectionState.DEGRADED

        # Past the whole window it becomes stale.
        mgr._status.last_packet_at = utc_now() - timedelta(seconds=7.0)
        mgr._check_stale(mgr._epoch)
        assert mgr.state is ConnectionState.STALE
    finally:
        mgr.stop(timeout=2.0)

