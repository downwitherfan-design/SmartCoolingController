"""Health checks and diagnostics (spec §64, §88, §89, §106, §107, §204).

Health is reported *per subsystem* and never collapsed into a single score
(spec §88): a healthy CPU says nothing about the database.  Each check is
independent and cheap, so the panel can refresh without stalling the UI.

The diagnostics service gathers a support bundle: environment, hardware
inventory, recent logs and recent alerts — with secrets redacted (spec §204).
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import APP_VERSION, BUILD_ID, PROTOCOL_VERSION
from app.core.clock import to_iso, utc_now
from app.core.enums import HealthStatus
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.models.alerts import HealthReport

_log = get_logger(__name__)

TOPIC_HEALTH = "health.updated"

#: Substrings that mark a key as sensitive in a support bundle.
_REDACT_HINTS = ("password", "secret", "token", "key", "credential", "passphrase")


@dataclass(slots=True)
class HealthProbe:
    """A callable that returns one subsystem's status and a detail string."""

    name: str
    status: HealthStatus
    detail: str = ""


class HealthService:
    """Runs independent health checks across the application."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._probes: dict[str, Any] = {}
        self._last: HealthReport | None = None

    def register(self, name: str, probe) -> None:
        """Register ``probe() -> (HealthStatus, detail)`` under ``name``."""
        self._probes[name] = probe

    @property
    def last_report(self) -> HealthReport | None:
        return self._last

    def check(self) -> HealthReport:
        report = HealthReport()
        for name, probe in self._probes.items():
            try:
                status, detail = probe()
            except Exception as exc:  # noqa: BLE001
                status, detail = HealthStatus.ERROR, f"probe failed: {exc}"
            if hasattr(report, name):
                setattr(report, name, status)
            report.detail[name] = detail
        report.detail["version"] = f"{APP_VERSION}+{BUILD_ID}"
        report.detail["python"] = platform.python_version()
        self._last = report
        self._bus.publish(TOPIC_HEALTH, report)
        return report


class DiagnosticsService:
    """Collects and redacts diagnostic information for support."""

    def __init__(self, *, paths, persistence, registry, connection=None) -> None:
        self._paths = paths
        self._persistence = persistence
        self._registry = registry
        self._connection = connection

    def environment(self) -> dict[str, Any]:
        return {
            "app_version": APP_VERSION,
            "build_id": BUILD_ID,
            "protocol_version": PROTOCOL_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "executable": sys.executable,
            "frozen": getattr(sys, "frozen", False),
            "data_mode": self._paths.mode.value,
            "data_root": str(self._paths.root),
            "log_dir": str(self._paths.logs),
            "database": str(self._paths.database),
        }

    def hardware(self) -> dict[str, Any]:
        try:
            profile = self._registry.profile
            return {
                "profile": profile.to_dict() if hasattr(profile, "to_dict") else {},
                "backends": [health.to_dict() for health in self._registry.health()],
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def database(self) -> dict[str, Any]:
        db = self._persistence.database
        return {
            "path": str(db.path),
            "size_bytes": db.size_bytes(),
            "integrity_ok": self._persistence.integrity_check(),
            "snapshots": self._persistence.snapshots.count(),
            "schema_version": db.get_meta("schema_version", "?"),
        }

    def recent_diagnostics(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._persistence.diagnostics.recent(limit)

    def support_bundle(self) -> dict[str, Any]:
        bundle = {
            "generated_at": to_iso(utc_now()),
            "environment": self.environment(),
            "hardware": self.hardware(),
            "database": self.database(),
            "recent_alerts": [
                alert.to_dict() for alert in self._persistence.alerts.list_recent(50)
            ],
            "recent_diagnostics": self.recent_diagnostics(200),
        }
        if self._connection is not None:
            try:
                bundle["connection"] = self._connection.diagnostics_snapshot()
            except Exception as exc:  # noqa: BLE001
                bundle["connection"] = {"error": str(exc)}
        return redact(bundle)


def redact(value: Any) -> Any:
    """Recursively redact values whose key looks like a secret."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(hint in str(key).lower() for hint in _REDACT_HINTS):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


__all__ = ["HealthService", "DiagnosticsService", "HealthProbe", "redact", "TOPIC_HEALTH"]
