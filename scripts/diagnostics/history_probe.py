"""Benchmark the history page's data path at representative database sizes.

The history page must stay responsive — and *correct* — when the user switches
range on a database that has been recording for days.  This probe compares the
old approach (load rows with ``range(limit=MAX_POINTS)``, decimate in Python) with
the new one (aggregate inside SQLite) against a real database built by the
application's own :class:`~app.persistence.repositories.SnapshotRepository`.

It reports, per range preset:

- rows the old path loaded vs. buckets the new path returns,
- the newest timestamp covered by the old capped query (the defect: an ascending
  ``LIMIT`` returns the *oldest* rows, hiding recent data),
- wall time for query / project / render on both paths.

It is a diagnostic, not a test: run it by hand and read the numbers.

Run:
    QT_QPA_PLATFORM=offscreen python scripts/diagnostics/history_probe.py \
        --rows 86400 --rows 250000
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.enums import Language, ThemeMode  # noqa: E402
from app.ui.design import build_theme  # noqa: E402
from app.ui.i18n import Translator  # noqa: E402
from app.ui.pages.history import MAX_POINTS, RANGES  # noqa: E402


def _make_snapshot(sequence: int, ts: datetime):
    from app.models.telemetry import TelemetrySnapshot

    snapshot = TelemetrySnapshot(sequence=sequence, timestamp=ts, simulated=True)
    snapshot.cpu.temperature = 45.0 + (sequence % 40) * 0.5
    snapshot.cpu.usage = float(sequence % 100)
    snapshot.gpu.temperature = 50.0 + (sequence % 35) * 0.5
    snapshot.gpu.usage = float((sequence * 3) % 100)
    snapshot.memory.usage_percent = 40.0 + (sequence % 20)
    snapshot.fan.rpm = 1200.0 + (sequence % 800)
    snapshot.fan.pwm_percent = float(sequence % 101)
    snapshot.fan.target_percent = float(sequence % 101)
    snapshot.fan.power_w = 1.5 + (sequence % 10) * 0.1
    snapshot.pad.temperature = 30.0 + (sequence % 10)
    return snapshot


def _build_database(rows: int, seconds_between: float):
    from app.persistence import Persistence

    directory = Path(tempfile.mkdtemp(prefix="sc-history-probe-"))
    persistence = Persistence(directory / "history.db")
    end = datetime.now(UTC)
    batch_size = 2000
    for start in range(0, rows, batch_size):
        stop = min(start + batch_size, rows)
        persistence.snapshots.insert_many(
            [
                _make_snapshot(i, end - timedelta(seconds=(rows - 1 - i) * seconds_between))
                for i in range(start, stop)
            ]
        )
    return persistence


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _old_path(persistence, since: float, until: float, bucket_s: int, chart) -> dict[str, float]:
    """The previous implementation: capped rows, decimate in Python."""
    started = time.perf_counter()
    rows = persistence.snapshots.range(since, until, limit=MAX_POINTS)
    query_s = time.perf_counter() - started
    newest_covered = float(rows[-1]["ts"]) if rows else since

    started = time.perf_counter()
    buckets: dict[int, dict[str, list[float]]] = {}
    for row in rows:
        slot = buckets.setdefault(int(float(row["ts"]) // bucket_s), {"cpu": [], "gpu": [], "fan": []})
        for name, column in (("cpu", "cpu_temp"), ("gpu", "gpu_temp"), ("fan", "fan_pwm")):
            if row[column] is not None:
                slot[name].append(float(row[column]))
    points = {name: [(b * bucket_s, _mean(v[name])) for b, v in sorted(buckets.items())] for name in ("cpu", "gpu", "fan")}
    project_s = time.perf_counter() - started

    started = time.perf_counter()
    chart.clear()
    for name in ("cpu", "gpu", "fan"):
        chart.set_points(name, [p for p in points[name] if p[1] is not None])
    render_s = time.perf_counter() - started

    return {
        "rows": float(len(rows)),
        "points": float(len(points["cpu"])),
        "newest_covered": newest_covered,
        "query_ms": query_s * 1000.0,
        "project_ms": project_s * 1000.0,
        "render_ms": render_s * 1000.0,
        "total_ms": (query_s + project_s + render_s) * 1000.0,
    }


def _new_path(persistence, since: float, until: float, bucket_s: int, chart) -> dict[str, float]:
    """The new implementation: aggregate inside SQLite."""
    started = time.perf_counter()
    rows = persistence.snapshots.series_bucketed(since, until, bucket_s)
    stats = persistence.snapshots.aggregate(since, until, threshold_c=80.0, interval_s=0.5)
    query_s = time.perf_counter() - started
    newest_covered = float(rows[-1]["ts"]) if rows else since

    started = time.perf_counter()
    chart.clear()
    for name, field in (("cpu", "cpu_temp"), ("gpu", "gpu_temp"), ("fan", "fan_pwm")):
        chart.set_points(name, [(r["ts"], float(r[field])) for r in rows if r[field] is not None])
    render_s = time.perf_counter() - started
    _ = stats

    return {
        "rows": float(len(rows)),
        "points": float(len(rows)),
        "newest_covered": newest_covered,
        "query_ms": query_s * 1000.0,
        "project_ms": 0.0,
        "render_ms": render_s * 1000.0,
        "total_ms": (query_s + render_s) * 1000.0,
    }


def _make_panel():
    from app.ui.widgets.charts import ChartPanel

    theme = build_theme(ThemeMode.DARK)
    translator = Translator(Language.ENGLISH)
    panel = ChartPanel(theme, translator, title="history", ranges=[], capacity=MAX_POINTS)
    panel.add_series("cpu", "CPU", unit="C", color_index=0)
    panel.add_series("gpu", "GPU", unit="C", color_index=1)
    panel.add_series("fan", "Fan", unit="%", color_index=2)
    return panel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, action="append", default=None)
    parser.add_argument("--seconds-between", type=float, default=1.0)
    args = parser.parse_args()
    sizes = args.rows or [86_400, 250_000]

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    print("History data-path probe: old (capped rows) vs new (SQL aggregation)")
    print(f"seconds between samples: {args.seconds_between:g}")

    for size in sizes:
        print(f"\n=== database: {size:,} rows ===")
        started = time.perf_counter()
        persistence = _build_database(size, args.seconds_between)
        print(f"  (built in {time.perf_counter() - started:.1f}s)")
        panel = _make_panel()
        until = datetime.now(UTC).timestamp()

        for key, _label, hours, bucket_s in RANGES:
            since = until - hours * 3600
            old = _old_path(persistence, since, until, bucket_s, panel.chart)
            new = _new_path(persistence, since, until, bucket_s, panel.chart)
            old_gap = (until - old["newest_covered"]) / 60.0
            new_gap = (until - new["newest_covered"]) / 60.0
            print(f"\n  range {key:>4} ({hours:g}h, bucket {bucket_s}s):")
            print(
                f"    old: rows={int(old['rows']):>6} points={int(old['points']):>5} "
                f"query={old['query_ms']:7.1f}ms project={old['project_ms']:6.1f}ms "
                f"render={old['render_ms']:5.1f}ms total={old['total_ms']:7.1f}ms "
                f"| newest missing by {old_gap:6.1f} min"
            )
            print(
                f"    new: points={int(new['points']):>5} "
                f"query={new['query_ms']:7.1f}ms project={new['project_ms']:6.1f}ms "
                f"render={new['render_ms']:5.1f}ms total={new['total_ms']:7.1f}ms "
                f"| newest missing by {new_gap:6.1f} min"
            )

        panel.deleteLater()
        persistence.database.close()
        app.processEvents()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
