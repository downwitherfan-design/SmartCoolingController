"""History range queries (spec §35–§37, §108).

These exercise the repository queries the history page relies on, against a real
SQLite database.  They pin the defect that motivated the SQL-side aggregation:
an ascending ``LIMIT`` over a capped row count returns the *oldest* rows of a
wide range, so a "24h" view silently hid the most recent hours and reported
statistics over a truncated prefix.  The bucketed query must cover the whole
range and the aggregate must describe every sample.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.models.telemetry import TelemetrySnapshot
from app.persistence import Persistence


@pytest.fixture
def persistence(tmp_path):
    instance = Persistence(tmp_path / "history.db")
    try:
        yield instance
    finally:
        instance.database.close()


def _sawtooth(index: int) -> float:
    """A bounded, saw-tooth CPU curve so min/max are stable in any window."""
    return 40.0 + (index % 100) * 0.25


def _seed(
    persistence: Persistence,
    *,
    end: datetime,
    count: int,
    step_s: float,
    cpu: Callable[[int], float] = _sawtooth,
) -> list[TelemetrySnapshot]:
    """Insert ``count`` samples ending at ``end``, spaced ``step_s`` apart."""
    snapshots = []
    for index in range(count):
        ts = end - timedelta(seconds=(count - 1 - index) * step_s)
        snapshot = TelemetrySnapshot(sequence=index, timestamp=ts, simulated=True)
        snapshot.cpu.temperature = cpu(index)
        snapshot.gpu.temperature = cpu(index) - 2.0
        snapshot.fan.pwm_percent = float(index % 101)
        snapshot.fan.rpm = 1000.0 + (index % 500)
        snapshots.append(snapshot)
    persistence.snapshots.insert_many(snapshots)
    return snapshots


def test_series_bucketed_covers_the_newest_samples_not_a_prefix(persistence) -> None:
    # 90_000 samples at 1 s spans 25 h; a 24 h range is wider than a 20k row cap.
    end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    _seed(persistence, end=end, count=90_000, step_s=1.0)
    until = end.timestamp()
    since = until - 24 * 3600

    points = persistence.snapshots.series_bucketed(since, until, bucket_s=60)

    assert points, "the range has data"
    # The whole 24 h is covered, and the last point is within one bucket of now.
    assert points[0]["ts"] >= since
    assert until - 60 <= points[-1]["ts"] <= until
    # One point per minute of the 24 h range.
    assert len(points) == pytest.approx(24 * 60, abs=2)

    # Contrast: the legacy capped query returns the oldest rows, so its newest
    # row is many hours stale — the defect the bucketed query fixes.
    legacy = persistence.snapshots.range(since, until, limit=20_000)
    assert legacy[-1]["ts"] < until - 3600


def test_series_bucketed_averages_within_each_bucket(persistence) -> None:
    end = datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)
    snapshots = _seed(persistence, end=end, count=600, step_s=1.0)
    until = end.timestamp()
    since = until - 600
    bucket_s = 100

    points = persistence.snapshots.series_bucketed(since, until, bucket_s)

    first = points[0]
    members = [
        float(s.cpu.temperature)
        for s in snapshots
        if first["ts"] <= s.timestamp.timestamp() < first["ts"] + bucket_s
    ]
    assert members, "the first bucket holds samples"
    assert first["cpu_temp"] == pytest.approx(sum(members) / len(members), abs=1e-6)


def test_aggregate_describes_every_sample_in_the_range(persistence) -> None:
    end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    _seed(persistence, end=end, count=90_000, step_s=1.0)
    until = end.timestamp()
    since = until - 24 * 3600

    stats = persistence.snapshots.aggregate(since, until, threshold_c=80.0, bucket_s=60)

    # 86_401 samples fall in the closed 24 h window, not the 20k row cap.
    assert stats["samples"] == pytest.approx(86_401, abs=2)
    # The saw-tooth reaches its extremes inside the window.
    assert stats["cpu_max"] == pytest.approx(64.75, abs=0.01)
    assert stats["cpu_min"] == pytest.approx(40.0, abs=0.01)


def test_aggregate_time_above_uses_the_sample_interval(persistence) -> None:
    end = datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)
    # A ramp crossing 80 °C at index 160 and staying above for the rest.
    _seed(persistence, end=end, count=600, step_s=1.0, cpu=lambda index: 40.0 + index * 0.25)
    until = end.timestamp()
    since = until - 600

    stats = persistence.snapshots.aggregate(since, until, threshold_c=80.0, bucket_s=60)

    # 440 samples at or above the threshold, one second apart -> 440 s.
    assert stats["time_above_s"] == pytest.approx(440.0, abs=2.0)


def test_aggregate_on_an_empty_range_is_all_none(persistence) -> None:
    stats = persistence.snapshots.aggregate(0.0, 1.0, threshold_c=80.0, bucket_s=60)
    assert stats["samples"] == 0
    assert stats["cpu_max"] is None
    assert stats["time_above_s"] == 0.0
