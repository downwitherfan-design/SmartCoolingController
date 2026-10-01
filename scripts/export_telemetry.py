#!/usr/bin/env python3
"""Export recorded telemetry without the GUI (spec §120, §121).

Support and power users often need a CSV from a machine that cannot show a
window (RDP, a locked session, a head-less test box).  This tool opens the same
SQLite database the application uses, runs the same :class:`ExportService`, and
writes the file to the standard Exports folder.

Usage::

    python scripts/export_telemetry.py --last 24h
    python scripts/export_telemetry.py --since 2026-01-01T00:00:00Z --format json
    python scripts/export_telemetry.py --data-dir D:\\SmartCoolingData --last 1h
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import utc_now
from app.core.enums import DataMode, ExportFormat
from app.core.paths import AppPaths

_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def parse_last(value: str) -> float:
    """Parse ``24h`` / ``30m`` / ``7d`` into a "seconds ago" epoch."""
    value = value.strip().lower()
    unit = value[-1] if value and value[-1] in _UNITS else "s"
    number = value[:-1] if value[-1:] in _UNITS else value
    try:
        amount = float(number)
    except ValueError as exc:
        raise SystemExit(f"invalid duration {value!r} (expected e.g. 24h, 30m, 7d)") from exc
    return amount * _UNITS[unit]


def parse_since(value: str) -> float:
    """Parse an ISO-8601 timestamp, with or without a timezone."""
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"invalid timestamp {value!r} (expected ISO-8601)") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export recorded telemetry to CSV or JSON.")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--last", metavar="DURATION", help="e.g. 24h, 30m, 7d (default: 24h)")
    window.add_argument("--since", metavar="TIMESTAMP", help="ISO-8601 start time")
    parser.add_argument("--until", metavar="TIMESTAMP", help="ISO-8601 end time (default: now)")
    parser.add_argument(
        "--format", choices=[fmt.value for fmt in ExportFormat], default=ExportFormat.CSV.value
    )
    parser.add_argument("--data-dir", metavar="PATH", help="override the data directory")
    parser.add_argument("--mode", choices=[m.value for m in DataMode], default=None)
    parser.add_argument("--name", default=None, help="output base name (no extension)")
    args = parser.parse_args(argv)

    if args.data_dir:
        import os

        os.environ["SMARTCOOLING_DATA_DIR"] = str(Path(args.data_dir).expanduser())

    mode = DataMode(args.mode) if args.mode else None
    paths = AppPaths.resolve(mode=mode)
    paths.ensure()
    database = paths.database_file
    if not database.exists():
        raise SystemExit(f"no telemetry database at {database}")

    # Imports are deferred so ``--help`` stays instant and dependency-light.
    from app.core.events import EventBus
    from app.persistence.database import Database
    from app.persistence.repositories import (
        AlertRepository,
        ExportRepository,
        SessionRepository,
        SnapshotRepository,
    )
    from app.services.export import ExportService

    database_handle = Database(database)  # the constructor opens and migrates
    try:
        snapshots = SnapshotRepository(database_handle)
        sessions = SessionRepository(database_handle)
        exports = ExportRepository(database_handle)
        AlertRepository(database_handle)  # created so the schema is complete
        service = ExportService(
            snapshots, sessions, exports, EventBus(), export_dir=paths.exports
        )

        since = parse_since(args.since) if args.since else utc_now().timestamp() - parse_last(
            args.last or "24h"
        )
        until = parse_since(args.until) if args.until else None
        result = service.export_range(
            since=since,
            until=until,
            fmt=ExportFormat(args.format),
            name=args.name,
        )
    finally:
        database_handle.close()

    print(f"wrote {result.row_count} rows ({result.simulated_rows} simulated) to:")
    print(f"  {result.path}")
    print(f"in {result.duration_s * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
