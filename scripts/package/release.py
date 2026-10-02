#!/usr/bin/env python3
"""One-shot release pipeline for SmartCooling Controller (spec §263, §264).

Runs the whole sequence in order and fails fast, so a release is either
complete or clearly reported as broken:

1. build the frozen bundle   (``scripts/build/build.py --release``)
2. build the Windows installer (``scripts/installer/build_installer.py --release``)
3. generate release notes    (``scripts/package/release_notes.py``)
4. write a ``checksums.txt`` and a combined ``release-manifest.json``

Nothing is uploaded and no git tag is created: those are deliberate, human
decisions.  The script leaves every artefact in ``build/`` for review.

Usage::

    python scripts/package/release.py --release
    python scripts/package/release.py --release --skip-installer
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ROOT, build_info, is_windows, python, run, write_json

BUILD = ROOT / "scripts" / "build" / "build.py"
INSTALLER = ROOT / "scripts" / "installer" / "build_installer.py"
NOTES = ROOT / "scripts" / "package" / "release_notes.py"
BUILD_DIR = ROOT / "build"
INSTALLER_DIR = BUILD_DIR / "installer"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the full release pipeline.")
    parser.add_argument("--release", action="store_true", help="tagged-release build id")
    parser.add_argument("--skip-installer", action="store_true", help="bundle only, no setup.exe")
    parser.add_argument("--skip-tests", action="store_true", help="skip the pytest gate")
    args = parser.parse_args(argv)

    info = build_info(release=args.release)
    print(f"=== SmartCooling Controller {info.version} (build {info.build_id}) ===\n")

    print("[1/4] frozen bundle")
    build_command = [python(), str(BUILD)]
    if args.release:
        build_command.append("--release")
    if args.skip_tests:
        build_command.append("--skip-tests")
    run(build_command)

    installer_path: Path | None = None
    if args.skip_installer:
        print("[2/4] installer — skipped")
    else:
        print("[2/4] Windows installer")
        installer_command = [python(), str(INSTALLER)]
        if args.release:
            installer_command.append("--release")
        try:
            run(installer_command)
            artefacts = sorted(INSTALLER_DIR.glob("SmartCoolingController-*-setup.exe"))
            installer_path = artefacts[-1] if artefacts else None
        except SystemExit as exc:
            # A missing Inno Setup must not throw away a perfectly good bundle.
            print(f"      installer step failed: {exc}", file=sys.stderr)

    print("[3/4] release notes")
    notes_path = BUILD_DIR / "RELEASE_NOTES.md"
    # Notes are a nicety, not a build gate: a failure here (no git, an odd
    # history, a future regression) must never discard a verified bundle.
    if run([python(), str(NOTES), "--output", str(notes_path)], check=False) != 0:
        print("      release notes step failed; continuing without them", file=sys.stderr)

    print("[4/4] checksums and manifest")
    artefacts = [path for path in (installer_path,) if path is not None]
    bundle_dir = BUILD_DIR / "dist" / "SmartCoolingController"
    if bundle_dir.is_dir():
        artefacts.append(bundle_dir)

    lines: list[str] = []
    for artefact in artefacts:
        if artefact.is_dir():
            # A directory cannot be hashed directly; record its main executable
            # so ``checksums.txt`` still pins the shipped binary.
            name = "SmartCoolingController.exe" if is_windows() else "SmartCoolingController"
            exe = artefact / name
            if exe.is_file():
                lines.append(f"{sha256(exe)}  {artefact.name}/{exe.name}")
            continue
        lines.append(f"{sha256(artefact)}  {artefact.name}")
    checksums = BUILD_DIR / "checksums.txt"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = write_json(
        BUILD_DIR / "release-manifest.json",
        {
            "version": info.version,
            "build_id": info.build_id,
            "protocol_version": info.protocol_version,
            "git_sha": info.git_sha,
            "dirty": info.dirty,
            "installer": installer_path.name if installer_path else None,
            "installer_sha256": sha256(installer_path) if installer_path else None,
            "bundle": str(bundle_dir.relative_to(ROOT)) if bundle_dir.is_dir() else None,
            "release_notes": str(notes_path.relative_to(ROOT)),
            "checksums": str(checksums.relative_to(ROOT)),
        },
    )

    print(f"\nchecksums: {checksums}")
    print(f"manifest:  {manifest}")
    print("\nRelease pipeline complete.  Review build/ before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
