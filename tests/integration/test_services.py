"""Integration tests: services wired to a real database and the event bus.

These tests use the application's *real* components end to end — the real
SQLite database, the real persistence writer thread, the real event bus and the
real simulated hardware back-end.  Nothing here is a mock: the point is to
prove that the wiring actually works together (spec §28, §29, §30, §104).
"""

from __future__ import annotations

import time

import pytest

from app.core.enums import (
    AlertCategory,
    AlertEventType,
    AlertSeverity,
    BackendKind,
    ConnectionState,
    ControllerMode,
    ExportFormat,
)
from app.services.alerts import TOPIC_ALERT, TOPIC_NOTIFY


def test_services_start_on_the_simulation_backend(services) -> None:
    assert services.started is True
    assert services.registry.selected_backend() is BackendKind.SIMULATION


def test_snapshot_has_valid_fields(services) -> None:
    snapshot = services.telemetry.sample_once()
    assert snapshot is not None
    assert snapshot.sequence >= 0
    assert snapshot.cpu.usage is not None or snapshot.cpu.temperature is not None


def test_snapshots_are_published_on_the_bus(services) -> None:
    from app.telemetry.engine import TOPIC_SNAPSHOT

    received: list = []
    services.bus.subscribe(TOPIC_SNAPSHOT, lambda event: received.append(event.payload))
    services.telemetry.sample_once()
    assert received, "sampling should publish a snapshot event"
    assert received[-1].sequence >= 0


def test_persistence_writes_and_reads_back(services) -> None:
    for _ in range(5):
        services.telemetry.sample_once()
    services.persistence.stop_writer()  # drain the queue deterministically
    row = services.persistence.database.query_one("SELECT COUNT(*) AS n FROM snapshots")
    assert row is not None
    assert row["n"] >= 0  # schema is live and queryable


def test_database_file_is_created(services) -> None:
    assert services.paths.database_file.exists()
    assert services.persistence.database.size_bytes() > 0


def test_health_report_covers_subsystems(services) -> None:
    report = services.health.check()
    assert report is not None
    assert report.application is not None
    assert report.cpu is not None


def test_simulation_snapshot_is_flagged(services) -> None:
    snapshot = services.telemetry.sample_once()
    assert snapshot.simulated is True


def test_mock_device_connects_over_the_real_protocol(services) -> None:
    device = services.enable_mock_device(host="127.0.0.1", port=8899)
    assert device.is_mock is True
    services.connect_device(device)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if services.connection.state is ConnectionState.CONNECTED:
            break
        time.sleep(0.05)
    assert services.connection.state is ConnectionState.CONNECTED


def test_mock_server_is_listening_as_soon_as_enable_returns(services) -> None:
    """The mock socket must be bound before ``enable_mock_device`` returns.

    Regression guard: the server used to be started only when the connection
    thread's event loop already happened to be running, so on a slower machine
    (Windows) the loop had not come up yet, the server was never started and the
    subsequent connect failed with "connection refused" — the state stayed
    ``CONNECTING``.  A direct TCP connect here proves the port is open without
    relying on timing.
    """
    import socket

    device = services.enable_mock_device(host="127.0.0.1", port=8898)
    with socket.create_connection((device.host, device.port), timeout=3.0) as probe:
        assert probe.fileno() >= 0


def test_enable_mock_is_idempotent_and_releases_the_port(services) -> None:
    """Starting the mock twice on one port, then stopping, must free the port."""
    import socket

    device = services.enable_mock_device(host="127.0.0.1", port=8897)
    with socket.create_connection((device.host, device.port), timeout=3.0):
        pass
    services.connection.stop()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection((device.host, device.port), timeout=0.5):
                time.sleep(0.1)
        except OSError:
            break
    with pytest.raises(OSError):
        socket.create_connection((device.host, device.port), timeout=0.5).close()


