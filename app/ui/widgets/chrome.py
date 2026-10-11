"""Application chrome: navigation rail, top bar, status bar (spec §27, §117, §129).

The rail is the primary navigational object.  Layout rules:

* In LTR the rail sits on the left with a right border; in RTL it mirrors to the
  right and the border flips (spec §81 "mirrored navigation").
* Sections are labelled so a 12-item rail stays scannable.
* The active page is marked by an accent pill **and** bold weight, not colour
  alone (spec §130).

The top bar hosts the page title, global search, session control and the alert
bell; the status bar shows the always-relevant link/backend facts.
"""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.enums import ConnectionState, HealthStatus
from app.ui.design import SPACE, Theme
from app.ui.i18n import Translator
from app.ui.icons import make_icon
from app.ui.widgets.atoms import ConnectionBadge, StatusBadge
from app.ui.widgets.base import ThemedWidget


class NavItem(QPushButton):
    """A single rail entry."""

    def __init__(self, key: str, label: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.key = key
        self.icon_name = icon_name
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(18, 18))

    def set_icon_color(self, color: str) -> None:
        self.setIcon(make_icon(self.icon_name, color, 18))


class NavigationRail(ThemedWidget):
    """The vertical navigation rail."""

    page_requested = Signal(str)

    SECTIONS: ClassVar[list[tuple[str, list[tuple[str, str, str]]]]] = [
        ("nav.section.monitor", [("dashboard", "nav.dashboard", "dashboard"), ("telemetry", "nav.telemetry", "telemetry"), ("diagram", "nav.diagram", "diagram")]),
        ("nav.section.control", [("fan", "nav.fan", "fan"), ("sessions", "nav.sessions", "sessions"), ("history", "nav.history", "history")]),
        ("nav.section.data", [("sensors", "nav.sensors", "sensors"), ("alerts", "nav.alerts", "alerts")]),
        ("nav.section.system", [("esp32", "nav.esp32", "esp32"), ("diagnostics", "nav.diagnostics", "diagnostics"), ("settings", "nav.settings", "settings"), ("about", "nav.about", "about")]),
    ]

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self.setObjectName("Rail")
        self.setFixedWidth(224)
        self._items: dict[str, NavItem] = {}
        self._section_labels: list[tuple[QLabel, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["lg"], SPACE["md"], SPACE["lg"])
        layout.setSpacing(SPACE["xs"])

        self.brand = QWidget()
        brand_layout = QHBoxLayout(self.brand)
        brand_layout.setContentsMargins(SPACE["sm"], 0, SPACE["sm"], SPACE["lg"])
        brand_layout.setSpacing(SPACE["sm"])
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(28, 28)
        brand_layout.addWidget(self.brand_icon)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.brand_title = QLabel()
        self.brand_title.setProperty("role", "subheading")
        # The rail is a fixed narrow column; the product name and tagline are
        # long enough to be clipped by a single-line QLabel, so let them wrap.
        self.brand_title.setWordWrap(True)
        brand_text.addWidget(self.brand_title)
        self.brand_sub = QLabel()
        self.brand_sub.setProperty("role", "caption")
        self.brand_sub.setWordWrap(True)
        brand_text.addWidget(self.brand_sub)
        brand_layout.addLayout(brand_text, 1)
        layout.addWidget(self.brand)

        for section_key, entries in self.SECTIONS:
            label = QLabel()
            label.setObjectName("NavSection")
            layout.addWidget(label)
            self._section_labels.append((label, section_key))
            for key, _label_key, icon in entries:
                item = NavItem(key, "", icon)
                item.clicked.connect(lambda _=False, k=key: self.page_requested.emit(k))
                layout.addWidget(item)
                self._items[key] = item

        layout.addStretch(1)

        self.footer = QWidget()
        footer_layout = QVBoxLayout(self.footer)
        footer_layout.setContentsMargins(SPACE["sm"], SPACE["md"], SPACE["sm"], 0)
        footer_layout.setSpacing(SPACE["xs"])
        self.connection_badge = ConnectionBadge(theme, translator, "", tone="text_faint")
        footer_layout.addWidget(self.connection_badge)
        self.health_badge = StatusBadge(theme, translator, "", tone="text_faint")
        footer_layout.addWidget(self.health_badge)
        layout.addWidget(self.footer)

        self.retranslate()

    # -- api ---------------------------------------------------------------

    def set_active(self, key: str) -> None:
        for name, item in self._items.items():
            item.setChecked(name == key)

    def set_connection(self, state: ConnectionState) -> None:
        self.connection_badge.set_connection(state)

    def set_health(self, status: HealthStatus) -> None:
        self.health_badge.set_status(self.tr_.t(f"health.{status.value}"), self.theme.health_color(status))

    def set_side(self, side: str) -> None:
        """Mirror the rail for RTL layouts."""
        self.setProperty("side", side)

    def retranslate(self) -> None:
        self.brand_title.setText(self.tr_.t("app.name"))
        self.brand_sub.setText(self.tr_.t("app.tagline"))
        for label, section_key in self._section_labels:
            label.setText(self.tr_.t(section_key))
        for key, item in self._items.items():
            label_key = next(label for _, entries in self.SECTIONS for k, label, _ in entries if k == key)
            item.setText(self.tr_.t(label_key))
        self.apply_theme()

    def apply_theme(self) -> None:
        self.brand_icon.setPixmap(make_icon("fan", self.theme.color("primary"), 26).pixmap(26, 26))
        for item in self._items.values():
            if item.isChecked():
                item.set_icon_color(self.theme.color("primary"))
            else:
                item.set_icon_color(self.theme.color("text_muted"))


class TopBar(ThemedWidget):
    """The application top bar."""

    search_requested = Signal()
    session_toggled = Signal(bool)
    alerts_requested = Signal()
    compact_requested = Signal()
    settings_requested = Signal()

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["sm"], SPACE["xl"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel()
        self.title.setProperty("role", "title")
        titles.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setProperty("role", "caption")
        titles.addWidget(self.subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)

        self.search_button = QPushButton()
        self.search_button.setProperty("variant", "quiet")
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.setMinimumWidth(220)
        self.search_button.clicked.connect(self.search_requested.emit)
        layout.addWidget(self.search_button)

        self.session_button = QPushButton()
        self.session_button.setProperty("variant", "primary")
        self.session_button.setCheckable(True)
        self.session_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.session_button.toggled.connect(self.session_toggled.emit)
        layout.addWidget(self.session_button)

        self.alert_button = QPushButton()
        self.alert_button.setProperty("variant", "quiet")
        self.alert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alert_button.clicked.connect(self.alerts_requested.emit)
        layout.addWidget(self.alert_button)

        self.compact_button = QPushButton()
        self.compact_button.setProperty("variant", "quiet")
        self.compact_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.compact_button.clicked.connect(self.compact_requested.emit)
        layout.addWidget(self.compact_button)

        self.settings_button = QPushButton()
        self.settings_button.setProperty("variant", "quiet")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_button)

        self._alert_count = 0
        self.retranslate()

    def set_page(self, title: str, subtitle: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)

    def set_session_state(self, recording: bool) -> None:
        self.session_button.blockSignals(True)
        self.session_button.setChecked(recording)
        self.session_button.blockSignals(False)
        self.session_button.setText(
            self.tr_.t("sessions.stop") if recording else self.tr_.t("sessions.start")
        )

    def set_alert_count(self, count: int) -> None:
        self._alert_count = count
        self.alert_button.setText(f"  {count}" if count else "")

    def retranslate(self) -> None:
        self.search_button.setText("  " + self.tr_.t("common.search") + "   Ctrl+K")
        self.session_button.setText(self.tr_.t("sessions.start"))
        self.compact_button.setText(self.tr_.t("shortcut.compact"))
        self.settings_button.setText(self.tr_.t("settings.title"))
        self.alert_button.setToolTip(self.tr_.t("alerts.title"))
        self.search_button.setToolTip(self.tr_.t("palette.placeholder"))
        self.compact_button.setToolTip(self.tr_.t("shortcut.compact"))
        self.settings_button.setToolTip(self.tr_.t("shortcut.settings"))
        self.apply_theme()

    def apply_theme(self) -> None:
        self.search_button.setIcon(make_icon("search", self.theme.color("text_muted"), 15))
        self.alert_button.setIcon(make_icon("alerts", self.theme.color("text_muted"), 16))
        self.compact_button.setIcon(make_icon("grid", self.theme.color("text_muted"), 16))
        self.settings_button.setIcon(make_icon("settings", self.theme.color("text_muted"), 16))


class StatusBar(ThemedWidget):
    """Bottom status strip: link, backend, sampling, latency, storage."""

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], 0, SPACE["lg"], 0)
        layout.setSpacing(SPACE["lg"])

        self.connection_label = self._cell(layout)
        self.backend_label = self._cell(layout)
        layout.addStretch(1)
        self.rate_label = self._cell(layout)
        self.latency_label = self._cell(layout)
        self.db_label = self._cell(layout)

    def _cell(self, layout: QHBoxLayout) -> QLabel:
        label = QLabel()
        label.setProperty("role", "caption")
        layout.addWidget(label)
        return label

    def set_connection(self, state: ConnectionState, *, mock: bool = False) -> None:
        text = self.tr_.t(f"conn.{state.value}")
        if mock:
            text += " · " + self.tr_.t("esp32.mock")
        self.connection_label.setText("● " + text)
        self.connection_label.setStyleSheet(f"color: {self.theme.connection_color(state)};")

    def set_backend(self, name: str, simulated: bool = False) -> None:
        text = name.upper()
        if simulated:
            text += " · " + self.tr_.t("common.simulated")
        self.backend_label.setText(text)

    def set_rate(self, interval_s: float) -> None:
        hz = 1.0 / interval_s if interval_s > 0 else 0.0
        self.rate_label.setText(f"{self.tr_.number(hz, decimals=1)} Hz")

    def set_latency(self, latency_ms: float | None, loss_percent: float | None) -> None:
        if latency_ms is None:
            self.latency_label.setText("")
            return
        text = f"{self.tr_.number(latency_ms, decimals=0)} ms"
        if loss_percent:
            text += f" · {self.tr_.number(loss_percent, decimals=1)}%"
        self.latency_label.setText(text)

    def set_database(self, size_bytes: int) -> None:
        mib = size_bytes / (1024 * 1024)
        self.db_label.setText(f"DB {self.tr_.number(mib, decimals=1)} MiB")

    def retranslate(self) -> None:
        pass


__all__ = ["NavigationRail", "StatusBar", "TopBar"]
