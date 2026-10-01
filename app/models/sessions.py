"""Cooling session and baseline models (spec §36, §37, §38).

A session is a user-started observation window.  Its statistics are computed
from persisted telemetry and are labelled as derived where appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.clock import to_iso, utc_now
from app.core.enums import DataProvenance, SessionState


@dataclass(slots=True)
class CoolingSession:
    """Aggregated statistics for one cooling session."""

    session_id: str | None = None
    name: str = ""
    state: SessionState = SessionState.RUNNING
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    profile_key: str = ""
    simulated: bool = False

    max_cpu_c: float | None = None
    max_gpu_c: float | None = None
    avg_cpu_c: float | None = None
    avg_gpu_c: float | None = None
    peak_rpm: float | None = None
    avg_rpm: float | None = None
    max_fan_power_w: float | None = None
    time_above_threshold_s: float = 0.0
    esp32_connected: bool = False
    alert_count: int = 0
    sample_count: int = 0
    notes: str = ""
    provenance: DataProvenance = DataProvenance.DERIVED

    @property
    def duration_s(self) -> float:
        end = self.ended_at or utc_now()
        return max(0.0, (end - self.started_at).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "name": self.name,
            "state": self.state.value,
            "started_at": to_iso(self.started_at),
            "ended_at": to_iso(self.ended_at) if self.ended_at else None,
            "duration_s": self.duration_s,
            "profile_key": self.profile_key,
            "simulated": self.simulated,
            "max_cpu_c": self.max_cpu_c,
            "max_gpu_c": self.max_gpu_c,
            "avg_cpu_c": self.avg_cpu_c,
            "avg_gpu_c": self.avg_gpu_c,
            "peak_rpm": self.peak_rpm,
            "avg_rpm": self.avg_rpm,
            "max_fan_power_w": self.max_fan_power_w,
            "time_above_threshold_s": self.time_above_threshold_s,
            "esp32_connected": self.esp32_connected,
            "alert_count": self.alert_count,
            "sample_count": self.sample_count,
            "notes": self.notes,
            "provenance": self.provenance.value,
        }


@dataclass(slots=True)
class SessionComparison:
    """Side-by-side comparison of two sessions (spec §37)."""

    left: CoolingSession
    right: CoolingSession

    def deltas(self) -> dict[str, float | None]:
        def delta(a: float | None, b: float | None) -> float | None:
            if a is None or b is None:
                return None
            return b - a

        return {
            "max_cpu_c": delta(self.left.max_cpu_c, self.right.max_cpu_c),
            "max_gpu_c": delta(self.left.max_gpu_c, self.right.max_gpu_c),
            "avg_cpu_c": delta(self.left.avg_cpu_c, self.right.avg_cpu_c),
            "avg_gpu_c": delta(self.left.avg_gpu_c, self.right.avg_gpu_c),
            "peak_rpm": delta(self.left.peak_rpm, self.right.peak_rpm),
            "avg_rpm": delta(self.left.avg_rpm, self.right.avg_rpm),
            "max_fan_power_w": delta(
                self.left.max_fan_power_w, self.right.max_fan_power_w
            ),
        }


@dataclass(slots=True)
class Baseline:
    """A named historical reference (spec §38)."""

    key: str
    name: str
    avg_cpu_c: float | None = None
    avg_gpu_c: float | None = None
    avg_fan_rpm: float | None = None
    sample_count: int = 0
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "avg_cpu_c": self.avg_cpu_c,
            "avg_gpu_c": self.avg_gpu_c,
            "avg_fan_rpm": self.avg_fan_rpm,
            "sample_count": self.sample_count,
            "created_at": to_iso(self.created_at),
        }


__all__ = ["Baseline", "CoolingSession", "SessionComparison"]