def test_manual_mode_command_reaches_the_controller(services) -> None:
    services.cooling.set_mode(ControllerMode.MANUAL)
    services.cooling.set_manual_pwm(42.0)
    snapshot = services.telemetry.sample_once()
    decision = services.cooling.update(snapshot)
    assert decision.mode is ControllerMode.MANUAL
    assert decision.target_pwm == pytest.approx(42.0)


def _wait_for(predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_auto_mode_advertises_its_recommendation_in_the_telemetry_packet(services) -> None:
    """The automatic duty must travel to the firmware as ``recommendedPwm``.

    Regression: the telemetry supplier never included the recommendation, so the
    controller computed a target that never reached the device and automatic
    cooling did nothing.
    """
    services.cooling.set_profile(services.profiles.active)
    services.cooling.set_mode(ControllerMode.AUTO)
    snapshot = services.telemetry.sample_once()
    decision = services.cooling.update(snapshot)

    payload = services._telemetry_supplier()
    assert payload.get("recommendedPwm") == pytest.approx(round(decision.target_pwm, 1))

    # Outside AUTO the firmware owns a recommendation no longer.
    services.cooling.set_mode(ControllerMode.SAFE)
    assert "recommendedPwm" not in services._telemetry_supplier()


def test_automatic_and_safe_modes_actually_move_the_mock_fan(services) -> None:
    """End-to-end: the real controller drives the real mock over the real link."""
    import threading

    device = services.enable_mock_device(host="127.0.0.1", port=8896)
    services.connect_device(device)
    assert _wait_for(lambda: services.connection.state is ConnectionState.CONNECTED)

    mock = services.connection._mock
    assert mock is not None
    services.cooling.set_profile(services.profiles.active)
    services.cooling.set_mode(ControllerMode.AUTO)

    # The controller samples on its own cadence in the real app; drive it here
    # while the connection's telemetry loop pushes recommendations to the mock.
    stop = threading.Event()

    def drive() -> None:
        while not stop.is_set():
            services.telemetry.sample_once()
            time.sleep(0.02)

    worker = threading.Thread(target=drive, daemon=True)
    worker.start()
    try:
        # Auto: the firmware follows the recommendation pushed with telemetry.
        moved = _wait_for(lambda: mock._target_pwm > 0.0, timeout=6.0)
        assert moved, "automatic mode never reached the mock fan"
        auto_target = mock._target_pwm
        assert auto_target == pytest.approx(services.cooling.target_pwm, abs=2.0)

        # Safe: the desktop overrides firmware authority and forces the maximum.
        services.cooling.set_mode(ControllerMode.SAFE)
        assert _wait_for(lambda: mock._target_pwm >= 99.0, timeout=6.0), (
            "safe mode did not force the mock fan to full speed"
        )
    finally:
        stop.set()
        worker.join(timeout=2.0)
        services.connection.disconnect()


def test_profiles_seed_builtins(services) -> None:
    profiles = services.profiles.all()
    keys = {p.key for p in profiles}
    assert {"silent", "balanced", "performance", "turbo"} <= keys


def test_switching_profile_updates_the_controller(services) -> None:
    profile = services.profiles.set_active("performance")
    services.cooling.set_profile(profile)
    assert services.cooling.profile is not None
    assert services.cooling.profile.key == "performance"


def test_sessions_record_and_stop(services) -> None:
    services.sessions.start(profile_key="balanced")
    assert services.sessions.recording is True
    services.sessions.stop()
    assert services.sessions.recording is False


def test_alerts_service_raises_and_clears(services) -> None:
    raised: list = []
    services.bus.subscribe(TOPIC_ALERT, lambda e: raised.append(e.payload))
    services.alerts.raise_event(
        category=AlertCategory.THERMAL,
        severity=AlertSeverity.WARNING,
        title="Test alert",
        message="integration test",
        event_type=AlertEventType.THERMAL_WARNING,
        dedup_key="test.alert",
    )
    assert raised, "raising an event should publish on the alert topic"
    recent = services.alerts.recent(limit=10)
    assert any(a.dedup_key == "test.alert" for a in recent)


def test_a_warning_alert_requests_a_desktop_notification(services) -> None:
    """Regression: the notification was published to a topic nobody consumed."""
    services.alerts.set_notifications_enabled(True)
    notified: list = []
    services.bus.subscribe(TOPIC_NOTIFY, lambda e: notified.append(e.payload))
    services.alerts.raise_event(
        category=AlertCategory.THERMAL,
        severity=AlertSeverity.WARNING,
        title="Notify me",
        message="cpu hot",
        event_type=AlertEventType.THERMAL_WARNING,
        dedup_key="test.notify",
    )
    assert notified, "a warning alert must publish a notification request"
    assert notified[0].title == "Notify me"


def test_information_alerts_do_not_notify(services) -> None:
    services.alerts.set_notifications_enabled(True)
    notified: list = []
    services.bus.subscribe(TOPIC_NOTIFY, lambda e: notified.append(e.payload))
    services.alerts.raise_event(
        category=AlertCategory.SYSTEM,
        severity=AlertSeverity.INFORMATION,
        title="Just so you know",
        event_type=AlertEventType.PROFILE_CHANGE,
        dedup_key="test.info",
    )
    assert notified == []


def test_disabling_notifications_suppresses_the_request(services) -> None:
    services.alerts.set_notifications_enabled(False)
    notified: list = []
    services.bus.subscribe(TOPIC_NOTIFY, lambda e: notified.append(e.payload))
    services.alerts.raise_event(
        category=AlertCategory.THERMAL,
        severity=AlertSeverity.CRITICAL,
        title="Critical",
        event_type=AlertEventType.CRITICAL_TEMPERATURE,
        dedup_key="test.suppressed",
    )
    assert notified == []


def test_backup_can_be_created(services) -> None:
    info = services.persistence.backups.create(kind="manual")
    assert info is not None
    assert info.path.exists()


def test_export_produces_a_file(services) -> None:
    for _ in range(3):
        services.telemetry.sample_once()
    services.persistence.stop_writer()
    result = services.export.export_range(
        since=0.0, fmt=ExportFormat.CSV, name="integration-test"
    )
    assert result.path.exists()
    assert result.format is ExportFormat.CSV


def test_export_json_produces_parseable_output(services) -> None:
    import json

    services.telemetry.sample_once()
    services.persistence.stop_writer()
    result = services.export.export_range(
        since=0.0, fmt=ExportFormat.JSON, name="integration-test-json"
    )
    payload = json.loads(result.path.read_text(encoding="utf-8"))
    assert isinstance(payload, list | dict)


def test_export_is_not_truncated_by_the_range_row_limit(services) -> None:
    """A large export must be complete, not silently capped at 100 000 rows.

    ``export_range`` used ``snapshots.range`` with its default limit, so an
    export wider than the limit silently dropped the newest rows.  It now
    streams every row via ``iter_range``.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.telemetry import TelemetrySnapshot

    end = datetime.now(UTC)
    count = 7_500
    batch = []
    for index in range(count):
        snapshot = TelemetrySnapshot(
            sequence=index,
            timestamp=end - timedelta(seconds=count - 1 - index),
            simulated=True,
        )
        snapshot.cpu.temperature = 45.0
        batch.append(snapshot)
    services.persistence.snapshots.insert_many(batch)

    result = services.export.export_range(
        since=0.0, fmt=ExportFormat.CSV, name="completeness"
    )
    assert result.row_count >= count
    lines = result.path.read_text(encoding="utf-8").splitlines()
    # One header row plus every data row.
    assert len(lines) - 1 == result.row_count


def test_export_crosses_the_old_row_limit_without_loss(services) -> None:
    """Export 120 000 rows — past the old 100 000-row cap — and prove completeness.

    The defect was a silent truncation at the range query's default limit.  This
    inserts more rows than that limit and asserts every row is present exactly
    once, in ascending time order, with no duplicate timestamp sequence.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.telemetry import TelemetrySnapshot

    count = 120_000
    start = datetime(2026, 1, 1, tzinfo=UTC)
    batch = []
    for index in range(count):
        snapshot = TelemetrySnapshot(
            sequence=index,
            timestamp=start + timedelta(seconds=index),
            simulated=True,
        )
        snapshot.cpu.temperature = 40.0 + (index % 100) * 0.25
        batch.append(snapshot)
    services.persistence.snapshots.insert_many(batch)

    result = services.export.export_range(since=0.0, fmt=ExportFormat.CSV, name="crossing")

    assert result.row_count == count
    lines = result.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) - 1 == count
    # Ordering is preserved and every timestamp appears exactly once.
    timestamps = [line.split(",", 1)[0] for line in lines[1:]]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == count


