"""Resilient SQLite connection handling (spec §55–§58, §108–§110, §206).

The connection layer is deliberately conservative:

* WAL journalling for crash safety and concurrent reads.
* ``synchronous=NORMAL`` — the right durability/throughput trade-off with WAL.
* A busy timeout so transient locks resolve instead of raising.
* Integrity checking on open; if the file is corrupt it is moved aside and a
  fresh database is created, so the application always starts (spec §56).
* Automatic periodic checkpoints and ``VACUUM`` when the file grows past a
  threshold (spec §206).

All writes happen through :class:`Database`, which is used from the persistence
writer thread and from the UI thread for small configuration writes; the
connection itself is guarded by a re-entrant lock.
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from app.core.logging_setup import get_logger
from app.persistence.schema import SCHEMA_VERSION, migrations_from

_log = get_logger(__name__)

#: Re-run ``VACUUM`` once the database exceeds this size.
VACUUM_THRESHOLD_BYTES = 64 * 1024 * 1024
#: Force a WAL checkpoint after this many writes.
CHECKPOINT_EVERY_WRITES = 2000


class DatabaseError(RuntimeError):
    """Raised when the database cannot be opened or recovered."""


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into individual statements.

    ``executescript`` issues an implicit COMMIT, which would break the explicit
    transaction we use to keep migrations atomic, so we split and execute each
    statement ourselves.  Semicolons inside string literals are respected.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_string: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if in_string is not None:
            buffer.append(char)
            if char == in_string:
                if index + 1 < len(sql) and sql[index + 1] == in_string:
                    buffer.append(sql[index + 1])
                    index += 2
                    continue
                in_string = None
            index += 1
            continue
        if char in {"'", '"'}:
            in_string = char
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


class Database:
    """A thread-safe SQLite wrapper with integrity recovery and migrations."""

    def __init__(
        self,
        path: Path,
        *,
        backup_dir: Path | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.path = Path(path)
        # A caller may hand us the containing folder by mistake (or by design);
        # resolve it to the database file so we never try to open a directory.
        if self.path.is_dir():
            self.path = self.path / "SmartCooling.db"
        self.backup_dir = Path(backup_dir) if backup_dir else self.path.parent / "backups"
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._writes_since_checkpoint = 0
        self._open(timeout_s)

    # -- lifecycle ---------------------------------------------------------

    def _open(self, timeout_s: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = self._connect(timeout_s)
            self._verify(connection)
        except sqlite3.DatabaseError as exc:
            _log.error("database integrity check failed: %s", exc)
            connection = self._recover(exc, timeout_s)
        self._connection = connection
        self._migrate()

    def _connect(self, timeout_s: float) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=timeout_s,
            isolation_level=None,  # autocommit; explicit BEGIN for transactions
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA mmap_size=268435456")
        return connection

    def _verify(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA quick_check").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"quick_check reported: {row[0] if row else 'no result'}")

    def _recover(self, exc: Exception, timeout_s: float) -> sqlite3.Connection:
        """Move a corrupt database aside and start fresh, keeping the data file."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if self.path.exists():
            corrupt = self.backup_dir / f"corrupt-{self.path.stem}-{stamp}.db"
            with contextlib.suppress(OSError):
                shutil.move(str(self.path), str(corrupt))
                _log.warning("moved corrupt database to %s", corrupt)
        for suffix in ("-wal", "-shm"):
            side = self.path.with_name(self.path.name + suffix)
            with contextlib.suppress(OSError):
                side.unlink()
        connection = self._connect(timeout_s)
        _log.info("created a fresh database after corruption (%s)", exc)
        return connection

    # -- migrations --------------------------------------------------------

    def _migrate(self) -> None:
        connection = self._require()
        with self._lock:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            steps = migrations_from(current)
            if not steps:
                return
            for version, sql in steps:
                _log.info("applying database migration -> v%d", version)
                connection.execute("BEGIN")
                try:
                    for statement in _split_statements(sql):
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version={version}")
                    connection.execute("COMMIT")
                except sqlite3.Error as exc:
                    with contextlib.suppress(sqlite3.Error):
                        connection.execute("ROLLBACK")
                    raise DatabaseError(f"migration to v{version} failed: {exc}") from exc
            self._set_meta("schema_version", str(SCHEMA_VERSION))

    # -- access ------------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        return self._require()

    def _require(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DatabaseError("database is closed")
        return self._connection

    @contextlib.contextmanager
    def transaction(self):
        with self._lock:
            connection = self._require()
            connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    def execute(self, sql: str, parameters: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._require().execute(sql, parameters)
            self._after_write()
            return cursor

    def executemany(self, sql: str, rows) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._require().executemany(sql, rows)
            self._after_write()
            return cursor

    def query(self, sql: str, parameters: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._require().execute(sql, parameters).fetchall()

    def query_one(self, sql: str, parameters: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._require().execute(sql, parameters).fetchone()

    def _after_write(self) -> None:
        self._writes_since_checkpoint += 1
        if self._writes_since_checkpoint >= CHECKPOINT_EVERY_WRITES:
            self._writes_since_checkpoint = 0
            self._checkpoint()

    def _checkpoint(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self._require().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.maintain()

    # -- maintenance -------------------------------------------------------

    def maintain(self) -> None:
        """Reclaim space when the database has grown large."""
        with contextlib.suppress(OSError):
            if self.path.stat().st_size >= VACUUM_THRESHOLD_BYTES:
                _log.info("vacuuming database (%d bytes)", self.path.stat().st_size)
                with self._lock:
                    self._require().execute("VACUUM")

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    # -- meta helpers ------------------------------------------------------

    def _set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        return str(row["value"]) if row else default

    # -- close -------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                with contextlib.suppress(sqlite3.Error):
                    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                with contextlib.suppress(sqlite3.Error):
                    self._connection.close()
                self._connection = None


__all__ = ["Database", "DatabaseError", "VACUUM_THRESHOLD_BYTES", "CHECKPOINT_EVERY_WRITES"]
