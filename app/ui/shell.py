"""Main application window — the shell (spec §27, §56, §57, §84, §85, §117).

The shell owns the page lifecycle and the cross-cutting concerns so that pages
stay small and testable:

* **Lazy page construction.**  A page is built the first time it is shown, so
  startup cost is proportional to what the user actually opens.
* **Snapshot fan-out.**  One bus subscription feeds the visible page through the
  page's own coalescing timer.  Hidden pages keep their last snapshot and are
  refreshed once on show, which keeps the UI thread free (spec §205, §208).
* **Command palette.**  Every page plus a curated set of actions (spec §84).
* **Tray, compact mode, shortcuts, RTL, theme rebuild.**  All reversible at
  runtime without restarting the process (spec §245-§249).
* **Failure isolation.**  A page that raises while building is replaced by an
  inline error card instead of taking the window down (spec §125).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME
from app.core.enums import ConnectionState, Language, PerformanceMode
from app.core.events import Event
from app.models.telemetry import TelemetrySnapshot
from app.network.connection import TOPIC_STATE as TOPIC_CONNECTION
from app.services.alerts import TOPIC_ALERT, TOPIC_ALERT_CLEARED
from app.services.cooling_control import TOPIC_MODE
from app.services.health import TOPIC_HEALTH
from app.services.sessions import TOPIC_SESSIONS
from app.telemetry.engine import TOPIC_SNAPSHOT
from app.ui.animation import AnimationManager
from app.ui.design import Theme, build_theme
from app.ui.i18n import Translator
from app.ui.icons import make_icon
from app.ui.pages.about import AboutPage
from app.ui.pages.alerts import AlertsPage
from app.ui.pages.base import Page
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.data import DataPage
from app.ui.pages.diagnostics import DiagnosticsPage
from app.ui.pages.diagram import DiagramPage
from app.ui.pages.esp32 import Esp32Page
from app.ui.pages.fan import FanPage
from app.ui.pages.history import HistoryPage
from app.ui.pages.sensors import SensorsPage
from app.ui.pages.sessions import SessionsPage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.telemetry import TelemetryPage
from app.ui.styles import build_stylesheet
from app.ui.widgets.atoms import Card, EmptyState
from app.ui.widgets.chrome import NavigationRail, StatusBar, TopBar
from app.ui.widgets.compact import CompactView
from app.ui.widgets.palette import Command, CommandPalette

if TYPE_CHECKING:  # pragma: no cover
    from app.services import ApplicationServices

PAGE_CLASSES: dict[str, type[Page]] = {
    "dashboard": DashboardPage,
    "telemetry": TelemetryPage,
    "diagram": DiagramPage,
    "fan": FanPage,
    "sessions": SessionsPage,
    "history": HistoryPage,
    "sensors": SensorsPage,
    "alerts": AlertsPage,
    "esp32": Esp32Page,
    "diagnostics": DiagnosticsPage,
    "settings": SettingsPage,
    "data": DataPage,
    "about": AboutPage,
}

_SHORTCUTS: tuple[tuple[str, str, str], ...] = (
    ("Ctrl+K", "shortcut.palette", "palette"),
    ("Ctrl+,", "shortcut.settings", "settings"),
    ("Ctrl+D", "shortcut.diagnostics", "diagnostics"),
    ("Ctrl+R", "shortcut.session", "session"),
    ("Ctrl+E", "shortcut.export", "export"),
    ("Ctrl+Shift+C", "shortcut.compact", "compact"),
    ("Ctrl+1", "nav.dashboard", "page:dashboard"),
    ("Ctrl+2", "nav.telemetry", "page:telemetry"),
    ("Ctrl+3", "nav.fan", "page:fan"),
    ("Ctrl+4", "nav.sessions", "page:sessions"),
    ("Ctrl+5", "nav.sensors", "page:sensors"),
    ("Ctrl+6", "nav.alerts", "page:alerts"),
    ("Ctrl+7", "nav.esp32", "page:esp32"),
    ("Ctrl+8", "nav.diagnostics", "page:diagnostics"),
)

_TRAY_LABELS = {"open": "tray.open", "pause": "tray.pause", "exit": "tray.exit"}


class MainWindow(QMainWindow):
    """The SmartCooling Controller main window."""

    _instance: ClassVar[MainWindow | None] = None

    def __init__(self, services: ApplicationServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self.translator = Translator(services.config.localization.language)
        self.theme = self._resolve_theme()
        self.animations = AnimationManager(self.theme)

        self._pages: dict[str, Page] = {}
        self._current_key = ""
        self._paused = False
        self._shutting_down = False

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1080, 700)
        self.setWindowIcon(self._app_icon())

        self._build_ui()
        self._build_tray()
        self._build_shortcuts()
        self._build_palette()
        self._connect_services()
        self._restore_window_state()
        self.apply_theme(rebuild_pages=False)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_status)
        self._clock.start()

        last = services.config.window.selected_page or "dashboard"
        self.show_page(last if last in PAGE_CLASSES else "dashboard")

    # -- construction ------------------------------------------------------

    def _resolve_theme(self) -> Theme:
        appearance = self.services.config.appearance
        return build_theme(
            appearance.theme,
            accent=appearance.accent,
            animation=appearance.animation_level,
            show_glow=appearance.show_glow,
        )

    def _app_icon(self) -> QIcon:
        return make_icon("fan", self.theme.color("primary"), 64)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.rail = NavigationRail(self.theme, self.translator)
        self.rail.page_requested.connect(self.show_page)
        layout.addWidget(self.rail)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.topbar = TopBar(self.theme, self.translator)
        self.topbar.search_requested.connect(self._open_palette)
        self.topbar.settings_requested.connect(lambda: self.show_page("settings"))
        self.topbar.alerts_requested.connect(lambda: self.show_page("alerts"))
        self.topbar.compact_requested.connect(self.toggle_compact)
        self.topbar.session_toggled.connect(self._toggle_session)
        right.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        right.addWidget(self.stack, 1)

        self.status = StatusBar(self.theme, self.translator)
        right.addWidget(self.status)
        layout.addLayout(right, 1)

        self.compact = CompactView(self.theme, self.translator)
        self.compact.expand_requested.connect(self.exit_compact)
        self.compact.pause_toggled.connect(self.set_paused)
        self.compact.exit_requested.connect(self._request_quit)
        self.compact.moved.connect(self._on_compact_moved)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self._app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self._tray_actions: dict[str, QAction] = {}
        handlers = {
            "open": self._restore_from_tray,
            "pause": lambda: self.set_paused(not self._paused),
            "exit": self._request_quit,
        }
        for key, label_key in _TRAY_LABELS.items():
            action = QAction(self.translator.t(label_key), self)
            action.triggered.connect(handlers[key])
            if key == "pause":
                action.setCheckable(True)
            if key == "exit":
                menu.addSeparator()
            menu.addAction(action)
            self._tray_actions[key] = action
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._tray_menu = menu
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _build_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        for sequence, _label_key, action in _SHORTCUTS:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(lambda a=action: self._run_action(a))
            self._shortcuts.append(shortcut)
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self._request_quit)
        self._shortcuts.append(quit_shortcut)

    def _build_palette(self) -> None:
        self.command_palette = CommandPalette(self.theme, self.translator, self)
        self.command_palette.invoked.connect(self._run_action)
        self._refresh_palette()

    def _refresh_palette(self) -> None:
        commands: list[Command] = []
        for key, page_class in PAGE_CLASSES.items():
            commands.append(
                Command(
                    id=f"page:{key}",
                    label=self.translator.t(page_class.title_key),
                    category=self.translator.t("palette.pages"),
                    icon=page_class.icon_name,
                    keywords=(key, page_class.icon_name),
                )
            )
        for action, label_key in (
            ("palette", "shortcut.palette"),
            ("session", "shortcut.session"),
            ("compact", "shortcut.compact"),
            ("export", "shortcut.export"),
            ("pause", "tray.pause"),
        ):
            commands.append(
                Command(
                    id=action,
                    label=self.translator.t(label_key),
                    category=self.translator.t("palette.commands"),
                    icon="play",
                    keywords=(action,),
                )
            )
        self.command_palette.set_commands(commands)
        self.command_palette.set_placeholder(self.translator.t("palette.placeholder"))

    # -- service wiring ----------------------------------------------------

    def _connect_services(self) -> None:
        # The telemetry engine publishes from a worker thread; touching Qt
        # widgets there is undefined behaviour.  All bus traffic is funnelled
        # through a single bridge so slots run on the GUI thread (spec §62).
        from app.application.qt_bridge import QtEventBridge

        self._bridge = QtEventBridge(self.services.bus, parent=self)
        self._bridge.attach(
            {
                TOPIC_SNAPSHOT,
                TOPIC_ALERT,
                TOPIC_ALERT_CLEARED,
                TOPIC_HEALTH,
                TOPIC_CONNECTION,
                TOPIC_SESSIONS,
                TOPIC_MODE,
            }
        )
        self._bridge.received.connect(self._on_bus_event)

    def _on_bus_event(self, event: Event) -> None:
        """Dispatch a bus event on the GUI thread."""
        topic = event.topic
        if topic == TOPIC_SNAPSHOT:
            self._on_snapshot(event)
        elif topic in (TOPIC_ALERT, TOPIC_ALERT_CLEARED):
            self._on_alert(event)
        elif topic == TOPIC_HEALTH:
            self._on_health(event)
        elif topic == TOPIC_CONNECTION:
            self._on_connection(event)
        elif topic == TOPIC_SESSIONS:
            self._on_session(event)
        elif topic == TOPIC_MODE:
            self._on_mode(event)

    def _on_snapshot(self, event: Event) -> None:
        snapshot: TelemetrySnapshot = event.payload
        if self._paused:
            return
        page = self._pages.get(self._current_key)
        if page is not None and page.content.isVisible():
            page.on_snapshot(snapshot)
        if self.compact.isVisible():
            self.compact.update_snapshot(snapshot)

    def _on_alert(self, event: Event) -> None:
        self.topbar.set_alert_count(len(self.services.alerts.active))
        page = self._pages.get("alerts")
        snapshot = self.services.telemetry.last_snapshot
        if page is not None and snapshot is not None:
            page.on_snapshot(snapshot)

    def _on_health(self, event: Event) -> None:
        report = event.payload
        # A HealthReport keeps every subsystem independent (spec §88); the rail
        # badge only shows the worst status, with the detail in its tooltip.
        worst = report.worst() if hasattr(report, "worst") else report
        self.rail.set_health(worst)
        non_healthy = report.summary() if hasattr(report, "summary") else {}
        if non_healthy:
            lines = [
                f"{name}: {status.value}" for name, status in non_healthy.items()
            ]
            self.rail.health_badge.setToolTip("\n".join(lines))
        else:
            self.rail.health_badge.setToolTip(self.translator.t("health.healthy"))

    def _on_connection(self, event: Event) -> None:
        payload = event.payload
        state = payload.get("state") if isinstance(payload, dict) else payload
        if isinstance(state, str):
            with contextlib.suppress(ValueError):
                state = ConnectionState(state)
        if not isinstance(state, ConnectionState):
            return
        self.rail.set_connection(state)
        self.status.set_connection(state, mock=self.services.connection.status.is_mock)
        self.compact.set_connection(state)

    def _on_session(self, event: Event) -> None:
        self.topbar.set_session_state(self.services.sessions.recording)

    def _on_mode(self, event: Event) -> None:
        snapshot = self.services.telemetry.last_snapshot
        backend = snapshot.backend.value if snapshot is not None else "none"
        self.status.set_backend(backend, simulated=bool(snapshot and snapshot.simulated))

    # -- page management ---------------------------------------------------

    def show_page(self, key: str) -> None:
        if key not in PAGE_CLASSES:
            return
        page = self._pages.get(key)
        if page is None:
            page = self._create_page(key)
            if page is None:
                return
            self._pages[key] = page
            self.stack.addWidget(page.content)
        previous = self._pages.get(self._current_key)
        if previous is not None and previous is not page:
            previous.content.setVisible(False)
        self._current_key = key
        self.stack.setCurrentWidget(page.content)
        page.content.setVisible(True)
        self.rail.set_active(key)
        self.topbar.set_page(
            self.translator.t(page.title_key),
            self.translator.t(page.subtitle_key) if page.subtitle_key else "",
        )
        page.on_shown()
        snapshot = self.services.telemetry.last_snapshot
        if snapshot is not None:
            page.on_snapshot(snapshot)
        with contextlib.suppress(Exception):
            self.services.update_config(window={"selected_page": key})

    def _create_page(self, key: str) -> Page | None:
        page_class = PAGE_CLASSES[key]
        try:
            page = page_class(self.services, self.theme, self.translator, self.animations)
            page.build()
            page.message.connect(self._show_message)
            page.navigate.connect(self.show_page)
            page.set_theme(self.theme)
            if isinstance(page, SettingsPage):
                page.theme_changed.connect(self._on_theme_changed)
                page.language_changed.connect(self._on_language_changed)
                page.performance_changed.connect(self._on_performance_changed)
            return page
        except Exception as exc:  # noqa: BLE001 - a broken page must not kill the window
            return self._create_error_page(key, exc)

    def _create_error_page(self, key: str, exc: Exception) -> Page:
        page_class = PAGE_CLASSES[key]

        class _ErrorPage(Page):
            title_key = page_class.title_key
            subtitle_key = "dialog.error_title"
            icon_name = page_class.icon_name
            wants_snapshots = False

            def _build_content(self, content: QWidget) -> None:
                layout = QVBoxLayout(content)
                layout.setContentsMargins(24, 24, 24, 24)
                card = Card()
                inner = QVBoxLayout(card)
                inner.setContentsMargins(24, 24, 24, 24)
                inner.addWidget(
                    EmptyState(self.theme, self.tr_, icon_name="alerts",
                               title=self.tr_.t("dialog.error_title"))
                )
                detail = QLabel(str(exc))
                detail.setProperty("role", "mono")
                detail.setWordWrap(True)
                inner.addWidget(detail)
                layout.addWidget(card)
                layout.addStretch(1)

            def refresh(self, snapshot: TelemetrySnapshot) -> None:
                return

        page = _ErrorPage(self.services, self.theme, self.translator, self.animations)
        page.build()
        page.set_theme(self.theme)
        return page

    def _show_message(self, text: str, level: str) -> None:
        label = getattr(self, "_toast", None)
        if label is None:
            label = QLabel(self)
            label.setObjectName("Toast")
            label.setVisible(False)
            self._toast = label
        label.setText(text)
        token = {"success": "success", "error": "danger", "warning": "warning"}.get(level, "info")
        label.setStyleSheet(
            f"background: {self.theme.color(token)}; color: {self.theme.palette.bg};"
            "padding: 8px 16px; border-radius: 10px;"
        )
        label.adjustSize()
        label.move(max(24, self.width() - label.width() - 40), self.height() - label.height() - 60)
        label.setVisible(True)
        label.raise_()
        self.animations.fade(label, start=0.0, end=1.0, duration_ms=180)
        QTimer.singleShot(
            2600,
            lambda: self.animations.fade(
                label, start=1.0, end=0.0, duration_ms=280,
                on_finished=lambda: label.setVisible(False),
            ),
        )

    # -- actions -----------------------------------------------------------

    def _run_action(self, action: str) -> None:
        if action.startswith("page:"):
            self.show_page(action.split(":", 1)[1])
            return
        if action == "palette":
            self._open_palette()
        elif action == "settings":
            self.show_page("settings")
        elif action == "diagnostics":
            self.show_page("diagnostics")
        elif action == "session":
            self._toggle_session(not self.services.sessions.recording)
        elif action == "compact":
            self.toggle_compact()
        elif action == "export":
            self.show_page("data")
        elif action == "pause":
            self.set_paused(not self._paused)

    def _open_palette(self) -> None:
        self._refresh_palette()
        self.command_palette.open()

    def _toggle_session(self, start: bool) -> None:
        if start:
            profile = self.services.cooling.profile
            self.services.sessions.start(profile_key=profile.key if profile else "")
            self.topbar.set_session_state(True)
            self._show_message(self.translator.t("sessions.start"), "success")
        else:
            self.services.sessions.stop()
            self.topbar.set_session_state(False)
            self._show_message(self.translator.t("sessions.stop"), "info")

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self.services.telemetry.stop()
        else:
            self.services.telemetry.start()
        self.compact.set_paused(paused)
        action = self._tray_actions.get("pause")
        if action is not None:
            action.blockSignals(True)
            action.setChecked(paused)
            action.blockSignals(False)
        self._show_message(
            self.translator.t("status.paused") if paused else self.translator.t("status.live"),
            "info",
        )

    # -- compact mode ------------------------------------------------------

    def toggle_compact(self) -> None:
        if self.compact.isVisible():
            self.exit_compact()
        else:
            self.enter_compact()

    def enter_compact(self) -> None:
        appearance = self.services.config.appearance
        self.compact.set_always_on_top(appearance.compact_always_on_top)
        self.compact.setWindowOpacity(appearance.compact_opacity)
        self.compact.set_locked(appearance.compact_position_locked)
        if appearance.compact_position and len(appearance.compact_position) == 2:
            self.compact.move(int(appearance.compact_position[0]), int(appearance.compact_position[1]))
        profile = self.services.cooling.profile
        self.compact.set_profile(profile.name if profile else "")
        snapshot = self.services.telemetry.last_snapshot
        if snapshot is not None:
            self.compact.update_snapshot(snapshot)
        self.compact.show()
        self.hide()
        self.animations.fade(self.compact, start=0.0, end=1.0, duration_ms=200)

    def exit_compact(self) -> None:
        self.compact.hide()
        self._restore_from_tray()

    def _on_compact_moved(self, x: int, y: int) -> None:
        with contextlib.suppress(Exception):
            self.services.update_config(appearance={"compact_position": [x, y]})

    # -- theme / language / performance ------------------------------------

    def _on_theme_changed(self) -> None:
        self.theme = self._resolve_theme()
        self.apply_theme(rebuild_pages=True)

    def _on_language_changed(self, language: Language) -> None:
        self.translator.set_language(language)
        self.apply_theme(rebuild_pages=False, retranslate=True)

    def _on_performance_changed(self, mode: PerformanceMode) -> None:
        self.theme = self._resolve_theme()
        self.apply_theme(rebuild_pages=True)

    def apply_theme(self, *, rebuild_pages: bool = True, retranslate: bool = False) -> None:
        app = QApplication.instance()
        rtl = self.translator.is_rtl
        if isinstance(app, QGuiApplication):
            app.setLayoutDirection(
                Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight
            )
        self.setStyleSheet(build_stylesheet(self.theme, rtl=rtl))
        self.rail.set_theme(self.theme)
        self.topbar.set_theme(self.theme)
        self.status.set_theme(self.theme)
        self.compact.set_theme(self.theme)
        self.command_palette.set_theme(self.theme)
        self.setWindowIcon(self._app_icon())
        if self.tray.isVisible():
            self.tray.setIcon(self._app_icon())
        self.rail.set_side("right" if rtl else "left")

        for page in self._pages.values():
            page.set_translator(self.translator)
            page.set_theme(self.theme)
            if retranslate:
                page.retranslate()

        if rebuild_pages and self._pages:
            self._rebuild_visible_page()
        if retranslate:
            self.rail.retranslate()
            self.topbar.retranslate()
            self.status.retranslate()
            self.compact.retranslate()
            for key, action in self._tray_actions.items():
                action.setText(self.translator.t(_TRAY_LABELS[key]))
            self._refresh_palette()
            current = self._pages.get(self._current_key)
            if current is not None:
                self.topbar.set_page(
                    self.translator.t(current.title_key),
                    self.translator.t(current.subtitle_key) if current.subtitle_key else "",
                )

    def _rebuild_visible_page(self) -> None:
        """Recreate the current page so hard-coded colours are refreshed."""
        key = self._current_key
        if not key:
            return
        page = self._pages.pop(key, None)
        if page is not None:
            self.stack.removeWidget(page.content)
            page.deleteLater()
        self._current_key = ""
        self.show_page(key)

    # -- status tick -------------------------------------------------------

    def _tick_status(self) -> None:
        self.status.set_rate(self.services.telemetry.interval_s)
        snapshot = self.services.telemetry.last_snapshot
        backend = snapshot.backend.value if snapshot is not None else "none"
        self.status.set_backend(backend, simulated=bool(snapshot and snapshot.simulated))
        self.status.set_latency(
            self.services.connection.status.latency_ms,
            self.services.connection.packet_loss_percent,
        )
        persistence = self.services.persistence
        if persistence is not None:
            self.status.set_database(persistence.database.size_bytes())

    # -- window / tray -----------------------------------------------------

    def _restore_window_state(self) -> None:
        window = self.services.config.window
        self.resize(window.width, window.height)
        if window.x is not None and window.y is not None:
            self.move(window.x, window.y)
        if window.maximized:
            self.showMaximized()

    def _save_window_state(self) -> None:
        geometry = self.normalGeometry() if self.isMaximized() else self.geometry()
        with contextlib.suppress(Exception):
            self.services.update_config(
                window={
                    "width": geometry.width(),
                    "height": geometry.height(),
                    "x": geometry.x(),
                    "y": geometry.y(),
                    "maximized": self.isMaximized(),
                    "selected_page": self._current_key,
                }
            )

    def _restore_from_tray(self) -> None:
        self.compact.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _request_quit(self) -> None:
        if self.services.config.general.confirm_exit:
            box = QMessageBox(self)
            box.setWindowTitle(self.translator.t("dialog.exit_title"))
            box.setText(self.translator.t("dialog.exit_text"))
            box.setIcon(QMessageBox.Icon.Question)
            quit_button = box.addButton(
                self.translator.t("dialog.quit"), QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton(self.translator.t("dialog.cancel"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not quit_button:
                return
        self.shutdown()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._save_window_state()
        self.animations.stop_all()
        with contextlib.suppress(Exception):
            self._bridge.detach()
        for page in list(self._pages.values()):
            with contextlib.suppress(Exception):
                page.teardown()
        with contextlib.suppress(Exception):
            self.tray.hide()
        self.services.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and self.services.config.general.minimize_to_tray
            and self.tray.isVisible()
        ):
            QTimer.singleShot(0, self.hide)

    def closeEvent(self, event: QCloseEvent) -> None:
        general = self.services.config.general
        if general.close_to_tray and self.tray.isVisible() and not self._shutting_down:
            event.ignore()
            self.hide()
            return
        if general.confirm_exit and not self._shutting_down:
            event.ignore()
            self._request_quit()
            return
        event.accept()
        self.shutdown()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Sleep/resume and first paint both land here; force one refresh so
        # charts never show a frozen pre-suspend frame (spec §60, §127).
        snapshot = self.services.telemetry.last_snapshot
        page = self._pages.get(self._current_key)
        if page is not None and snapshot is not None:
            page.on_snapshot(snapshot)


def create_window(services: ApplicationServices) -> MainWindow:
    """Create the singleton main window, reusing an existing one if present."""
    if MainWindow._instance is None:
        MainWindow._instance = MainWindow(services)
    return MainWindow._instance


__all__ = ["PAGE_CLASSES", "MainWindow", "create_window"]