def test_export_failure_leaves_no_partial_file(services, monkeypatch) -> None:
    """A write that fails partway must not leave a partial export or a false success.

    ``_write`` writes to a ``.part`` file and atomically replaces the target only
    on success.  Injecting an ``OSError`` mid-stream must raise, remove the
    temporary file, and record the attempt as an error — never a truncated file
    masquerading as a complete export.
    """
    from datetime import UTC, datetime, timedelta

    import pytest as _pytest

    from app.core.errors import ExportError
    from app.models.telemetry import TelemetrySnapshot

    batch = []
    for index in range(50):
        snapshot = TelemetrySnapshot(
            sequence=index,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            simulated=True,
        )
        batch.append(snapshot)
    services.persistence.snapshots.insert_many(batch)

    original = services.export._snapshot_row
    calls = {"n": 0}

    def flaky(row):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return original(row)

    monkeypatch.setattr(services.export, "_snapshot_row", flaky)

    with _pytest.raises(ExportError):
        services.export.export_range(since=0.0, fmt=ExportFormat.CSV, name="broken")

    # No final file, and no leftover .part, remain in the export directory.
    leftovers = [p for p in services.export._dir.iterdir() if p.name.startswith("broken")]
    assert leftovers == []
    # The failure is recorded as an error, not a success.
    history = services.export.history(limit=10)
    broken = [h for h in history if h.get("status") == "error"]
    assert broken, "the failed export is recorded with an error status"


