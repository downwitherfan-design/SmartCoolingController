#!/usr/bin/env python3
"""Assemble the SmartCooling Controller delivery package (spec §263).

The release pipeline (``scripts/package/release.py``) produces a verified
frozen bundle plus its metadata, but a delivery is more than a binary: a user
has to receive the executable, the source it was built from, the checksums to
verify both, and a document that says how to run it.

This script gathers exactly that under ``build/delivery/``:

- ``SmartCoolingController-<version>-linux-x86_64.zip``  frozen runtime bundle
- ``SmartCoolingController-<version>-source.zip``        full, buildable source
- ``INSTALL.md``                                          end-user run guide
- ``RELEASE_NOTES.md`` / ``manifest.json`` / ``checksums.txt``  build metadata
- ``checksums.sha256``                                    hashes of every artefact

Nothing is uploaded: the directory is a self-contained hand-off.  Re-running is
idempotent — the output directory is rebuilt from scratch each time.

Usage::

    python scripts/package/deliver.py --release
    python scripts/package/deliver.py            # local, timestamped build id
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ROOT, build_info, clean, write_json

BUILD = ROOT / "build"
BUNDLE = BUILD / "dist" / "SmartCoolingController"
DELIVERY = BUILD / "delivery"

#: Directories excluded only at the archive root: build output, VCS metadata
#: and virtual environments.  They must not match nested source packages such
#: as ``scripts/build`` — the earlier "match anywhere" rule silently dropped
#: the build scripts from the source archive.
_ROOT_EXCLUDES = {"build", ".git", "dist", ".venv", "venv"}
#: Cache directories excluded wherever they appear.
_ANY_EXCLUDES = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
_SOURCE_EXCLUDE_SUFFIXES = (".egg-info", ".pyc", ".pyo")

#: Individual generated files that must not enter the source archive even
#: though they live inside a shipped package (the build re-creates them).
_SOURCE_EXCLUDE_FILES = {"_build.py"}

#: Metadata files copied verbatim from ``build/`` when they exist.
_METADATA = ("manifest.json", "checksums.txt", "RELEASE_NOTES.md")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(relative: Path) -> bool:
    if relative.parts and relative.parts[0] in _ROOT_EXCLUDES:
        return True
    if any(part in _ANY_EXCLUDES for part in relative.parts):
        return True
    if relative.name in _SOURCE_EXCLUDE_FILES:
        return True
    return any(part.endswith(_SOURCE_EXCLUDE_SUFFIXES) for part in relative.parts)


def _zip_tree(source: Path, target: Path, *, arc_prefix: str = "") -> int:
    """Zip ``source`` into ``target``, skipping ignored paths.  Returns count."""
    written = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if _excluded(relative):
                continue
            arcname = f"{arc_prefix}/{relative}" if arc_prefix else str(relative)
            archive.write(path, arcname)
            written += 1
    return written


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _platform_tag() -> str:
    system = {"linux": "linux", "win32": "windows", "darwin": "macos"}.get(
        sys.platform, sys.platform
    )
    machine = platform.machine().lower() or "unknown"
    if machine in {"amd64", "x86_64"}:
        machine = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        machine = "arm64"
    return f"{system}-{machine}"


def _install_guide(version: str, build_id: str, bundle_zip: str) -> str:
    return f"""# SmartCooling Controller — installation and first run

Version **{version}** · build `{build_id}`

## What you received

| File | Purpose |
| --- | --- |
| `{bundle_zip}` | The application itself — unzip and run, no Python required. |
| `SmartCoolingController-{version}-source.zip` | Full source tree, buildable with the pinned toolchain. |
| `RELEASE_NOTES.md` | What changed in this build. |
| `manifest.json` | Exact versions, sizes and hashes recorded at build time. |
| `checksums.txt`, `checksums.sha256` | Integrity hashes for every artefact. |

## Verify before you run

Integrity first — confirm the archive you downloaded matches the recorded hash:

```sh
sha256sum --check checksums.sha256
```

Every line must report `OK`. A mismatch means the download is corrupt or was
tampered with; do not run it.

## Run the application

1. Unzip `{bundle_zip}`.
2. Launch the executable inside the extracted folder:
   - Linux: `./SmartCoolingController`
   - Windows: `SmartCoolingController.exe`
3. On first launch the setup wizard opens and offers a simulated backend so you
   can explore the interface without any hardware attached.

Nothing is installed system-wide and no administrator rights are needed — the
bundle is self-contained.

