"""Shared helpers for the SmartCooling Controller build scripts.

Importing this module is cheap and side-effect free.  It is used by every
script under ``scripts/`` so that versioning, the build identifier and the
"where do artefacts go" rules are defined in exactly one place.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: Repository root (this file lives in ``<root>/scripts/``).
ROOT = Path(__file__).resolve().parents[1]

#: The application package, imported lazily to keep this module light.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(slots=True)
class BuildInfo:
    """Everything the build pipeline needs to stamp a release."""

    version: str
    protocol_version: int
    build_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    git_sha: str = ""
    dirty: bool = False

    @property
    def full_version(self) -> str:
        """``1.0.0+build.20260101T000000Z`` — PEP 440 local version."""
        return f"{self.version}+build.{self.timestamp}"


def app_metadata() -> tuple[str, int]:
    """Return ``(APP_VERSION, PROTOCOL_VERSION)`` from the application."""
    from app import APP_VERSION, PROTOCOL_VERSION

    return APP_VERSION, PROTOCOL_VERSION


def git(*args: str, timeout: float = 10.0) -> str:
    """Run ``git`` and return its stdout, or ``""`` when git is missing.

    This is the single place in the codebase allowed to invoke git.  A source
    archive is unpacked on machines that have no git at all, where
    ``subprocess`` raises ``FileNotFoundError`` (an ``OSError``); that must
    never be fatal, so a missing binary is reported as "no output".
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def git_available() -> bool:
    """True when a working ``git`` executable is on ``PATH``."""
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


#: Common install locations for Inno Setup 6.
_INNO_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)


def find_iscc(explicit: str | None = None) -> Path | None:
    """Locate ``ISCC.exe``, or return ``None`` when Inno Setup is not installed.

    Inno Setup is optional: without it the pipeline still produces a verified
    portable bundle, it just cannot compile the ``setup.exe``.  Keeping the
    search here means the launcher and the installer builder agree on exactly
    where to look, including a per-user installation.
    """
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    candidates = [Path(p) for p in _INNO_CANDIDATES]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(Path(program_files) / "Inno Setup 6" / "ISCC.exe")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def git_state() -> tuple[str, bool]:
    """Return ``(short_sha, is_dirty)``; empty/False outside a git checkout."""
    sha = git("rev-parse", "--short", "HEAD")
    if not sha:
        return "", False
    dirty = bool(git("status", "--porcelain"))
    return sha, dirty


def build_info(*, release: bool = False) -> BuildInfo:
    """Assemble a :class:`BuildInfo` for the current tree.

    ``release=True`` produces a stable, timestamp-free identifier suitable for a
    tagged release; otherwise a timestamped identifier is used so that local
    builds are always distinguishable.
    """
    version, protocol = app_metadata()
    sha, dirty = git_state()
    if release and sha:
        build_id = sha
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        build_id = f"{sha or 'nogit'}-{stamp}"
        if dirty:
            build_id += "-dirty"
    return BuildInfo(
        version=version,
        protocol_version=protocol,
        build_id=build_id,
        git_sha=sha,
        dirty=dirty,
    )


def stamp_build(info: BuildInfo) -> Path:
    """Write the build identifier into ``app/_build.py``.

    The generated module is imported by ``app.__init__`` at runtime.  It is
    regenerated on every build so a shipped binary always reports the commit it
    was produced from (spec §240, §260).
    """
    target = ROOT / "app" / "_build.py"
    content = (
        '"""Generated by scripts/build/build.py — do not edit by hand."""\n\n'
        "from __future__ import annotations\n\n"
        f"BUILD_ID = {info.build_id!r}\n"
        f"BUILD_TIMESTAMP = {info.timestamp!r}\n"
        f"GIT_SHA = {info.git_sha!r}\n"
        f"DIRTY = {info.dirty!r}\n"
    )
    target.write_text(content, encoding="utf-8")
    return target


def clean(path: Path) -> None:
    """Remove a directory tree if it exists."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> int:
    """Run a subprocess, streaming output; return its exit code."""
    printable = " ".join(command)
    print(f"  $ {printable}", flush=True)
    result = subprocess.run(command, cwd=str(cwd or ROOT), check=False, env=env)
    if check and result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {printable}")
    return result.returncode


def python() -> str:
    """The interpreter to use for child processes."""
    return os.environ.get("SMARTCOOLING_PYTHON", sys.executable)


def is_windows() -> bool:
    return sys.platform == "win32"


__all__ = [
    "ROOT",
    "BuildInfo",
    "app_metadata",
    "build_info",
    "clean",
    "find_iscc",
    "git",
    "git_available",
    "git_state",
    "is_windows",
    "python",
    "run",
    "stamp_build",
    "write_json",
]
