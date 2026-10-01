"""Time helpers.

Everything persisted is stored in UTC (spec §286); only the presentation layer
converts to local time.  Keeping this in one module makes that invariant easy
to audit.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def utc_timestamp() -> float:
    """Current UTC time as POSIX seconds."""
    return utc_now().timestamp()


def to_iso(value: datetime) -> str:
    """Serialise a datetime as an ISO-8601 string in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 string into an aware UTC datetime."""
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def monotonic_ms() -> float:
    """Monotonic milliseconds, for measuring durations only."""
    import time

    return time.monotonic() * 1000.0


def local_now() -> datetime:
    """Current local time, for display only."""
    return datetime.now().astimezone()


def format_local(value: datetime, *, with_seconds: bool = True) -> str:
    """Format a UTC datetime for local display."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = value.astimezone()
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    return local.strftime(fmt)


def format_duration(seconds: float) -> str:
    """Human readable duration, e.g. ``1h 04m 12s``."""
    seconds = max(0.0, float(seconds))
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


__all__ = [
    "format_duration",
    "format_local",
    "local_now",
    "monotonic_ms",
    "parse_iso",
    "to_iso",
    "utc_now",
    "utc_timestamp",
]