### Command-line options

```sh
./SmartCoolingController --version     # print version, protocol and build id
./SmartCoolingController --headless    # run without a window (diagnostics)
./SmartCoolingController --safe-mode   # start with hardware polling disabled
```

Application data (settings, history, logs) lives under the per-user data
directory; pass `SMARTCOOLING_DATA_DIR` to relocate it, which is also how the
tests keep their runs isolated.

## Building from source

The source archive pins every dependency, so the build is reproducible.

### Windows (one click)

Double-click **`build_windows.bat`**. It locates a supported Python, creates
`.venv`, installs the pinned dependencies and runs the full build
(lint → tests → freeze → verify). The executable lands in
`build\\dist\\SmartCoolingController\\SmartCoolingController.exe`.

> Requires 64-bit **Python 3.11–3.13**. Python 3.14 is not supported yet — the
> pinned PySide6 6.8.1 publishes no wheels for it.

### Manual / Linux

```sh
python -m pip install -r requirements-build.txt
python scripts/build/build.py           # quality gate → tests → freeze → verify
```

On a head-less Linux machine install the graphics libraries PySide6 needs:

```sh
sudo apt-get install -y --no-install-recommends libegl1 libgl1
```

> The frozen bundle is platform-specific: a build made on Linux runs on Linux,
> a build made on Windows runs on Windows. PyInstaller cannot cross-compile, so
> build on the platform you intend to ship.

## Support

Logs and exported support bundles pass through the redaction filter, so they
never contain tokens, keys or pairing codes and are safe to share.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble the delivery package.")
    parser.add_argument("--release", action="store_true", help="tagged-release build id")
    args = parser.parse_args(argv)

    info = build_info(release=args.release)
    tag = _platform_tag()
    print(f"=== assembling delivery for {info.version} (build {info.build_id}) ===\n")

    if not BUNDLE.is_dir():
        raise SystemExit(
            f"frozen bundle not found at {BUNDLE}; run scripts/build/build.py first"
        )

    clean(DELIVERY)
    DELIVERY.mkdir(parents=True, exist_ok=True)

    artefacts: list[Path] = []

    print("[1/4] frozen runtime bundle")
    bundle_zip = DELIVERY / f"SmartCoolingController-{info.version}-{tag}.zip"
    count = _zip_tree(BUNDLE, bundle_zip, arc_prefix="SmartCoolingController")
    artefacts.append(bundle_zip)
    print(f"      {bundle_zip.name}  ({count} files, {_human(bundle_zip.stat().st_size)})")

    print("[2/4] source tree")
    source_zip = DELIVERY / f"SmartCoolingController-{info.version}-source.zip"
    root_name = f"SmartCoolingController-{info.version}"
    count = _zip_tree(ROOT, source_zip, arc_prefix=root_name)
    artefacts.append(source_zip)
    print(f"      {source_zip.name}  ({count} files, {_human(source_zip.stat().st_size)})")

    print("[3/4] metadata and install guide")
    for name in _METADATA:
        origin = BUILD / name
        if origin.is_file() and origin.stat().st_size > 0:
            shutil.copy2(origin, DELIVERY / name)
            print(f"      {name}")
    guide = DELIVERY / "INSTALL.md"
    guide.write_text(_install_guide(info.version, info.build_id, bundle_zip.name), encoding="utf-8")
    print(f"      {guide.name}")

    print("[4/4] checksums")
    artefacts.extend(sorted(DELIVERY.glob("*.md")))
    artefacts.extend(sorted(DELIVERY.glob("*.json")))
    artefacts.extend(sorted(DELIVERY.glob("checksums.txt")))
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in artefacts:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    lines = [f"{sha256(path)}  {path.name}" for path in ordered]
    checksums = DELIVERY / "checksums.sha256"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"      {checksums.name}  ({len(lines)} entries)")

    write_json(
        DELIVERY / "delivery-manifest.json",
        {
            "version": info.version,
            "build_id": info.build_id,
            "protocol_version": info.protocol_version,
            "platform": tag,
            "python": sys.version.split()[0],
            "artefacts": [
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
                for path in sorted(DELIVERY.iterdir())
                if path.is_file() and path.name != "delivery-manifest.json"
            ],
        },
    )

    total = sum(path.stat().st_size for path in DELIVERY.iterdir() if path.is_file())
    print(f"\ndelivery: {DELIVERY}  ({_human(total)})")
    print("Delivery package complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
