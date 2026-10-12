"""SmartCooling Controller — application entry point (spec §1, §2, §53, §240).

This module is the only place that knows how to assemble the whole program:
command-line parsing, the single-instance guard, crash detection, the first-run
wizard, service startup and the Qt event loop.

Design notes
------------
* **Nothing heavy happens before ``QApplication`` exists.**  Qt is imported
  lazily inside :func:`main` so ``--version`` and ``--help`` stay instant.
* **The persisted data mode wins.**  If the user chose "portable" during
  onboarding we re-open the services in that layout before starting anything.
* **Crash detection is cheap and honest.**  A marker file is written before the
  UI appears and removed on a clean exit; finding a stale marker offers safe
  mode rather than silently carrying on.
* **Every failure has a next step.**  Uncaught exceptions are logged, shown to
  the user, and (when possible) the app keeps running so they can export
  diagnostics (spec §125, §126).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from app import APP_NAME, APP_ORGANIZATION, APP_SLUG, APP_VERSION, BUILD_ID, PROTOCOL_VERSION

if TYPE_CHECKING:  # pragma: no cover
    from app.core.enums import DataMode

_CRASH_MARKER = "running.lock"

#: The shutdown callback the signal handler invokes.  It starts as ``app.quit``
#: and is upgraded to ``window.shutdown`` once the window exists.
_SHUTDOWN: list = [lambda: None]

#: Set when a signal asked the app to quit.  ``QApplication.quit`` is a no-op
#: before ``app.exec`` starts, so start-up checks this flag at each phase
#: boundary and bails out cleanly instead of entering the event loop forever.
_QUIT_REQUESTED: list = [False]

#: Zero-argument callable that stops whichever nested event loop currently owns
#: the GUI thread (first-run wizard, safe-mode prompt), or ``None`` when the
#: main ``app.exec`` loop is running.  ``QApplication.quit()`` only unwinds the
#: *top* loop, so a signal that arrives while a nested loop is open has to stop
#: that loop too or the app hangs.  ``QEventLoop`` uses ``quit`` while a
#: ``QDialog`` uses ``reject``, hence storing a callable rather than the object.
_ACTIVE_LOOP: list = [None]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="smartcooling",
        description=f"{APP_NAME} {APP_VERSION} — ESP32 cooling-pad telemetry and control.",
    )
    parser.add_argument("--version", action="store_true", help="print version information and exit")
    parser.add_argument(
        "--portable", action="store_true", help="store data next to the executable"
    )
    parser.add_argument(
        "--standard", action="store_true", help="store data under the user profile"
    )
    parser.add_argument(
        "--safe", action="store_true", help="start with safe defaults (no hardware probing)"
    )
    parser.add_argument(
        "--simulate", action="store_true", help="force the simulated hardware backend"
    )
    parser.add_argument(
        "--diagnostics", action="store_true", help="open the diagnostics page on start"
    )
    parser.add_argument("--page", metavar="KEY", default="", help="open a specific page on start")
    parser.add_argument(
        "--no-tray", action="store_true", help="run without a system-tray icon"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "start-up smoke-test mode: skip the first-run wizard and safe-mode "
            "prompt, never show a tray icon, and quit automatically after "
            "--headless-seconds"
        ),
    )
    parser.add_argument(
        "--headless-seconds",
        type=float,
        default=4.0,
        metavar="SECONDS",
        help="how long --headless keeps the app alive before quitting (default: 4)",
    )
    return parser.parse_args(argv)


def _configure_qt() -> None:
    """Environment tweaks that must happen before Qt is imported."""
    # Keep the UI crisp on fractional-DPI monitors without the historical
    # rounding artefacts around 125%/150% scaling.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    # A stable platform plugin on Windows; harmless elsewhere.
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows")


class _SingleInstance:
    """A local-socket guard so a second launch focuses the first window."""

    def __init__(self, key: str) -> None:
        self._key = key
        self._server = None

    def acquire(self) -> bool:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket

        probe = QLocalSocket()
        probe.connectToServer(self._key)
        if probe.waitForConnected(200):
            probe.disconnectFromServer()
            return False
        server = QLocalServer()
        server.removeServer(self._key)  # clear a stale socket after a crash
        if not server.listen(self._key):
            return True  # never block startup because of a socket quirk
        self._server = server
        return True

    def notify(self) -> None:
        from PySide6.QtNetwork import QLocalSocket

        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if socket.waitForConnected(300):
            socket.write(b"raise")
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None


class _CrashGuard:
    """Detects an unclean shutdown using a marker file in the cache directory."""

    def __init__(self, cache_dir: Path) -> None:
        self._path = cache_dir / _CRASH_MARKER
        self.detected = self._path.exists()

    def arm(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                f"{os.getpid()}\n{APP_VERSION}+{BUILD_ID}\n", encoding="utf-8"
            )
        except OSError:
            pass

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)


def _install_excepthook(log, *, interactive: bool = True) -> None:
    def _hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error(
            "uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        if not interactive:
            # A head-less / smoke-test run has no user to dismiss a modal
            # dialog, so showing one would hang the process instead of letting
            # the non-zero exit code surface the failure.
            return
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    APP_NAME,
                    f"{exc_value}\n\nDetails were written to the log file.",
                )
        except Exception:  # noqa: BLE001 - the hook must never raise
            pass

    sys.excepthook = _hook


def _install_signal_handlers(app, log):
    """Make Ctrl+C and SIGTERM quit the app cleanly.

    Installed *before* service start-up so an interrupt during initialisation
    still produces an orderly shutdown; previously the handlers were only
    registered after the window was built, so a signal during start-up hit the
    default "terminate immediately" disposition and left the crash marker
    behind.

    Qt's event loop may not be running yet when a signal arrives, so the quit
    is queued with ``QTimer.singleShot`` and runs as soon as a loop starts.
    The queued callback tears down any nested loop still open (first-run
    wizard, safe-mode prompt) and then calls :data:`_SHUTDOWN`, which
    :func:`main` upgrades from ``app.quit`` to ``window.shutdown`` once the
    window exists.  A second interrupt gives up waiting and quits on the spot.

    Returns the heartbeat timer that keeps Python servicing signals while Qt
    owns the event loop; the caller must stop it during shutdown.
    """
    from PySide6.QtCore import QTimer

    state = {"count": 0, "queued": False}

    def _quit_now() -> None:
        state["queued"] = False
        _QUIT_REQUESTED[0] = True
        stop_nested = _ACTIVE_LOOP[0]
        if stop_nested is not None:
            stop_nested()
        _SHUTDOWN[0]()

    def _handle(_signum, _frame) -> None:
        state["count"] += 1
        if state["count"] >= 2:
            raise KeyboardInterrupt
        if state["queued"]:
            return
        state["queued"] = True
        QTimer.singleShot(0, _quit_now)

    try:
        signal.signal(signal.SIGINT, _handle)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle)
        # Windows has no real SIGTERM: ``Popen.send_signal(SIGTERM)`` maps to
        # TerminateProcess (an ungraceful kill) and CTRL_C is unreliable for a
        # process in its own group, so the console-level CTRL_BREAK_EVENT
        # (``signal.SIGBREAK``) is the one signal that reaches us cleanly there.
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handle)
    except (ValueError, OSError):  # pragma: no cover - non-main thread on some platforms
        log.debug("signal handlers were not installed")

    heartbeat = QTimer()
    heartbeat.setInterval(400)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()
    return heartbeat


def _build_services(mode: DataMode | None):
    """Create services, honouring a previously persisted data mode."""
    from app.services import ApplicationServices

    services = ApplicationServices(mode=mode)
    if mode is None and services.config.data.mode is not services.paths.mode:
        services = ApplicationServices(mode=services.config.data.mode)
    return services


def _run_welcome(services) -> None:
    """Show the first-run wizard and persist whatever the user picked."""
    from PySide6.QtCore import QEventLoop

    from app.ui.design import build_theme
    from app.ui.i18n import Translator
    from app.ui.widgets.wizard import WelcomeWizard

    appearance = services.config.appearance
    theme = build_theme(
        appearance.theme, accent=appearance.accent, animation=appearance.animation_level,
        show_glow=appearance.show_glow,
    )
    translator = Translator(services.config.localization.language)
    wizard = WelcomeWizard(theme, translator)

    loop = QEventLoop()
    captured: dict = {}

    def _finished(patch: dict) -> None:
        captured.update(patch)
        loop.quit()

    wizard.finished.connect(_finished)
    wizard.show()
    _ACTIVE_LOOP[0] = loop.quit
    try:
        loop.exec()
    finally:
        _ACTIVE_LOOP[0] = None
    if not captured:
        captured = {"general": {"first_run_completed": True}}
    services.update_config(**captured)
    return captured


def _safe_mode_dialog() -> str:
    """Ask the user how to proceed after an unclean shutdown."""
    from PySide6.QtWidgets import QMessageBox

    from app.ui.i18n import Translator

    translator = Translator()
    box = QMessageBox()
    box.setWindowTitle(translator.t("safe.title"))
    box.setText(translator.t("safe.body"))
    box.setIcon(QMessageBox.Icon.Warning)
    diag = box.addButton(translator.t("safe.diagnostics"), QMessageBox.ButtonRole.ActionRole)
    reset = box.addButton(translator.t("safe.reset"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(translator.t("safe.quit"), QMessageBox.ButtonRole.RejectRole)
    # Track this nested loop so a SIGTERM/SIGINT that lands while the prompt is
    # open is routed here; without it the app would enter app.exec() and hang.
    _ACTIVE_LOOP[0] = box.reject
    try:
        box.exec()
    finally:
        _ACTIVE_LOOP[0] = None
    if _QUIT_REQUESTED[0]:
        return "quit"
    if box.clickedButton() is diag:
        return "diagnostics"
    if box.clickedButton() is reset:
        return "safe"
    return "quit"


def main(argv: list[str] | None = None) -> int:
    """Program entry point.  Returns a process exit code."""
    args = _parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {APP_VERSION} (build {BUILD_ID})")
        print(f"protocol v{PROTOCOL_VERSION} · python {sys.version.split()[0]}")
        return 0

    _configure_qt()

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    # The rounding-policy enum lives on Qt, not QGuiApplication; passing
    # PassThrough keeps fractional-DPI displays (125%/150%) from rounding the
    # scale factor and producing blurry or mis-sized widgets.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationVersion(APP_VERSION)
    app.setDesktopFileName(APP_SLUG)
    # The window may be hidden to the tray, so an explicit quit is required.
    app.setQuitOnLastWindowClosed(False)

    instance = _SingleInstance(APP_SLUG)
    if not instance.acquire():
        instance.notify()
        return 0

    from app.core.logging_setup import get_logger

    log = get_logger("app")
    # Reset per-run lifecycle state so a re-entrant call (tests, embedded use)
    # cannot inherit a stale quit request or shutdown callback.
    _QUIT_REQUESTED[0] = False
    _ACTIVE_LOOP[0] = None
    _install_excepthook(log, interactive=not args.headless)
    _SHUTDOWN[0] = app.quit
    heartbeat = _install_signal_handlers(app, log)

    mode = None
    if args.portable:
        from app.core.enums import DataMode

        mode = DataMode.PORTABLE
    elif args.standard:
        from app.core.enums import DataMode

        mode = DataMode.STANDARD

    services = _build_services(mode)
    guard = _CrashGuard(services.paths.cache)

    start_page = args.page
    simulate = args.simulate
    if args.headless:
        # A smoke-test run must be deterministic: no modal prompts, no tray,
        # and it always exercises the real hardware backend path.
        simulate = True
    elif guard.detected and not args.safe:
        choice = _safe_mode_dialog()
        if choice == "quit":
            instance.release()
            return 0
        if choice == "diagnostics":
            start_page = "diagnostics"
        simulate = True  # safe mode never touches real hardware

    if not services.config.general.first_run_completed and not args.headless:
        patch = _run_welcome(services)
        new_mode = (patch.get("data") or {}).get("mode")
        if new_mode is not None and new_mode is not services.paths.mode:
            services = _build_services(new_mode)
            guard = _CrashGuard(services.paths.cache)

    # A signal may have arrived while a modal loop was open (wizard or safe-mode
    # prompt).  Bail out here rather than falling into app.exec(), which would
    # otherwise spin forever because the earlier quit() was a no-op.
    if _QUIT_REQUESTED[0]:
        heartbeat.stop()
        services.shutdown()
        instance.release()
        return 0

    guard.arm()

    from app.ui.shell import create_window

    try:
        # Real hardware discovery can block for a long time opening WMI/LHM;
        # run it in the background so the window paints immediately and shows an
        # "initialising" state instead of freezing.  Smoke/safe runs stay
        # synchronous so their output is deterministic.
        services.start(simulate=simulate, background_discovery=not simulate)
    except Exception as exc:  # report, then continue degraded
        log.exception("failed to start services")
        if not args.headless:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(None, APP_NAME, str(exc))

    window = create_window(services)
    if args.no_tray or args.headless:
        window.tray.hide()
    if start_page:
        window.show_page(start_page)

    general = services.config.general
    if general.start_minimized and window.tray.isVisible() and not args.headless:
        window.hide()
    else:
        window.show()
        window.raise_()
        window.activateWindow()

    # Keep a reference so the window is never garbage collected mid-session.
    app._smartcooling_window = window  # type: ignore[attr-defined]
    # From here on, a signal runs the full graceful teardown, not a bare quit.
    _SHUTDOWN[0] = window.shutdown

    if args.headless:
        QTimer.singleShot(max(0.5, args.headless_seconds) * 1000, window.shutdown)

    _ACTIVE_LOOP[0] = None
    try:
        if _QUIT_REQUESTED[0]:
            # A signal raced the final phase of start-up; tear the window down
            # gracefully instead of entering an event loop that would hang.
            window.shutdown()
            exit_code = 0
        else:
            exit_code = app.exec()
    finally:
        heartbeat.stop()
        guard.clear()
        instance.release()

    return int(exit_code)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
