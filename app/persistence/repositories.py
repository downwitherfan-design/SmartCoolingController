"""Repositories: typed access to each table (spec §55–§58, §108–§110).

Every repository takes a :class:`~app.persistence.database.Database` and maps
between rows and the application's typed models.  Keeping the mapping here —
rather than sprinkling SQL through the services — means the schema can evolve
behind a stable Python API.

Repositories never assume a value is present: readings are frequently
unavailable on a given machine, so every numeric column is nullable and mapped
to ``None``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from typing import Any, TypedDict

from app.core.enums import (
    AlertCategory,
    AlertEventType,
    AlertSeverity,
    CoolingProfileKind,
    DataProvenance,
    FanControlSource,
    SessionState,
)
from app.core.logging_setup import get_logger
from app.models.alerts import AlertEvent
from app.models.cooling import CoolingProfile
from app.models.devices import Esp32Device
from app.models.sessions import Baseline, CoolingSession
from app.models.telemetry import TelemetrySnapshot
from app.persistence.database import Database

_log = get_logger(__name__)


def _to_epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _from_epoch(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


def _epoch_required(value: Any) -> datetime:
    """Like :func:`_from_epoch` but for a ``NOT NULL`` timestamp column.

    ``started_at`` / ``created_at`` / ``timestamp`` are non-null in the schema,
    so a NULL here means a corrupted or hand-edited database row.  Returning
    ``None`` would put an invalid value into a model whose field is a plain
    ``datetime`` and surface much later; raising keeps the failure local.
    """
    moment = _from_epoch(value)
    if moment is None:
        raise ValueError("expected a non-null timestamp column")
    return moment


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _b(value: Any) -> bool:
    return bool(value)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def set(self, key: str, value: object) -> None:
        self._db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), _to_epoch(datetime.now(UTC))),
        )

    def get(self, key: str, default: object = None) -> object:
        row = self._db.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def all(self) -> dict[str, object]:
        return {
            row["key"]: json.loads(row["value"])
            for row in self._db.query("SELECT key, value FROM settings")
        }

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM settings WHERE key=?", (key,))


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


class SeriesPoint(TypedDict):
    """One averaged chart point, as returned by :meth:`SnapshotRepository.series_bucketed`."""

    ts: float
    cpu_temp: float | None
    gpu_temp: float | None
    fan_pwm: float | None


class SnapshotRepository:
    _COLUMNS = (
        "ts", "seq", "cpu_temp", "gpu_temp", "cpu_usage", "gpu_usage", "ram_usage",
        "cpu_clock_mhz", "fan_rpm", "fan_pwm", "fan_target", "fan_power_w",
        "pad_temp", "thermal_state", "validity", "simulated",
    )

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_many(self, snapshots: Iterable[TelemetrySnapshot]) -> int:
        rows = [self._row(snapshot) for snapshot in snapshots]
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in self._COLUMNS)
        columns = ", ".join(self._COLUMNS)
        with self._db.transaction() as connection:
            connection.executemany(
                f"INSERT INTO snapshots({columns}) VALUES({placeholders})", rows
            )
        return len(rows)

    @staticmethod
    def _row(snapshot: TelemetrySnapshot) -> tuple:
        return (
            _to_epoch(snapshot.timestamp),
            snapshot.sequence,
            snapshot.cpu.temperature,
            snapshot.gpu.temperature,
            snapshot.cpu.usage,
            snapshot.gpu.usage,
            snapshot.memory.usage_percent,
            snapshot.cpu.clock_mhz,
            snapshot.fan.rpm,
            snapshot.fan.pwm_percent,
            snapshot.fan.target_percent,
            snapshot.fan.power_w,
            snapshot.pad.temperature,
            snapshot.thermal.cpu_state.value if snapshot.thermal else None,
            snapshot.validity.value,
            1 if snapshot.simulated else 0,
        )

    def range(self, since: float, until: float | None = None, limit: int = 100_000) -> list[sqlite3.Row]:
        if until is None:
            return self._db.query(
                "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (since, limit),
            )
        return self._db.query(
            "SELECT * FROM snapshots WHERE ts BETWEEN ? AND ? ORDER BY ts ASC LIMIT ?",
            (since, until, limit),
        )

    def iter_range(
        self, since: float, until: float | None = None, *, page: int = 5000
    ) -> Iterator[sqlite3.Row]:
        """Yield every snapshot in ``[since, until]`` in ascending time order.

        Unlike :meth:`range`, this never silently truncates: it pages through the
        whole range with keyset pagination (``ts > last``), so a multi-day export
        is complete while memory stays bounded by one page.  ``ts`` alone is the
        cursor; rows that share a timestamp with a page boundary are the only
        ambiguity, which is harmless for a telemetry export.
        """
        size = max(1, int(page))
        cursor = since
        while True:
            if until is None:
                rows = self._db.query(
                    "SELECT * FROM snapshots WHERE ts > ? ORDER BY ts ASC LIMIT ?",
                    (cursor, size),
                )
            else:
                rows = self._db.query(
                    "SELECT * FROM snapshots WHERE ts > ? AND ts <= ? ORDER BY ts ASC LIMIT ?",
                    (cursor, until, size),
                )
            if not rows:
                return
            yield from rows
            if len(rows) < size:
                return
            cursor = float(rows[-1]["ts"])

    def series_bucketed(
        self, since: float, until: float, bucket_s: int
    ) -> list[SeriesPoint]:
        """Averaged chart series over ``[since, until]``, one point per bucket.

        The whole range is aggregated *inside SQLite*, so a wide range never has
        to materialise every raw row: the query scans the ``ts`` index once but
        returns only ``range / bucket_s`` points.  Averaging matches the page's
        own decimation, so the drawn curve is identical to bucketing the full
        rows — without a row cap that would otherwise silently truncate the
        range and drop the newest data.
        """
        bucket = max(1, int(bucket_s))
        rows = self._db.query(
            """
            SELECT CAST(ts / ? AS INTEGER) AS bucket,
                   AVG(cpu_temp) AS cpu_temp,
                   AVG(gpu_temp) AS gpu_temp,
                   AVG(fan_pwm)  AS fan_pwm
            FROM snapshots
            WHERE ts BETWEEN ? AND ?
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            (bucket, since, until),
        )
        return [
            {
                "ts": float(row["bucket"] * bucket),
                "cpu_temp": row["cpu_temp"],
                "gpu_temp": row["gpu_temp"],
                "fan_pwm": row["fan_pwm"],
            }
            for row in rows
        ]

    def aggregate(
        self, since: float, until: float, *, threshold_c: float, bucket_s: int
    ) -> dict[str, float | None]:
        """Whole-range summary over ``[since, until]``.

        Returns the row count, per-metric min/max/average and the seconds the CPU
        spent at or above ``threshold_c`` — all computed over the *entire* range,
        never a truncated sample.

        The time-above figure is ``count(above) x interval``, where ``interval``
        is the observed mean spacing between samples.  The engine samples on a
        fixed cadence, so this is exact for the normal case and, unlike a window
        function that materialises every row, it is a single index scan that
        stays cheap on a multi-day database.  ``interval`` is capped at the
        display bucket so a sampling gap can never over-count the time.
        """
        bucket = max(1, int(bucket_s))
        summary = self._db.query_one(
            """
            SELECT COUNT(*) AS samples,
                   MIN(cpu_temp) AS cpu_min, MAX(cpu_temp) AS cpu_max, AVG(cpu_temp) AS cpu_avg,
                   MIN(gpu_temp) AS gpu_min, MAX(gpu_temp) AS gpu_max, AVG(gpu_temp) AS gpu_avg,
                   AVG(fan_rpm)  AS fan_avg, MAX(fan_rpm)  AS peak_fan,
                   MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                   SUM(CASE WHEN cpu_temp >= ? THEN 1 ELSE 0 END) AS above_n
            FROM snapshots
            WHERE ts BETWEEN ? AND ?
            """,
            (threshold_c, since, until),
        )
        samples = float(summary["samples"]) if summary else 0.0
        above_seconds = 0.0
        if summary is not None and samples > 1:
            span = float(summary["last_ts"]) - float(summary["first_ts"])
            interval = min(bucket, span / (samples - 1)) if span > 0 else 0.0
            above_seconds = float(summary["above_n"] or 0) * interval
        return {
            "samples": samples,
            "cpu_min": summary["cpu_min"] if summary else None,
            "cpu_max": summary["cpu_max"] if summary else None,
            "cpu_avg": summary["cpu_avg"] if summary else None,
            "gpu_min": summary["gpu_min"] if summary else None,
            "gpu_max": summary["gpu_max"] if summary else None,
            "gpu_avg": summary["gpu_avg"] if summary else None,
            "fan_avg": summary["fan_avg"] if summary else None,
            "peak_fan": summary["peak_fan"] if summary else None,
            "time_above_s": above_seconds,
        }

    def count(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM snapshots")
        return int(row["n"]) if row else 0

    def delete_before(self, timestamp: float, *, simulated_only: bool = False) -> int:
        if simulated_only:
            cursor = self._db.execute(
                "DELETE FROM snapshots WHERE ts < ? AND simulated = 1", (timestamp,)
            )
        else:
            cursor = self._db.execute("DELETE FROM snapshots WHERE ts < ?", (timestamp,))
        return cursor.rowcount or 0

    def delete_all_simulated(self) -> int:
        cursor = self._db.execute("DELETE FROM snapshots WHERE simulated = 1")
        return cursor.rowcount or 0

    def prune_to_limit(self, limit: int) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM snapshots")
        total = int(row["n"]) if row else 0
        if total <= limit:
            return 0
        cursor = self._db.execute(
            "DELETE FROM snapshots WHERE id IN ("
            "  SELECT id FROM snapshots ORDER BY ts ASC LIMIT ?"
            ")",
            (total - limit,),
        )
        return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, session: CoolingSession) -> None:
        self._db.execute(
            """
            INSERT INTO sessions(
                session_id, name, state, started_at, ended_at, profile_key, simulated,
                max_cpu_c, max_gpu_c, avg_cpu_c, avg_gpu_c, peak_rpm, avg_rpm,
                max_fan_power_w, time_above_threshold_s, esp32_connected, alert_count,
                sample_count, notes, provenance
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                name=excluded.name, state=excluded.state, ended_at=excluded.ended_at,
                profile_key=excluded.profile_key, max_cpu_c=excluded.max_cpu_c,
                max_gpu_c=excluded.max_gpu_c, avg_cpu_c=excluded.avg_cpu_c,
                avg_gpu_c=excluded.avg_gpu_c, peak_rpm=excluded.peak_rpm,
                avg_rpm=excluded.avg_rpm, max_fan_power_w=excluded.max_fan_power_w,
                time_above_threshold_s=excluded.time_above_threshold_s,
                esp32_connected=excluded.esp32_connected, alert_count=excluded.alert_count,
                sample_count=excluded.sample_count, notes=excluded.notes,
                provenance=excluded.provenance
            """,
            (
                session.session_id,
                session.name,
                session.state.value,
                _to_epoch(session.started_at),
                _to_epoch(session.ended_at),
                session.profile_key,
                1 if session.simulated else 0,
                session.max_cpu_c,
                session.max_gpu_c,
                session.avg_cpu_c,
                session.avg_gpu_c,
                session.peak_rpm,
                session.avg_rpm,
                session.max_fan_power_w,
                session.time_above_threshold_s,
                1 if session.esp32_connected else 0,
                session.alert_count,
                session.sample_count,
                session.notes,
                session.provenance.value,
            ),
        )

    def get(self, session_id: str) -> CoolingSession | None:
        row = self._db.query_one("SELECT * FROM sessions WHERE session_id=?", (session_id,))
        return self._to_session(row) if row else None

    def list_recent(self, limit: int = 50) -> list[CoolingSession]:
        rows = self._db.query(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [self._to_session(row) for row in rows]

    def delete(self, session_id: str) -> None:
        self._db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

    def add_sample(self, session_id: str, snapshot: TelemetrySnapshot) -> None:
        self._db.execute(
            "INSERT INTO session_samples(session_id, ts, cpu_temp, gpu_temp, cpu_usage, "
            "gpu_usage, fan_rpm, fan_pwm, pad_temp) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                _to_epoch(snapshot.timestamp),
                snapshot.cpu.temperature,
                snapshot.gpu.temperature,
                snapshot.cpu.usage,
                snapshot.gpu.usage,
                snapshot.fan.rpm,
                snapshot.fan.pwm_percent,
                snapshot.pad.temperature,
            ),
        )

    def samples(self, session_id: str, limit: int = 100_000) -> list[sqlite3.Row]:
        return self._db.query(
            "SELECT * FROM session_samples WHERE session_id=? ORDER BY ts ASC LIMIT ?",
            (session_id, limit),
        )

    @staticmethod
    def _to_session(row: sqlite3.Row) -> CoolingSession:
        return CoolingSession(
            session_id=row["session_id"],
            name=row["name"],
            state=SessionState.coerce(row["state"], SessionState.COMPLETED),
            started_at=_epoch_required(row["started_at"]),
            ended_at=_from_epoch(row["ended_at"]),
            profile_key=row["profile_key"],
            simulated=_b(row["simulated"]),
            max_cpu_c=_f(row["max_cpu_c"]),
            max_gpu_c=_f(row["max_gpu_c"]),
            avg_cpu_c=_f(row["avg_cpu_c"]),
            avg_gpu_c=_f(row["avg_gpu_c"]),
            peak_rpm=_f(row["peak_rpm"]),
            avg_rpm=_f(row["avg_rpm"]),
            max_fan_power_w=_f(row["max_fan_power_w"]),
            time_above_threshold_s=_f(row["time_above_threshold_s"]) or 0.0,
            esp32_connected=_b(row["esp32_connected"]),
            alert_count=int(row["alert_count"] or 0),
            sample_count=int(row["sample_count"] or 0),
            notes=row["notes"] or "",
            provenance=DataProvenance.coerce(row["provenance"], DataProvenance.MEASURED),
        )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class AlertRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, alert: AlertEvent) -> None:
        event_type = (
            alert.event_type.value if alert.event_type else AlertEventType.THERMAL_WARNING.value
        )
        parameters = (
            _to_epoch(alert.timestamp),
            alert.category.value,
            alert.severity.value,
            alert.title,
            alert.message,
            alert.dedup_key,
            alert.source,
            alert.value,
            alert.threshold,
            alert.unit,
            event_type,
            1 if alert.acknowledged else 0,
            1 if alert.dismissed else 0,
            alert.provenance.value,
        )
        if alert.alert_id is not None:
            cursor = self._db.execute(
                "UPDATE alerts SET ts=?, category=?, severity=?, title=?, message=?, "
                "dedup_key=?, source=?, value=?, threshold=?, unit=?, event_type=?, "
                "acknowledged=?, dismissed=?, provenance=? WHERE alert_id=?",
                (*parameters, alert.alert_id),
            )
            if cursor.rowcount:
                return
        cursor = self._db.execute(
            "INSERT INTO alerts(ts, category, severity, title, message, dedup_key, source, "
            "value, threshold, unit, event_type, acknowledged, dismissed, provenance) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            parameters,
        )
        if cursor.lastrowid:
            alert.alert_id = int(cursor.lastrowid)

    def list_recent(self, limit: int = 200, *, include_dismissed: bool = False) -> list[AlertEvent]:
        if include_dismissed:
            rows = self._db.query("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
        else:
            rows = self._db.query(
                "SELECT * FROM alerts WHERE dismissed=0 ORDER BY ts DESC LIMIT ?", (limit,)
            )
        return [self._to_alert(row) for row in rows]

    def last_raised(self, dedup_key: str) -> float | None:
        row = self._db.query_one(
            "SELECT MAX(ts) AS ts FROM alerts WHERE dedup_key=?",
            (dedup_key,),
        )
        return _f(row["ts"]) if row else None

    def mark(self, alert_id: int, *, acknowledged: bool | None = None, dismissed: bool | None = None) -> None:
        if acknowledged is not None:
            self._db.execute(
                "UPDATE alerts SET acknowledged=? WHERE alert_id=?",
                (1 if acknowledged else 0, alert_id),
            )
        if dismissed is not None:
            self._db.execute(
                "UPDATE alerts SET dismissed=? WHERE alert_id=?",
                (1 if dismissed else 0, alert_id),
            )

    def clear(self) -> None:
        self._db.execute("DELETE FROM alerts")

    @staticmethod
    def _to_alert(row: sqlite3.Row) -> AlertEvent:
        return AlertEvent(
            alert_id=int(row["alert_id"]),
            timestamp=_epoch_required(row["ts"]),
            category=AlertCategory.coerce(row["category"], AlertCategory.SYSTEM),
            severity=AlertSeverity.coerce(row["severity"], AlertSeverity.INFORMATION),
            title=row["title"],
            message=row["message"] or "",
            dedup_key=row["dedup_key"] or "",
            source=row["source"] or "",
            value=_f(row["value"]),
            threshold=_f(row["threshold"]),
            unit=row["unit"] or "",
            event_type=AlertEventType.coerce(row["event_type"], AlertEventType.THERMAL_WARNING),
            acknowledged=_b(row["acknowledged"]),
            dismissed=_b(row["dismissed"]),
            provenance=DataProvenance.coerce(row["provenance"], DataProvenance.DERIVED),
        )


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ProfileRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, profile: CoolingProfile) -> None:
        self._db.execute(
            "INSERT INTO profiles(key, name, builtin, payload, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET name=excluded.name, builtin=excluded.builtin, "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            (
                profile.key,
                profile.name,
                1 if profile.builtin else 0,
                json.dumps(profile.to_dict()),
                _to_epoch(datetime.now(UTC)),
            ),
        )

    def get(self, key: str) -> CoolingProfile | None:
        row = self._db.query_one("SELECT payload FROM profiles WHERE key=?", (key,))
        if row is None:
            return None
        return self._decode(row["payload"])

    def list_all(self) -> list[CoolingProfile]:
        rows = self._db.query("SELECT payload FROM profiles ORDER BY builtin DESC, name ASC")
        return [profile for row in rows if (profile := self._decode(row["payload"]))]

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM profiles WHERE key=? AND builtin=0", (key,))

    @staticmethod
    def _decode(payload: str) -> CoolingProfile | None:
        try:
            data = json.loads(payload)
            data.setdefault("kind", CoolingProfileKind.CUSTOM.value)
            data.setdefault("source", FanControlSource.MAX.value)
            profile = CoolingProfile.from_dict(data)
            profile.validate()
            return profile
        except Exception as exc:
            _log.warning("discarding unreadable profile: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


class DeviceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, device: Esp32Device) -> None:
        self._db.execute(
            "INSERT INTO devices(device_id, nickname, host, port, firmware_version, "
            "protocol_version, last_seen, trusted, is_mock) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET nickname=excluded.nickname, "
            "host=excluded.host, port=excluded.port, firmware_version=excluded.firmware_version, "
            "protocol_version=excluded.protocol_version, last_seen=excluded.last_seen, "
            "trusted=excluded.trusted, is_mock=excluded.is_mock",
            (
                device.device_id,
                device.nickname,
                device.host,
                device.port,
                device.firmware_version,
                device.protocol_version,
                _to_epoch(device.last_seen),
                1 if device.trusted else 0,
                1 if device.is_mock else 0,
            ),
        )

    def list_all(self) -> list[Esp32Device]:
        rows = self._db.query("SELECT * FROM devices ORDER BY last_seen DESC")
        return [self._to_device(row) for row in rows]

    def get(self, device_id: str) -> Esp32Device | None:
        row = self._db.query_one("SELECT * FROM devices WHERE device_id=?", (device_id,))
        return self._to_device(row) if row else None

    def delete(self, device_id: str) -> None:
        self._db.execute("DELETE FROM devices WHERE device_id=?", (device_id,))

    @staticmethod
    def _to_device(row: sqlite3.Row) -> Esp32Device:
        return Esp32Device(
            device_id=row["device_id"],
            nickname=row["nickname"] or "",
            host=row["host"] or "",
            port=int(row["port"] or 0),
            firmware_version=row["firmware_version"] or "",
            protocol_version=int(row["protocol_version"] or 0),
            last_seen=_from_epoch(row["last_seen"]),
            trusted=_b(row["trusted"]),
            is_mock=_b(row["is_mock"]),
        )


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


class BaselineRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, baseline: Baseline) -> None:
        self._db.execute(
            "INSERT INTO baselines(key, name, avg_cpu_c, avg_gpu_c, avg_fan_rpm, "
            "sample_count, created_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET name=excluded.name, avg_cpu_c=excluded.avg_cpu_c, "
            "avg_gpu_c=excluded.avg_gpu_c, avg_fan_rpm=excluded.avg_fan_rpm, "
            "sample_count=excluded.sample_count, created_at=excluded.created_at",
            (
                baseline.key,
                baseline.name,
                baseline.avg_cpu_c,
                baseline.avg_gpu_c,
                baseline.avg_fan_rpm,
                baseline.sample_count,
                _to_epoch(baseline.created_at),
            ),
        )

    def list_all(self) -> list[Baseline]:
        rows = self._db.query("SELECT * FROM baselines ORDER BY created_at DESC")
        return [
            Baseline(
                key=row["key"],
                name=row["name"],
                avg_cpu_c=_f(row["avg_cpu_c"]),
                avg_gpu_c=_f(row["avg_gpu_c"]),
                avg_fan_rpm=_f(row["avg_fan_rpm"]),
                sample_count=int(row["sample_count"] or 0),
                created_at=_epoch_required(row["created_at"]),
            )
            for row in rows
        ]

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM baselines WHERE key=?", (key,))


