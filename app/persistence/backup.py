"""Database backup and restore (spec §58, §110, §205).

Backups use SQLite's online backup API so a snapshot is taken consistently even
while the application is writing.  Restores are always *safe*: the current
database is copied to a ``pre-restore`` backup first, so a bad restore can be
undone.  Old backups are pruned to a bounded count so the disk never fills.

Restoring requires a restart because the live connection must be reopened;
:func:`restore` performs the file swap and reports that a restart is needed.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.logging_setup import get_logger
from app.persistence.database import Database

_log = get_logger(__name__)

DEFAULT_BACKUP_COUNT = 10


@dataclass(slots=True)
class BackupInfo:
    path: Path
    created_at: float
    size_bytes: int
    kind: str = "manual"

    @property
    def name(self) -> str:
        return self.path.name


class BackupManager:
    """Creates, lists, prunes and restores database backups."""

    def __init__(
        self,
        database: Database,
        backup_dir: Path,
        *,
        keep: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self._db = database
        self._dir = Path(backup_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._keep = max(1, keep)

    # -- create ------------------------------------------------------------

    def create(self, *, kind: str = "manual") -> BackupInfo:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self._dir / f"smartcooling-{kind}-{stamp}.db"
        counter = 1
        while target.exists():
            target = self._dir / f"smartcooling-{kind}-{stamp}-{counter}.db"
            counter += 1

        source = self._db.connection
        destination = sqlite3.connect(str(target))
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
        self._prune()
        info = BackupInfo(target, target.stat().st_mtime, target.stat().st_size, kind)
        _log.info("database backup created: %s (%d bytes)", info.name, info.size_bytes)
        return info

    # -- list / prune ------------------------------------------------------

    def list(self) -> list[BackupInfo]:
        items: list[BackupInfo] = []
        for path in sorted(self._dir.glob("smartcooling-*.db")):
            with contextlib.suppress(OSError):
                stat = path.stat()
                kind = "pre-restore" if "pre-restore" in path.name else (
                    "automatic" if "automatic" in path.name else "manual"
                )
                items.append(BackupInfo(path, stat.st_mtime, stat.st_size, kind))
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def _prune(self) -> None:
        items = self.list()
        # Never prune pre-restore safety copies automatically.
        removable = [item for item in items if item.kind != "pre-restore"]
        for item in removable[self._keep :]:
            with contextlib.suppress(OSError):
                item.path.unlink()
                _log.info("pruned old backup %s", item.name)

    # -- restore -----------------------------------------------------------

    def restore(self, backup_path: Path, *, verify: bool = True) -> None:
        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise FileNotFoundError(f"backup not found: {backup_path}")
        if verify:
            self._verify_backup(backup_path)

        # Safety copy of the live database before we overwrite it.
        safety = self._dir / f"smartcooling-pre-restore-{time.strftime('%Y%m%d-%H%M%S')}.db"
        self._db.close()
        with contextlib.suppress(OSError):
            self._db.path.replace(safety)
        for suffix in ("-wal", "-shm"):
            side = self._db.path.with_name(self._db.path.name + suffix)
            with contextlib.suppress(OSError):
                side.unlink()

        import shutil

        shutil.copy2(backup_path, self._db.path)
        _log.warning(
            "database restored from %s (previous copy kept at %s)",
            backup_path.name,
            safety.name,
        )

    @staticmethod
    def _verify_backup(path: Path) -> None:
        connection = sqlite3.connect(str(path))
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
            if row is None or str(row[0]).lower() != "ok":
                raise ValueError(f"backup failed integrity check: {path.name}")
        finally:
            connection.close()

    # -- import / export ---------------------------------------------------

    def export_to(self, destination: Path) -> Path:
        """Write a consistent copy of the database to an arbitrary path."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            with target:
                self._db.connection.backup(target)
        finally:
            target.close()
        return destination

    def import_from(self, source: Path, *, verify: bool = True) -> None:
        """Restore from an external file (same semantics as :meth:`restore`)."""
        self.restore(Path(source), verify=verify)


__all__ = ["BackupManager", "BackupInfo", "DEFAULT_BACKUP_COUNT"]