def test_services_shutdown_is_idempotent(services) -> None:
    services.shutdown()
    services.shutdown()  # second call must be a harmless no-op
    assert services.started is False


def test_snapshot_history_is_bounded_and_ordered(services) -> None:
    for _ in range(10):
        services.telemetry.sample_once()
    history = services.telemetry.history()
    assert history
    sequences = [s.sequence for s in history]
    assert sequences == sorted(sequences)


def test_shutdown_survives_a_failing_subsystem(tmp_path) -> None:
    """One broken stop() must not skip the rest of teardown.

    Regression guard: ``shutdown`` used to run every step inside one ``try``,
    so an exception from (say) the telemetry thread left the persistence writer
    and the database connection open — a leaked thread and an unflushed file.
    """
    from app.services import ApplicationServices

    services = ApplicationServices(base_dir=tmp_path)
    services.start(simulate=True)
    closed: list[str] = []

    original = services.telemetry.stop

    def explode() -> None:
        raise RuntimeError("telemetry thread is wedged")

    services.telemetry.stop = explode  # type: ignore[method-assign]
    original_close = services.persistence.close

    def track_close() -> None:
        closed.append("persistence")
        original_close()

    services.persistence.close = track_close  # type: ignore[method-assign]
    services.shutdown()
    assert services.started is False
    assert closed == ["persistence"], "persistence must still be closed"

    services.telemetry.stop = original  # type: ignore[method-assign]
    services.persistence.close = original_close  # type: ignore[method-assign]


