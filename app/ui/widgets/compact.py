"""Compact always-on-top quick view (spec §56, §117, §118, §224).

A frameless, draggable strip that shows the four numbers that matter while you
are doing something else: CPU temperature, GPU temperature, fan RPM and the
active profile.  It is the "glance, don't stare" surface.

Interaction rules that keep it feeling native:

* Drag anywhere on the surface to move it; the position is persisted.
* Double-click returns to the full window.
* A right-click menu exposes open, pause and exit.
* When the position is locked the strip stops accepting drags so it cannot be
  nudged by accident (spec §118).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.enums import ConnectionState, MetricState
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import RADIUS, SPACE
from app.ui.i18n import Translator
from app.ui.widgets.atoms import StatusBadge
from app.ui.widgets.base import ThemedWidget


class CompactView(ThemedWidget):
    """Frameless quick-glance window."""

    expand_requested = Signal()
    pause_toggled = Signal(bool)
    exit_requested = Signal()
    moved = Signal(int, int)

    def __init__(self, theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self.setObjectName("CompactView")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(360, 118)
        self._drag_origin: QPoint | None = None
        self._locked = False
        self._paused = False
        self._state = ConnectionState.DISCONNECTED.value

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        layout.setSpacing(SPACE["xs"])

        header = QHBoxLayout()
        header.setSpacing(SPACE["xs"])
        self.title = QLabel()
        self.title.setProperty("role", "caption")
        header.addWidget(self.title)
        header.addStretch(1)
        self.connection = StatusBadge(self.theme, translator, "", tone="text_faint")
        header.addWidget(self.connection)
        layout.addLayout(header)

        values = QHBoxLayout()
        values.setSpacing(SPACE["md"])
        self.cpu = self._cell(values, "CPU")
        self.gpu = self._cell(values, "GPU")
        self.fan = self._cell(values, "FAN")
        layout.addLayout(values)

        self.profile = QLabel()
        self.profile.setProperty("role", "caption")
        layout.addWidget(self.profile)

        self.retranslate()

    def _cell(self, parent_layout: QHBoxLayout, name: str) -> QLabel:
        column = QVBoxLayout()
        column.setSpacing(0)
        caption = QLabel(name)
        caption.setProperty("role", "caption")
        column.addWidget(caption)
        value = QLabel("—")
        value.setProperty("role", "mono")
        value.setStyleSheet("font-size: 17pt;")
        column.addWidget(value)
        parent_layout.addLayout(column, 1)
        return value

    # -- state -------------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        self._locked = locked

    def set_always_on_top(self, enabled: bool) -> None:
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def set_profile(self, name: str) -> None:
        self.profile.setText(f"{self.tr_.t('tray.profile')}: {name or '—'}")

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        mode = self.tr_.t("status.paused") if paused else self.tr_.t("status.live")
        self.title.setText(f"{self.tr_.t('app.name')} · {mode}")

    def set_connection(self, state: ConnectionState) -> None:
        self._state = state.value
        self.connection.set_status(state.value, self.theme.color(_state_token(state.value)))

    def update_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self.cpu.setText(_temperature(snapshot.cpu.temperature, snapshot.cpu.state, self.tr_))
        self.gpu.setText(_temperature(snapshot.gpu.temperature, snapshot.gpu.state, self.tr_))
        if snapshot.fan.available and snapshot.fan.rpm is not None:
            self.fan.setText(f"{self.tr_.number(snapshot.fan.rpm)} RPM")
        else:
            self.fan.setText("—")

    # -- interaction -------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._locked:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self._drag_origin = None
            position = self.pos()
            self.moved.emit(position.x(), position.y())
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.expand_requested.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction(self.tr_.t("tray.open"), self.expand_requested.emit)
        pause = menu.addAction(self.tr_.t("tray.pause"))
        pause.setCheckable(True)
        pause.setChecked(self._paused)
        pause.toggled.connect(self.pause_toggled.emit)
        menu.addSeparator()
        menu.addAction(self.tr_.t("tray.exit"), self.exit_requested.emit)
        menu.exec(event.globalPos())

    def apply_theme(self) -> None:
        palette = self.theme.palette
        self.setStyleSheet(
            f"""
            #CompactView {{
                background: {palette.surface_raised};
                border: 1px solid {palette.border_strong};
                border-radius: {RADIUS['lg']}px;
            }}
            """
        )

    def retranslate(self) -> None:
        self.set_paused(self._paused)
        self.profile.setText(f"{self.tr_.t('tray.profile')}: —")


def _temperature(value: float | None, state: MetricState, translator: Translator) -> str:
    if value is None or state in (MetricState.UNAVAILABLE, MetricState.ERROR):
        return "—"
    return f"{translator.number(float(value))} °C"


def _state_token(state: str) -> str:
    return {
        ConnectionState.CONNECTED.value: "success",
        ConnectionState.CONNECTING.value: "warning",
        ConnectionState.PAIRING.value: "warning",
        ConnectionState.DEGRADED.value: "warm",
        ConnectionState.STALE.value: "warm",
    }.get(state, "text_faint")


__all__ = ["CompactView"]