# ---------------------------------------------------------------------------
# Cooling responses & exports & diagnostics
# ---------------------------------------------------------------------------


class ResponseRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, response) -> None:
        self._db.execute(
            "INSERT INTO cooling_responses(started_at, ended_at, before_c, after_c, delta_c, "
            "duty_before, duty_after, stabilization_s, provenance) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                _to_epoch(response.started_at),
                _to_epoch(response.ended_at),
                response.before_temperature_c,
                response.after_temperature_c,
                response.delta_c,
                response.duty_before,
                response.duty_after,
                response.stabilization_s,
                response.provenance.value,
            ),
        )

    def recent(self, limit: int = 50) -> list[dict[str, object]]:
        rows = self._db.query(
            "SELECT * FROM cooling_responses ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]


class ExportRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, kind: str, fmt: str, path: str, row_count: int, status: str = "ok", message: str = "") -> None:
        self._db.execute(
            "INSERT INTO exports(ts, kind, format, path, row_count, status, message) "
            "VALUES(?,?,?,?,?,?,?)",
            (_to_epoch(datetime.now(UTC)), kind, fmt, path, row_count, status, message),
        )

    def recent(self, limit: int = 50) -> list[dict[str, object]]:
        rows = self._db.query("SELECT * FROM exports ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]


class DiagnosticRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, level: str, component: str, message: str) -> None:
        self._db.execute(
            "INSERT INTO diagnostics(ts, level, component, message) VALUES(?,?,?,?)",
            (_to_epoch(datetime.now(UTC)), level, component, message[:1000]),
        )

    def recent(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self._db.query("SELECT * FROM diagnostics ORDER BY ts DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]


__all__ = [
    "SeriesPoint",
    "SettingsRepository",
    "SnapshotRepository",
    "SessionRepository",
    "AlertRepository",
    "ProfileRepository",
    "DeviceRepository",
    "BaselineRepository",
    "ResponseRepository",
    "ExportRepository",
    "DiagnosticRepository",
]
