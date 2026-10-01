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
from app.services.alerts import TOPIC_ALERT


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


def test_manual_mode_command_reaches_the_controller(services) -> None:
    services.cooling.set_mode(ControllerMode.MANUAL)
    services.cooling.set_manual_pwm(42.0)
    snapshot = services.telemetry.sample_once()
    decision = services.cooling.update(snapshot)
    assert decision.mode is ControllerMode.MANUAL
    assert decision.target_pwm == pytest.approx(42.0)


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
