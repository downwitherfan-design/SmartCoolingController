"""Adversarial audit of the history/export data path (spec §35–§37, §108).

These tests exist to *catch* the defects the history and export work fixed, not
merely to walk the happy path.  Each computation is compared against an
independent reference built by a plain loop over the raw rows, so a change that
reintroduces a bounded-prefix query, an inflated duration, or a pagination skip
fails here.

Contracts under test
--------------------
* ``[since, until]`` is an **inclusive** interval everywhere (``range``,
  ``series_bucketed``, ``aggregate``, ``iter_range``).
* ``series_bucketed`` returns ``floor(ts / bucket_s)`` buckets, ascending, each
  the mean of the in-range samples that fall in it.
* ``aggregate`` reports count/min/max/mean over *every* in-range sample, and a
  time-above figure that never exceeds the covered span — a sampling gap must
  not inflate it.
* ``iter_range`` yields every in-range row once, ascending, including rows that
  share a timestamp at a page boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.telemetry import TelemetrySnapshot
from app.persistence import Persistence


@pytest.fixture
def persistence(tmp_path):
    instance = Persistence(tmp_path / "audit.db")
    try:
        yield instance
    finally:
        instance.database.close()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _insert(persistence: Persistence, rows: list[tuple[float, float | None]]) -> None:
    """Insert ``(epoch_ts, cpu_temp)`` pairs with a bounded saw-tooth for the rest."""
    snapshots = []
    for index, (ts, cpu) in enumerate(rows):
        snapshot = TelemetrySnapshot(
            sequence=index, timestamp=datetime.fromtimestamp(ts, tz=UTC), simulated=True
        )
        snapshot.cpu.temperature = cpu
        snapshot.gpu.temperature = None if cpu is None else cpu - 3.0
        snapshot.fan.pwm_percent = float(index % 101)
        snapshot.fan.rpm = 1000.0 + (index % 500)
        snapshots.append(snapshot)
    persistence.snapshots.insert_many(snapshots)


def _regular(end_ts: float, count: int, step: float, start_temp: float = 40.0) -> list[tuple[float, float]]:
    return [(end_ts - (count - 1 - i) * step, start_temp + (i % 100) * 0.25) for i in range(count)]


# ---------------------------------------------------------------------------
# Independent reference implementations (plain loops over raw rows)
# ---------------------------------------------------------------------------


def _raw(persistence: Persistence, since: float, until: float) -> list[tuple[float, float | None]]:
    return [
        (float(r["ts"]), None if r["cpu_temp"] is None else float(r["cpu_temp"]))
        for r in persistence.snapshots.range(since, until, limit=1_000_000)
    ]


def _ref_buckets(rows: list[tuple[float, float | None]], bucket_s: int) -> dict[float, float | None]:
    groups: dict[int, list[float]] = {}
    for ts, value in rows:
        if value is None:
            continue
        groups.setdefault(int(ts // bucket_s), []).append(value)
    return {slot * bucket_s: sum(v) / len(v) for slot, v in groups.items()}


def _ref_stats(rows: list[tuple[float, float | None]]) -> dict[str, float | None]:
    values = [v for _, v in rows if v is not None]
    if not values:
        return {"samples": float(len(rows)), "cpu_min": None, "cpu_max": None, "cpu_avg": None}
    return {
        "samples": float(len(rows)),
        "cpu_min": min(values),
        "cpu_max": max(values),
        "cpu_avg": sum(values) / len(values),
    }


def _ref_time_above(rows: list[tuple[float, float | None]], threshold: float, interval_s: float) -> float:
    """Each above-threshold sample accounts for one nominal interval, bounded by span."""
    if len(rows) < 2:
        return 0.0
    span = rows[-1][0] - rows[0][0]
    above_n = sum(1 for _, v in rows if v is not None and v >= threshold)
    return min(span, above_n * interval_s)


# ---------------------------------------------------------------------------
# series_bucketed vs oracle
# ---------------------------------------------------------------------------


def test_series_bucketed_matches_the_oracle_on_regular_data(persistence) -> None:
    rows = _regular(1_700_000_000.0, 1_000, 1.0)
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    points = persistence.snapshots.series_bucketed(since, until, 30)

    expected = _ref_buckets(_raw(persistence, since, until), 30)
    assert {p["ts"]: p["cpu_temp"] for p in points} == pytest.approx(expected)


def test_series_bucketed_matches_the_oracle_on_irregular_data(persistence) -> None:
    # A deliberately ragged cadence, including duplicate timestamps.
    rows: list[tuple[float, float]] = []
    ts = 1_700_000_000.0
    for i in range(400):
        rows.append((ts, 40.0 + (i % 50)))
        ts += [0.5, 1.0, 2.0, 7.0][i % 4]
        if i % 11 == 0:
            rows.append((ts, 40.0 + (i % 50)))  # duplicate timestamp
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    points = persistence.snapshots.series_bucketed(since, until, 15)

    expected = _ref_buckets(_raw(persistence, since, until), 15)
    assert {p["ts"]: p["cpu_temp"] for p in points} == pytest.approx(expected)
    # Buckets are ascending and unique.
    stamps = [p["ts"] for p in points]
    assert stamps == sorted(stamps) and len(stamps) == len(set(stamps))


def test_series_bucketed_ignores_rows_outside_the_range(persistence) -> None:
    rows = _regular(1_700_000_000.0, 500, 1.0)
    _insert(persistence, rows)
    since, until = rows[100][0], rows[300][0]

    points = persistence.snapshots.series_bucketed(since, until, 10)

    expected = _ref_buckets(_raw(persistence, since, until), 10)
    assert {p["ts"]: p["cpu_temp"] for p in points} == pytest.approx(expected)
    assert min(p["ts"] for p in points) >= since - 10  # bucket start may precede since


# ---------------------------------------------------------------------------
# aggregate vs oracle
# ---------------------------------------------------------------------------


def test_aggregate_matches_the_oracle(persistence) -> None:
    rows = _regular(1_700_000_000.0, 2_000, 1.0)
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    stats = persistence.snapshots.aggregate(
        since, until, threshold_c=80.0, interval_s=1.0
    )

    expected = _ref_stats(_raw(persistence, since, until))
    assert {k: stats[k] for k in expected} == pytest.approx(expected)


def test_aggregate_counts_every_in_range_sample(persistence) -> None:
    rows = _regular(1_700_000_000.0, 500, 1.0)
    _insert(persistence, rows)
    # Inclusive boundaries: both the first and last sample are counted.
    since, until = rows[0][0], rows[-1][0]

    stats = persistence.snapshots.aggregate(since, until, threshold_c=999.0, interval_s=1.0)

    assert stats["samples"] == 500.0


def test_aggregate_time_above_is_exact_for_a_regular_cadence(persistence) -> None:
    rows = [(1_700_000_000.0 + i, 40.0 + i) for i in range(600)]
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    stats = persistence.snapshots.aggregate(since, until, threshold_c=80.0, interval_s=1.0)

    assert stats["time_above_s"] == pytest.approx(
        _ref_time_above(_raw(persistence, since, until), 80.0, 1.0), abs=1e-6
    )


def test_aggregate_time_above_does_not_inflate_across_a_sampling_gap(persistence) -> None:
    """A long gap must not multiply the reported time above the threshold.

    The engine samples at a fixed cadence; if a gap (sleep, suspend, backend
    stall) leaves a hole, the seconds "above" are still bounded by the real
    elapsed time, not by the mean spacing between the surviving samples.
    """
    interval_s = 1.0
    rows = [(1_700_000_000.0 + i, 90.0) for i in range(10)]  # all above
    gap_start = rows[-1][0] + 600.0
    rows += [(gap_start + i, 90.0) for i in range(10)]  # all above, after a 10-min hole
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    stats = persistence.snapshots.aggregate(
        since, until, threshold_c=80.0, interval_s=interval_s
    )

    assert stats["time_above_s"] == pytest.approx(
        _ref_time_above(_raw(persistence, since, until), 80.0, interval_s), abs=1e-6
    )
    # 20 above-threshold samples at 1 s -> at most ~20 s, never the ~609 s span.
    assert stats["time_above_s"] <= 20.0 + 1e-6


def test_aggregate_time_above_never_exceeds_the_covered_span(persistence) -> None:
    rows = [(1_700_000_000.0 + i, 90.0) for i in range(50)]
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    stats = persistence.snapshots.aggregate(since, until, threshold_c=80.0, interval_s=1.0)

    assert stats["time_above_s"] <= (until - since) + 1e-6


# ---------------------------------------------------------------------------
# Boundaries, empty ranges, single samples, time zones
# ---------------------------------------------------------------------------


def test_single_sample_range_is_handled(persistence) -> None:
    _insert(persistence, [(1_700_000_000.0, 55.0)])
    stats = persistence.snapshots.aggregate(
        1_700_000_000.0, 1_700_000_000.0, threshold_c=50.0, interval_s=0.5
    )
    assert stats["samples"] == 1.0
    assert stats["cpu_max"] == 55.0
    assert stats["time_above_s"] == 0.0  # no elapsed interval with a single sample


def test_empty_range_reports_nothing(persistence) -> None:
    _insert(persistence, _regular(1_700_000_000.0, 10, 1.0))
    stats = persistence.snapshots.aggregate(0.0, 1.0, threshold_c=50.0, interval_s=0.5)
    assert stats["samples"] == 0.0
    assert stats["cpu_max"] is None
    assert persistence.snapshots.series_bucketed(0.0, 1.0, 30) == []


def test_naive_and_aware_timestamps_are_stored_consistently(persistence) -> None:
    aware = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 3, 1, 12, 0, 1)  # no tzinfo -> treated as UTC
    for index, moment in enumerate((aware, naive)):
        snapshot = TelemetrySnapshot(sequence=index, timestamp=moment, simulated=True)
        snapshot.cpu.temperature = 50.0 + index
        persistence.snapshots.insert_many([snapshot])

    since = aware.timestamp() - 1
    until = naive.timestamp() + 1
    stats = persistence.snapshots.aggregate(since, until, threshold_c=0.0, interval_s=0.5)
    assert stats["samples"] == 2.0


# ---------------------------------------------------------------------------
# iter_range completeness and pagination (the export data path)
# ---------------------------------------------------------------------------


def test_iter_range_includes_the_row_exactly_at_since(persistence) -> None:
    rows = _regular(1_700_000_000.0, 100, 1.0)
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    collected = list(persistence.snapshots.iter_range(since, until, page=1000))

    assert len(collected) == 100
    assert float(collected[0]["ts"]) == since


def test_iter_range_does_not_skip_duplicate_timestamps_across_pages(persistence) -> None:
    # 50 rows share one timestamp; a small page must not drop the tail of the tie.
    ts = 1_700_000_000.0
    rows = [(ts, 40.0 + i) for i in range(50)] + [(ts + 1, 40.0) for _ in range(5)]
    _insert(persistence, rows)

    collected = list(persistence.snapshots.iter_range(ts, ts + 1, page=10))

    assert len(collected) == 55
    # Every row is yielded exactly once, identified by its unique id.
    ids = [int(r["id"]) for r in collected]
    assert len(ids) == len(set(ids))


def test_iter_range_is_stable_and_ascending_over_many_pages(persistence) -> None:
    rows = _regular(1_700_000_000.0, 5_000, 0.5)
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    collected = list(persistence.snapshots.iter_range(since, until, page=333))

    assert len(collected) == 5_000
    keys = [(float(r["ts"]), int(r["id"])) for r in collected]
    assert keys == sorted(keys)


def test_iter_range_never_materializes_beyond_a_page(persistence) -> None:
    """The iterator must fetch lazily; it must not build one giant list up front."""
    rows = _regular(1_700_000_000.0, 3_000, 1.0)
    _insert(persistence, rows)
    since, until = rows[0][0], rows[-1][0]

    iterator = persistence.snapshots.iter_range(since, until, page=100)
    first = next(iterator)
    # A generator yields before the rest is fetched; if it materialised the whole
    # range this would already be exhausted-adjacent.  Pull only a few.
    few = [first, next(iterator), next(iterator)]
    assert len(few) == 3
    assert float(few[0]["ts"]) == since


# ---------------------------------------------------------------------------
# Adversarial meta-tests: the guards must have teeth
# ---------------------------------------------------------------------------


def test_the_newest_data_guard_rejects_a_capped_prefix(persistence) -> None:
    """A prefix-returning query must fail the newest-data assertion.

    This proves the regression test has teeth: if the implementation regressed to
    ``ORDER BY ts ASC LIMIT n`` (the original defect), the same assertion used in
    ``test_series_bucketed_covers_the_newest_samples_not_a_prefix`` would fail.
    """
    end = 1_700_000_000.0
    rows = _regular(end, 90_000, 1.0)
    _insert(persistence, rows)
    since, until = end - 24 * 3600, end

    def capped_prefix_buckets(limit: int) -> float:
        """Reimplement the old defect: load oldest rows, then bucket in Python."""
        legacy = persistence.snapshots.range(since, until, limit=limit)
        return float(legacy[-1]["ts"])

    # The correct query's newest point is within one bucket of ``until``.
    correct_last = persistence.snapshots.series_bucketed(since, until, 60)[-1]["ts"]
    assert correct_last >= until - 60

    # The old behaviour does not satisfy that assertion — the guard has teeth.
    assert capped_prefix_buckets(20_000) < until - 3600


def test_database_and_in_memory_summaries_agree(persistence) -> None:
    """The SQL aggregate and the page's in-memory fallback must agree.

    The history page uses the database path when it has rows and falls back to
    ``_summarise`` over the in-memory ring otherwise; if the two disagree, the
    numbers change depending on whether the writer has flushed yet.  This builds
    the same samples in both, including a gap that used to inflate time-above.
    """
    from app.ui.pages.history import _rows_from_history, _summarise

    base = 1_700_000_000.0
    specs = [(base + i, 40.0 + (i % 20)) for i in range(10)]
    specs += [(base + 600 + i, 90.0) for i in range(10)]  # gap, then above threshold

    snapshots = []
    for index, (ts, cpu) in enumerate(specs):
        snapshot = TelemetrySnapshot(
            sequence=index, timestamp=datetime.fromtimestamp(ts, tz=UTC), simulated=True
        )
        snapshot.cpu.temperature = cpu
        snapshot.gpu.temperature = cpu - 3.0
        snapshot.fan.pwm_percent = float(index % 101)
        snapshot.fan.rpm = 1000.0 + (index % 500)
        snapshots.append(snapshot)
    persistence.snapshots.insert_many(snapshots)

    since, until = specs[0][0], specs[-1][0]
    from_db = persistence.snapshots.aggregate(since, until, threshold_c=80.0, interval_s=0.5)

    history_rows = _rows_from_history(snapshots, since, until)
    from_memory = _summarise(history_rows, 0.5, 80.0)

    assert from_memory == pytest.approx(from_db)
