"""Persistence subsystem: database, repositories, writer and backups.

:class:`Persistence` is the single object the rest of the application holds.
It opens the database, exposes every repository, and owns the background writer
so callers never touch threads or SQL directly.
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging_setup import get_logger
from app.persistence.backup import BackupInfo, BackupManager
from app.persistence.database import Database, DatabaseError
from app.persistence.repositories import (
    AlertRepository,
    BaselineRepository,
    DeviceRepository,
    DiagnosticRepository,
    ExportRepository,
    ProfileRepository,
    ResponseRepository,
    SessionRepository,
    SettingsRepository,
    SnapshotRepository,
)
from app.persistence.schema import SCHEMA_VERSION
from app.persistence.writer import PersistenceWriter

_log = get_logger(__name__)


class Persistence:
    """Facade over the database, its repositories and the writer thread."""

    def __init__(
        self,
        database_path: Path,
        *,
        backup_dir: Path | None = None,
        retention_days: int = 30,
        max_rows: int = 2_000_000,
    ) -> None:
        self.database = Database(database_path, backup_dir=backup_dir)
        self.settings = SettingsRepository(self.database)
        self.snapshots = SnapshotRepository(self.database)
        self.sessions = SessionRepository(self.database)
        self.alerts = AlertRepository(self.database)
        self.profiles = ProfileRepository(self.database)
        self.devices = DeviceRepository(self.database)
        self.baselines = BaselineRepository(self.database)
        self.responses = ResponseRepository(self.database)
        self.exports = ExportRepository(self.database)
        self.diagnostics = DiagnosticRepository(self.database)
        self.backups = BackupManager(
            self.database, backup_dir or Path(database_path).parent / "backups"
        )
        self._retention_days = retention_days
        self._max_rows = max_rows
        self.writer: PersistenceWriter | None = None
        _log.info("persistence opened: %s (schema v%d)", database_path, SCHEMA_VERSION)

    # -- writer ------------------------------------------------------------

    def start_writer(self, queue, *, on_error=None) -> PersistenceWriter:
        self.writer = PersistenceWriter(
            queue,
            self.snapshots,
            retention_days=self._retention_days,
            max_rows=self._max_rows,
            on_error=on_error,
        )
        self.writer.start()
        return self.writer

    def stop_writer(self) -> None:
        if self.writer is not None:
            self.writer.stop()
            self.writer = None

    # -- maintenance -------------------------------------------------------

    def set_retention(self, days: int, *, max_rows: int | None = None) -> None:
        self._retention_days = max(1, int(days))
        if max_rows is not None:
            self._max_rows = max(1000, int(max_rows))
        if self.writer is not None:
            self.writer.set_retention(self._retention_days, max_rows=self._max_rows)

    def integrity_check(self) -> bool:
        try:
            row = self.database.query_one("PRAGMA integrity_check")
            return bool(row and str(row[0]).lower() == "ok")
        except Exception:
            return False

    def is_healthy(self) -> bool:
        """Cheap liveness probe for a live health panel.

        Unlike :meth:`integrity_check` — which runs ``PRAGMA integrity_check``
        and walks the entire database, making it unfit for a periodic poll — this
        issues an indexed ``SELECT`` that only touches the WAL index header.  A
        full integrity scan belongs in the support bundle, not on a timer.
        """
        try:
            self.database.query_one("SELECT MAX(ts) AS ts FROM snapshots")
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.stop_writer()
        self.database.close()


__all__ = [
    "Persistence",
    "Database",
    "DatabaseError",
    "BackupManager",
    "BackupInfo",
    "PersistenceWriter",
    "SCHEMA_VERSION",
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
