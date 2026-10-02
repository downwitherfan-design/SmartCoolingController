#!/usr/bin/env python3
"""Compile the Windows installer from a frozen bundle (spec §261).

Locates Inno Setup 6 (``ISCC.exe``), feeds it the version and build identifier,
compiles ``installer/smartcooling.iss`` and reports the resulting artefact with
its SHA-256 so it can be published verbatim.

Usage::

    python scripts/installer/build_installer.py --release
    python scripts/installer/build_installer.py --iscc "C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ROOT, build_info, is_windows, run, write_json

BUNDLE = ROOT / "build" / "dist" / "SmartCoolingController"
ISS = ROOT / "installer" / "smartcooling.iss"
OUTPUT_DIR = ROOT / "build" / "installer"

#: Common install locations for Inno Setup 6.
_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)


def _candidate_paths() -> list[Path]:
    """Common install locations for Inno Setup 6, including a per-user install."""
    candidates = [Path(p) for p in _CANDIDATES]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.append(Path(program_files) / "Inno Setup 6" / "ISCC.exe")
    return candidates


def find_iscc(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"ISCC not found at {path}")
        return path
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    for candidate in _candidate_paths():
        if candidate.exists():
            return candidate
    raise SystemExit(
        "Inno Setup 6 (ISCC.exe) was not found.  Install it from "
        "https://jrsoftware.org/isdl.php or pass --iscc <path>."
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Windows installer.")
    parser.add_argument("--release", action="store_true", help="tagged release build id")
    parser.add_argument("--iscc", default=None, help="path to ISCC.exe")
    parser.add_argument("--bundle", type=Path, default=BUNDLE, help="frozen bundle directory")
    args = parser.parse_args(argv)

    if not is_windows():
        print("WARNING: Inno Setup only runs on Windows; continuing anyway.", file=sys.stderr)

    bundle = args.bundle.resolve()
    if not bundle.is_dir():
        raise SystemExit(
            f"frozen bundle not found at {bundle}.  Run scripts/build/build.py first."
        )

    info = build_info(release=args.release)
    iscc = find_iscc(args.iscc)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"compiling installer for {info.version} (build {info.build_id})")
    run(
        [
            str(iscc),
            str(ISS),
            f"/DAppVersion={info.version}",
            f"/DBuildId={info.build_id}",
            f"/DSourceDir={bundle}",
            f"/DOutputDir={OUTPUT_DIR}",
            "/Qp",  # progress only; keep the log for CI
        ],
        env={**os.environ},
    )

    artefacts = sorted(OUTPUT_DIR.glob("SmartCoolingController-*-setup.exe"))
    if not artefacts:
        raise SystemExit(f"ISCC reported success but no installer appeared in {OUTPUT_DIR}")
    installer = artefacts[-1]
    digest = sha256(installer)

    manifest = write_json(
        OUTPUT_DIR / "installer-manifest.json",
        {
            "version": info.version,
            "build_id": info.build_id,
            "protocol_version": info.protocol_version,
            "git_sha": info.git_sha,
            "dirty": info.dirty,
            "installer": installer.name,
            "size_bytes": installer.stat().st_size,
            "sha256": digest,
        },
    )
    print(f"installer: {installer}")
    print(f"sha256:    {digest}")
    print(f"manifest:  {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
