"""Filesystem layout and data-mode resolution.

Two data modes are supported (spec §66, §110, §124):

``Standard``
    Data lives under ``%LOCALAPPDATA%\\SmartCoolingController`` on Windows
    (``~/.local/share/SmartCoolingController`` elsewhere).  Application
    binaries are installed elsewhere, so upgrades never touch user data.

``Portable``
    Data lives in a ``Data`` folder next to the executable, so the whole
    product can live on a USB stick.

The mode is chosen by, in order of precedence:

1. an explicit override passed by the caller / CLI,
2. the ``SMARTCOOLING_DATA_MODE`` environment variable,
3. a ``portable.flag`` marker beside the executable,
4. otherwise Standard.

If Portable mode is selected but the directory is not writable (read-only
USB stick, restricted share) we transparently fall back to Standard and record
the reason, instead of crashing (spec §242).
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.enums import DataMode

#: Marker file that forces Portable mode when present beside the executable.
PORTABLE_FLAG = "portable.flag"

_ENV_OVERRIDE = "SMARTCOOLING_DATA_MODE"
_ENV_DATA_DIR = "SMARTCOOLING_DATA_DIR"


def is_frozen() -> bool:
    """Return ``True`` when running from a PyInstaller/Nuitka bundle."""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """Directory that contains the running application.

    For a frozen build this is the folder of ``sys.executable``; for a source
    checkout it is the project root (two levels above this file).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Root of the source tree (used for resources and tooling)."""
    return Path(__file__).resolve().parents[2]


def _default_standard_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "SmartCoolingController"
        return Path.home() / "AppData" / "Local" / "SmartCoolingController"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SmartCoolingController"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SmartCoolingController"


def _is_writable(path: Path) -> bool:
    """Return whether ``path`` can be created and written to."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write_probe_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved directory layout for a running instance."""

    mode: DataMode
    root: Path
    database: Path
    logs: Path
    backups: Path
    exports: Path
    cache: Path
    config: Path
    #: Non-empty when the requested mode could not be honoured.
    fallback_reason: str = ""

    @classmethod
    def resolve(
        cls,
        *,
        mode: DataMode | None = None,
        base_dir: Path | None = None,
    ) -> AppPaths:
        """Resolve the layout, applying precedence and safe fallback."""
        requested, reason = cls._choose_mode(mode)
        base = base_dir or executable_dir()

        # An explicit data directory (used by administrators, portable launchers
        # and the test-suite) overrides the standard per-user location.
        override = os.environ.get(_ENV_DATA_DIR, "").strip()
        if override:
            root = Path(override).expanduser()
            if _is_writable(root):
                return cls._build(requested, root, reason)
            reason = (
                f"SMARTCOOLING_DATA_DIR '{root}' is not writable; "
                "falling back to the default location."
            )

        if requested is DataMode.PORTABLE:
            portable_root = base / "Data"
            if _is_writable(portable_root):
                return cls._build(DataMode.PORTABLE, portable_root, "")
            reason = (
                f"Portable data directory '{portable_root}' is not writable; "
                "falling back to Standard user data mode."
            )
            requested = DataMode.STANDARD

        root = _default_standard_root()
        if not _is_writable(root):
            # Last-resort: a temp directory keeps the app alive and usable.
            root = Path(tempfile.gettempdir()) / "SmartCoolingController"
            reason = (
                f"Standard data directory is not writable; using temporary "
                f"location '{root}'."
            )
        return cls._build(requested, root, reason)

    @staticmethod
    def _choose_mode(explicit: DataMode | None) -> tuple[DataMode, str]:
        if explicit is not None:
            return explicit, ""
        env = os.environ.get(_ENV_OVERRIDE, "").strip().lower()
        if env in {DataMode.PORTABLE.value, "portable", "1", "true"}:
            return DataMode.PORTABLE, ""
        if env in {DataMode.STANDARD.value, "standard", "0", "false"}:
            return DataMode.STANDARD, ""
        if (executable_dir() / PORTABLE_FLAG).exists():
            return DataMode.PORTABLE, ""
        return DataMode.STANDARD, ""

    @classmethod
    def _build(cls, mode: DataMode, root: Path, reason: str) -> AppPaths:
        return cls(
            mode=mode,
            root=root,
            database=root / "Database",
            logs=root / "Logs",
            backups=root / "Backups",
            exports=root / "Exports",
            cache=root / "Cache",
            config=root / "Config",
            fallback_reason=reason,
        )

    @property
    def database_file(self) -> Path:
        return self.database / "SmartCooling.db"

    def ensure(self) -> None:
        """Create every directory in the layout."""
        for directory in (
            self.root,
            self.database,
            self.logs,
            self.backups,
            self.exports,
            self.cache,
            self.config,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode.value,
            "root": str(self.root),
            "database": str(self.database_file),
            "logs": str(self.logs),
            "backups": str(self.backups),
            "exports": str(self.exports),
            "cache": str(self.cache),
            "config": str(self.config),
            "fallback_reason": self.fallback_reason,
        }


__all__ = [
    "PORTABLE_FLAG",
    "AppPaths",
    "executable_dir",
    "is_frozen",
    "project_root",
]