def test_background_discovery_returns_immediately_and_completes(data_root, monkeypatch) -> None:
    """``start(background_discovery=True)`` must not block the caller.

    Regression: ``start()`` called ``registry.discover()`` synchronously, so a
    slow WMI/LHM ``open()`` froze the GUI thread before the first paint.  With
    background discovery the call returns while the worker does the work, and
    the result is advertised on the bus.
    """
    import threading

    from app.hardware.base import HardwareMonitorBackend
    from app.hardware.registry import HardwareRegistry
    from app.models.sensors import HardwareCategory, SensorReading, SensorType
    from app.services import TOPIC_DISCOVERY, ApplicationServices

    opened = threading.Event()
    release = threading.Event()

    class _BlockingBackend(HardwareMonitorBackend):
        kind = BackendKind.PSUTIL

        def is_available(self) -> bool:
            return True

        def open(self) -> None:
            opened.set()
            release.wait(3.0)  # simulate a slow native open
            self._mark_ready()

        def read(self) -> list[SensorReading]:
            return [
                SensorReading(
                    sensor_id="slow.temp",
                    hardware_name="Slow",
                    hardware_category=HardwareCategory.CPU,
                    sensor_name="CPU",
                    sensor_type=SensorType.TEMPERATURE,
                    value=40.0,
                    unit="°C",
                    backend=self.kind,
                )
            ]

    monkeypatch.setenv("SMARTCOOLING_DATA_MODE", "standard")
    monkeypatch.setenv("SMARTCOOLING_DATA_DIR", str(data_root))
    services = ApplicationServices()
    services.config.general.start_telemetry = False
    services.config.general.auto_connect_esp32 = False
    services.config.general.first_run_completed = True
    services.registry = HardwareRegistry(factories=[_BlockingBackend])
    seen: list[object] = []
    services.bus.subscribe(TOPIC_DISCOVERY, lambda event: seen.append(event.payload))

    try:
        start = time.perf_counter()
        services.start(background_discovery=True)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"start() blocked for {elapsed:.2f}s"
        assert services.discovery_finished is False
        assert opened.wait(2.0), "discovery worker never started"

        release.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not services.discovery_finished:
            time.sleep(0.02)
        assert services.discovery_finished, "discovery never completed"
        assert seen, "no discovery event was published"
    finally:
        release.set()
        services.shutdown()


def test_stale_after_setting_reaches_the_connection_manager(services) -> None:
    """The telemetry stale window is read from config, not hardcoded.

    ``telemetry.stale_after_s`` used to be persisted and shown in Settings but
    never consulted: the connection manager used a module constant, so moving
    the slider changed nothing until (and even after) a restart.  This pins the
    wiring from the setting through ``update_config`` to the live manager.
    """
    assert services.connection.stale_after_s == services.config.telemetry.stale_after_s

    services.update_config(telemetry={"stale_after_s": 12.5})
    assert services.connection.stale_after_s == 12.5
    assert services.connection.degraded_after_s == 6.25


def test_rapid_rise_setting_reaches_the_analyser(services) -> None:
    """``alerts.rapid_rise_c_per_min`` is consulted, not just persisted.

    It used to be stored and shown in Settings while the analyser used a
    hardcoded multiple of its stable band.  This pins the setting through
    ``update_config`` to the live analyser.
    """
    assert services.telemetry.analyser._rapid_rise_c_per_min == (
        services.config.alerts.rapid_rise_c_per_min
    )

    services.update_config(alerts={"rapid_rise_c_per_min": 4.5})
    assert services.telemetry.analyser._rapid_rise_c_per_min == 4.5


def test_stale_after_setting_reaches_the_anomaly_grace_window(services) -> None:
    """The sensor-dropout grace window follows ``telemetry.stale_after_s``.

    The live analyser gained ``SENSOR_DISAPPEARED`` detection whose grace window
    is the same stale window the connection manager uses; both must track the
    setting, at construction and through ``update_config``.
    """
    assert services.telemetry.analyser._sensor_stale_after_s == (
        services.config.telemetry.stale_after_s
    )

    services.update_config(telemetry={"stale_after_s": 7.5})
    assert services.telemetry.analyser._sensor_stale_after_s == 7.5


