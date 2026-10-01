"""The real entry point must construct a full window without crashing.

Regression guard: ``run.main`` used to call
``QGuiApplication.HighDpiScaleFactorRoundingPolicy`` — an enum that actually
lives on ``Qt`` — so *every* launch died with an ``AttributeError`` before the
window appeared.  Nothing caught it because the UI tests import
``app.ui.shell`` directly and never exercised ``run.main``.

These tests launch the real process, which is the only way to prove the whole
start-up path (Qt bootstrap → single-instance guard → services → window →
graceful shutdown) works end to end.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "SMARTCOOLING_DATA_DIR": str(tmp_path / "data"),
        "SMARTCOOLING_DATA_MODE": "standard",
        "PYTHONPATH": str(ROOT),
    }


def _popen_kwargs() -> dict[str, object]:
    """Extra ``Popen`` flags needed to deliver a console signal on Windows.

    A process started with ``CREATE_NEW_PROCESS_GROUP`` can receive
    ``CTRL_BREAK_EVENT``; without the flag Windows delivers no console signal at
    all and the only way to stop the child is an ungraceful ``TerminateProcess``.
    """
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def _stop_gracefully(process: subprocess.Popen) -> None:
    """Send the platform's *interrupt* signal, not an ungraceful kill.

    On POSIX that is ``SIGTERM``.  On Windows ``send_signal(SIGTERM)`` calls
    ``TerminateProcess`` — the process dies instantly with exit code 1 and no
    teardown runs — so the honest equivalent is ``CTRL_BREAK_EVENT``, which the
    entry point handles via ``signal.SIGBREAK``.
    """
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGTERM)


def test_version_flag_prints_and_exits_cleanly(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "run", "--version"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SmartCooling Controller" in result.stdout
    assert "protocol v" in result.stdout


def test_headless_startup_builds_the_window_and_exits_cleanly(tmp_path: Path) -> None:
    """The whole application must start and stop with exit code 0.

    A non-zero exit (or a crash traceback) means the window tree, service
    wiring or Qt bootstrap is broken — the class of bug the HighDPI regression
    belonged to.
    """
    result = subprocess.run(
        [sys.executable, "-m", "run", "--headless", "--headless-seconds", "2"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f"headless run failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr


def test_sigterm_during_startup_is_graceful(tmp_path: Path) -> None:
    """SIGTERM while the first-run wizard is open must still exit cleanly.

    With a fresh data directory the app opens the first-run wizard inside a
    nested event loop.  ``QApplication.quit`` cannot unwind that loop, so before
    the fix a signal here left the process running until the default terminate
    disposition killed it (exit code -15).  A zero exit code proves the signal
    was routed into the nested loop and shut down on the graceful path.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "run", "--simulate", "--no-tray"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_env(tmp_path),
        **_popen_kwargs(),
    )
    try:
        time.sleep(6.0)
        assert process.poll() is None, (
            "the app exited during start-up:\n"
            + (process.stdout.read() if process.stdout else "")
        )
        _stop_gracefully(process)
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            pytest.fail("the app did not shut down within 25s of the interrupt")
        assert process.returncode == 0, (
            f"unclean shutdown ({process.returncode}):\n"
            + (process.stdout.read() if process.stdout else "")
        )
    finally:
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()


def test_sigterm_during_main_loop_runs_the_window_teardown(tmp_path: Path) -> None:
    """SIGTERM once the window is up must run ``MainWindow.shutdown``.

    The crash marker is written by ``guard.arm()`` just before the window is
    built and removed only by the graceful teardown.  Waiting for the marker to
    appear proves we are signalling the running window (not the wizard), and its
    absence afterwards proves the teardown ran rather than the process being
    killed.
    """
    data_dir = tmp_path / "data"
    marker = data_dir / "Cache" / "running.lock"
    process = subprocess.Popen(
        [sys.executable, "-m", "run", "--headless", "--headless-seconds", "30"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_env(tmp_path),
        **_popen_kwargs(),
    )
    try:
        deadline = time.time() + 25
        while time.time() < deadline and process.poll() is None and not marker.exists():
            time.sleep(0.2)
        assert process.poll() is None, "the app exited during start-up"
        assert marker.exists(), "the app never armed its crash guard"

        _stop_gracefully(process)
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            pytest.fail("the app did not shut down within 25s of the interrupt")

        assert process.returncode == 0, (
            f"unclean shutdown ({process.returncode}):\n"
            + (process.stdout.read() if process.stdout else "")
        )
        assert not marker.exists(), f"crash marker was not cleared: {marker}"
    finally:
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()
