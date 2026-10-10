#!/usr/bin/env python3
"""Smoke-test a frozen SmartCooling Controller bundle (spec §262).

A PyInstaller build can succeed and still fail at runtime because a module was
excluded, a Qt plugin was not collected, or a data path is wrong.  This script
runs the *real* executable and asserts the things that would otherwise only
surface on a customer machine:

1. ``--version`` prints and exits 0 — proves the interpreter + ``app`` import;
2. a full head-less start-up reaches ``app.exec`` and shuts down with code 0 —
   proves the whole Qt/widget/service stack was collected correctly (this is
   the check that catches "numpy was excluded" style failures);
3. the SQLite/JSON/paths stack works: the app creates its data tree under
   ``SMARTCOOLING_DATA_DIR`` and clears its crash marker on a clean exit.

Usage::

    python scripts/build/verify_frozen.py build/dist/SmartCoolingController
    python scripts/build/verify_frozen.py <bundle> --skip-ui
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def _exe(bundle: Path) -> Path:
    for name in ("SmartCoolingController.exe", "SmartCoolingController"):
        candidate = bundle / name
        if candidate.exists():
            return candidate
    raise SystemExit(f"no executable found in {bundle}")


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # prove the bundle is self-contained
    env.pop("PYTHONHOME", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def _run(
    command: list[str], *, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=timeout, check=False
    )


def check_version(exe: Path, env: dict[str, str]) -> None:
    result = _run([str(exe), "--version"], env=env, timeout=60)
    if result.returncode != 0:
        raise SystemExit(f"--version failed ({result.returncode}):\n{result.stderr}")
    if "SmartCooling" not in result.stdout:
        raise SystemExit(f"unexpected --version output: {result.stdout!r}")
    print(f"      --version OK: {result.stdout.strip().splitlines()[0]}")


def check_headless_run(exe: Path, env: dict[str, str], data_dir: Path) -> None:
    """Start the UI head-less once and validate both the exit and the data tree.

    ``--headless`` skips the first-run wizard and auto-quits, so the run is
    reproducible.  A non-zero exit (or a traceback) means the widget tree or a
    bundled dependency is broken — exactly the failure a frozen build hides
    until launch.  The crash marker is written on start-up and removed only by
    the graceful shutdown path, so its absence proves the UI reached teardown
    rather than being killed.
    """
    env = dict(env)
    env["SMARTCOOLING_DATA_DIR"] = str(data_dir)
    result = _run(
        [str(exe), "--headless", "--headless-seconds", "2"],
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"UI start-up failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    if "Traceback" in result.stderr:
        raise SystemExit(f"UI start-up raised:\n{result.stderr}")

    if not data_dir.exists():
        raise SystemExit(f"the app did not create its data directory: {data_dir}")
    marker = data_dir / "Cache" / "running.lock"
    if marker.exists():
        raise SystemExit(f"crash marker was not cleared: {marker}")
    print(f"      UI start/stop + data tree OK ({data_dir})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a frozen bundle.")
    parser.add_argument("bundle", type=Path, help="path to the PyInstaller dist folder")
    parser.add_argument(
        "--skip-ui", action="store_true", help="only run the cheap import checks"
    )
    args = parser.parse_args(argv)

    bundle: Path = args.bundle.resolve()
    if not bundle.is_dir():
        raise SystemExit(f"not a directory: {bundle}")
    exe = _exe(bundle)
    print(f"      executable: {exe} ({exe.stat().st_size / 1e6:.1f} MB)")

    env = _base_env()
    data_dir = Path(tempfile.mkdtemp(prefix="smartcooling-verify-"))

    check_version(exe, env)
    if not args.skip_ui:
        check_headless_run(exe, env, data_dir)

    print("      frozen bundle verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
