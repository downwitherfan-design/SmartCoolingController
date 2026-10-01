"""Data export service (spec §59, §60, §61, §62, §103).

Exports are written to a temporary file and atomically moved into place, so an
interrupted export never leaves a half-written file that looks valid.

Every export carries provenance.  Simulated samples are marked in a dedicated
column/field and the JSON envelope records how many rows were simulated, so a
report can never quietly present synthetic data as a real measurement
(spec §29, §104).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.clock import to_iso, utc_now
from app.core.enums import ExportFormat
from app.core.errors import ExportError
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.persistence.repositories import (
    ExportRepository,
    SessionRepository,
    SnapshotRepository,
)

_log = get_logger(__name__)

TOPIC_EXPORT = "export.finished"

SNAPSHOT_COLUMNS = [
    "timestamp",
    "sequence",
    "cpu_temperature_c",
    "gpu_temperature_c",
    "cpu_usage_percent",
    "gpu_usage_percent",
    "memory_usage_percent",
    "cpu_clock_mhz",
    "fan_rpm",
    "fan_pwm_percent",
    "fan_target_percent",
    "fan_power_w",
    "pad_temperature_c",
    "thermal_state",
    "validity",
    "simulated",
]


@dataclass(slots=True)
class ExportResult:
    path: Path
    format: ExportFormat
    row_count: int
    simulated_rows: int
    started_at: datetime
    finished_at: datetime
    kind: str

    @property
    def duration_s(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "format": self.format.value,
            "row_count": self.row_count,
            "simulated_rows": self.simulated_rows,
            "started_at": to_iso(self.started_at),
            "finished_at": to_iso(self.finished_at),
            "duration_s": round(self.duration_s, 3),
            "kind": self.kind,
        }


class ExportService:
    """Exports telemetry, sessions and alerts to CSV or JSON."""

    def __init__(
        self,
        snapshots: SnapshotRepository,
        sessions: SessionRepository,
        exports: ExportRepository,
        bus: EventBus,
        *,
        export_dir: Path,
    ) -> None:
        self._snapshots = snapshots
        self._sessions = sessions
        self._exports = exports
        self._bus = bus
        self._dir = Path(export_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- public API --------------------------------------------------------

    def export_range(
        self,
        *,
        since: float,
        until: float | None = None,
        fmt: ExportFormat = ExportFormat.CSV,
        name: str | None = None,
    ) -> ExportResult:
        rows = self._snapshots.range(since, until)
        return self._write(
            rows,
            fmt=fmt,
            kind="telemetry",
            name=name or "telemetry",
            columns=SNAPSHOT_COLUMNS,
            record=lambda row: self._snapshot_row(row),
        )

    def export_session(
        self,
        session_id: str,
        *,
        fmt: ExportFormat = ExportFormat.CSV,
        name: str | None = None,
    ) -> ExportResult:
        rows = self._sessions.samples(session_id)
        columns = [
            "timestamp", "cpu_temperature_c", "gpu_temperature_c",
            "cpu_usage_percent", "gpu_usage_percent", "fan_rpm",
            "fan_pwm_percent", "pad_temperature_c",
        ]
        return self._write(
            rows,
            fmt=fmt,
            kind=f"session:{session_id}",
            name=name or f"session-{session_id}",
            columns=columns,
            record=lambda row: {
                "timestamp": to_iso(_dt(row["ts"])),
                "cpu_temperature_c": row["cpu_temp"],
                "gpu_temperature_c": row["gpu_temp"],
                "cpu_usage_percent": row["cpu_usage"],
                "gpu_usage_percent": row["gpu_usage"],
                "fan_rpm": row["fan_rpm"],
                "fan_pwm_percent": row["fan_pwm"],
                "pad_temperature_c": row["pad_temp"],
            },
        )

    def history(self, limit: int = 50) -> list[dict[str, object]]:
        return self._exports.recent(limit)

    # -- internals ---------------------------------------------------------

    def _write(self, rows, *, fmt, kind, name, columns, record) -> ExportResult:
        started = utc_now()
        stamp = started.strftime("%Y%m%d-%H%M%S")
        suffix = "csv" if fmt is ExportFormat.CSV else "json"
        final_path = self._dir / f"{name}-{stamp}.{suffix}"
        temp_path = final_path.with_suffix(final_path.suffix + ".part")
        simulated = 0
        count = 0
        try:
            if fmt is ExportFormat.CSV:
                count, simulated = self._write_csv(temp_path, rows, columns, record)
            else:
                count, simulated = self._write_json(temp_path, rows, columns, record, kind)
            temp_path.replace(final_path)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            self._exports.record(kind, fmt.value, str(final_path), 0, "error", str(exc))
            raise ExportError(f"export failed: {exc}") from exc

        result = ExportResult(
            path=final_path,
            format=fmt,
            row_count=count,
            simulated_rows=simulated,
            started_at=started,
            finished_at=utc_now(),
            kind=kind,
        )
        self._exports.record(kind, fmt.value, str(final_path), count, "ok", "")
        self._bus.publish(TOPIC_EXPORT, result)
        _log.info("exported %d rows to %s", count, final_path.name)
        return result

    @staticmethod
    def _write_csv(path: Path, rows, columns, record) -> tuple[int, int]:
        count = 0
        simulated = 0
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                data = record(row)
                writer.writerow(data)
                count += 1
                if data.get("simulated") in (1, True, "1"):
                    simulated += 1
        return count, simulated

    @staticmethod
    def _write_json(path: Path, rows, columns, record, kind) -> tuple[int, int]:
        count = 0
        simulated = 0
        with path.open("w", encoding="utf-8") as handle:
            handle.write("{\n")
            handle.write(f'  "kind": {json.dumps(kind)},\n')
            handle.write(f'  "generated_at": {json.dumps(to_iso(utc_now()))},\n')
            handle.write(f'  "columns": {json.dumps(columns)},\n')
            handle.write('  "rows": [\n')
            first = True
            for row in rows:
                data = record(row)
                if not first:
                    handle.write(",\n")
                handle.write("    " + json.dumps(data, default=str))
                first = False
                count += 1
                if data.get("simulated") in (1, True, "1"):
                    simulated += 1
            handle.write("\n  ],\n")
            handle.write(f'  "row_count": {count},\n')
            handle.write(f'  "simulated_rows": {simulated}\n')
            handle.write("}\n")
        return count, simulated

    @staticmethod
    def _snapshot_row(row) -> dict[str, object]:
        return {
            "timestamp": to_iso(_dt(row["ts"])),
            "sequence": row["seq"],
            "cpu_temperature_c": row["cpu_temp"],
            "gpu_temperature_c": row["gpu_temp"],
            "cpu_usage_percent": row["cpu_usage"],
            "gpu_usage_percent": row["gpu_usage"],
            "memory_usage_percent": row["ram_usage"],
            "cpu_clock_mhz": row["cpu_clock_mhz"],
            "fan_rpm": row["fan_rpm"],
            "fan_pwm_percent": row["fan_pwm"],
            "fan_target_percent": row["fan_target"],
            "fan_power_w": row["fan_power_w"],
            "pad_temperature_c": row["pad_temp"],
            "thermal_state": row["thermal_state"],
            "validity": row["validity"],
            "simulated": row["simulated"],
        }


def _dt(epoch: Any) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=UTC)


__all__ = ["ExportService", "ExportResult", "SNAPSHOT_COLUMNS", "TOPIC_EXPORT"]
