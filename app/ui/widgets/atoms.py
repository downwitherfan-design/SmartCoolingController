"""Reusable UI atoms (spec §24).

Small, composable widgets with no business logic: cards, badges, rows, headers,
toggles and segmented controls.  Pages assemble these instead of hand-rolling
layouts, which is what keeps spacing and typography consistent across 12 pages.

Accessibility notes (spec §130):

* :class:`StatusBadge` always shows a text label — colour is reinforcement, not
  the message.
* Interactive controls keep a minimum 28 px height and visible focus.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.enums import (
    AlertSeverity,
    ConnectionState,
    MetricState,
    ThermalState,
)
from app.models.sensors import SensorDescriptor
from app.ui.design import SPACE, TYPE, Theme
from app.ui.i18n import Translator
from app.ui.icons import make_icon, paint_icon
from app.ui.widgets.base import ThemedWidget


def _label(text: str, role: str = "body", parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", role)
    return label


def _format_value(value: float) -> str:
    """Compact, locale-neutral number for dense rows (no trailing noise)."""
    if float(value).is_integer():
        return f"{int(value):,}"
    magnitude = abs(value)
    decimals = 2 if magnitude < 10 else 1
    return f"{value:,.{decimals}f}"


class Card(QFrame):
    """A layered surface used for every panel and metric tile."""

    def __init__(self, parent: QWidget | None = None, *, variant: str = "card") -> None:
        super().__init__(parent)
        self.setProperty("card", variant if variant != "card" else "true")
        if variant == "card":
            self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)


class SectionHeader(QWidget):
    """Title + optional subtitle + optional trailing widget."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        trailing: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self.title = _label(title, "title")
        text_col.addWidget(self.title)
        self.subtitle = _label(subtitle, "caption")
        self.subtitle.setVisible(bool(subtitle))
        text_col.addWidget(self.subtitle)
        layout.addLayout(text_col)
        layout.addStretch(1)
        if trailing is not None:
            layout.addWidget(trailing)

    def set_text(self, title: str, subtitle: str = "") -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None, *, vertical: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("role", "vdivider" if vertical else "divider")
        self.setFrameShape(QFrame.Shape.NoFrame)
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class StatusBadge(ThemedWidget):
    """Pill badge with a dot, a label and a theme colour.

    The label is always present so the meaning survives greyscale printing and
    colour-vision differences (spec §130).
    """

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        text: str = "",
        *,
        tone: str = "text_muted",
        show_dot: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._text = text
        self._tone = tone
        self._show_dot = show_dot
        self.setMinimumHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_status(self, text: str, tone: str | QColor) -> None:
        self._text = text
        self._tone = tone if isinstance(tone, str) else tone.name()
        self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def _tone_color(self) -> QColor:
        if self._tone.startswith("#") or self._tone.startswith("rgba"):
            return QColor(self._tone)
        return QColor(self.theme.color(self._tone))

    def sizeHint(self) -> QSize:
        width = self.fontMetrics().horizontalAdvance(self._text) + (34 if self._show_dot else 20)
        return QSize(width, 22)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = self._tone_color()
        rect = self.rect().adjusted(0, 0, -1, -1)
        background = QColor(color)
        background.setAlphaF(0.16)
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.fillPath(path, background)
        painter.setPen(QColor(color.red(), color.green(), color.blue(), 120))
        painter.drawPath(path)

        text_x = rect.left() + 10
        if self._show_dot:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(rect.left() + 10, rect.center().y() - 3, 6, 6)
            text_x = rect.left() + 22
        font = QFont(self.font())
        font.setPointSizeF(TYPE["caption"])
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            rect.adjusted(text_x - rect.left(), 0, -10, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )
        painter.end()


class ConnectionBadge(StatusBadge):
    """Badge specialised for :class:`ConnectionState`."""

    def set_connection(self, state: ConnectionState) -> None:
        self.set_status(self.tr_.t(f"conn.{state.value}"), self.theme.connection_color(state))


class ThermalBadge(StatusBadge):
    def set_thermal(self, state: ThermalState) -> None:
        self.set_status(self.tr_.t(f"state.{state.value}"), self.theme.thermal_color(state))


class MetricStateBadge(StatusBadge):
    """Communicates data trust: measured / simulated / stale / unavailable."""

    def set_metric_state(self, state: MetricState) -> None:
        if state is MetricState.SIMULATED:
            self.set_status(self.tr_.t("common.simulated"), self.theme.color("secondary"))
        elif state is MetricState.STALE:
            self.set_status(self.tr_.t("common.stale"), self.theme.color("warning"))
        elif state is MetricState.ERROR:
            self.set_status(self.tr_.t("health.error"), self.theme.color("danger"))
        elif state is MetricState.UNAVAILABLE:
            self.set_status(self.tr_.t("common.unavailable"), self.theme.color("text_faint"))
        elif state is MetricState.DERIVED:
            self.set_status(self.tr_.t("common.derived"), self.theme.color("secondary"))
        else:
            self.set_status(self.tr_.t("common.measured"), self.theme.color("success"))


class Sparkline(ThemedWidget):
    """Tiny trend line drawn behind a metric value."""

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._values: list[float] = []
        self._color_token = "primary"
        self.setMinimumHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: list[float], *, color_token: str = "primary") -> None:
        self._values = values[-180:]
        self._color_token = color_token
        self.update()

    def paintEvent(self, event) -> None:
        if len(self._values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 3, -1, -3)
        low, high = min(self._values), max(self._values)
        span = (high - low) or 1.0
        step = rect.width() / (len(self._values) - 1)
        path = QPainterPath()
        for index, value in enumerate(self._values):
            x = rect.left() + index * step
            y = rect.bottom() - (value - low) / span * rect.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        color = self.color(self._color_token)
        from PySide6.QtGui import QLinearGradient

        fill = QPainterPath(path)
        fill.lineTo(rect.right(), rect.bottom())
        fill.lineTo(rect.left(), rect.bottom())
        fill.closeSubpath()
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = QColor(color)
        top.setAlphaF(0.28)
        bottom = QColor(color)
        bottom.setAlphaF(0.0)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillPath(fill, gradient)
        painter.setPen(QColor(color))
        painter.drawPath(path)
        painter.end()


class MetricCard(ThemedWidget):
    """A metric tile: label, value, unit, state badge and optional sparkline.

    The value is rendered by a custom ``paintEvent`` so the number can animate
    smoothly (spec §25 "number transitions") without fighting Qt layout.
    """

    pin_toggled = Signal(bool)

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        title: str = "",
        unit: str = "",
        icon_name: str = "thermometer",
        value_role: str = "value",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self.setObjectName("card")
        self._title = title
        self._unit = unit
        self._icon_name = icon_name
        self._value_text = "—"
        self._value_token = "text"
        self._subtitle = ""
        self._sparkline: Sparkline | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["sm"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["sm"])
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        head.addWidget(self.icon_label)
        self.title_label = _label(title, "subheading")
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.badge = MetricStateBadge(theme, translator, "")
        self.badge.setVisible(False)
        head.addWidget(self.badge)
        self.pin_button = QPushButton()
        self.pin_button.setProperty("variant", "ghost")
        self.pin_button.setFixedSize(24, 24)
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip(translator.t("sensors.pin"))
        self.pin_button.clicked.connect(self.pin_toggled.emit)
        head.addWidget(self.pin_button)
        layout.addLayout(head)

        self.value_label = _label("—", "display")
        self.value_label.setMinimumHeight(42)
        layout.addWidget(self.value_label)

        self.footer = QHBoxLayout()
        self.footer.setSpacing(SPACE["sm"])
        self.subtitle_label = _label("", "caption")
        self.footer.addWidget(self.subtitle_label)
        self.footer.addStretch(1)
        self.unit_label = _label(unit, "caption")
        self.footer.addWidget(self.unit_label)
        layout.addLayout(self.footer)

        self._sparkline = Sparkline(theme, translator)
        self._sparkline.setVisible(False)
        layout.addWidget(self._sparkline)
        self.apply_theme()

    # -- api ---------------------------------------------------------------

    def set_title(self, title: str) -> None:
        self._title = title
        self.title_label.setText(title)

    def set_icon(self, name: str, token: str = "primary") -> None:
        self._icon_name = name
        self.apply_theme()

    def set_value(
        self,
        text: str,
        *,
        token: str = "text",
        subtitle: str = "",
        state: MetricState | None = None,
    ) -> None:
        self._value_text = text
        self._value_token = token
        self._subtitle = subtitle
        self.value_label.setText(text)
        self.value_label.setStyleSheet(f"color: {self.theme.color(token)};")
        self.subtitle_label.setText(subtitle)
        if state is not None:
            self.badge.setVisible(True)
            self.badge.set_metric_state(state)

    def set_spark(self, values: list[float], token: str = "primary") -> None:
        sparkline = self._sparkline
        if sparkline is None:
            return
        sparkline.setVisible(bool(values))
        sparkline.set_values(values, color_token=token)

    def set_pinned(self, pinned: bool) -> None:
        self.pin_button.setChecked(pinned)

    def apply_theme(self) -> None:
        self.icon_label.setPixmap(
            make_icon(self._icon_name, self.theme.color("primary"), 18).pixmap(18, 18)
        )
        self.pin_button.setIcon(self.icon("pin", "text_faint", 14))
        self.value_label.setStyleSheet(f"color: {self.theme.color(self._value_token)};")


class KeyValueRow(QWidget):
    """A label/value row used in detail panels and diagnostics."""

    def __init__(
        self,
        key: str,
        value: str = "—",
        *,
        parent: QWidget | None = None,
        mono: bool = False,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(SPACE["md"])
        self.key_label = _label(key, "muted")
        self.key_label.setMinimumWidth(120)
        layout.addWidget(self.key_label)
        layout.addStretch(1)
        self.value_label = _label(value, "mono" if mono else "body")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, token: str | None = None) -> None:
        self.value_label.setText(value)
        if token:
            self.value_label.setStyleSheet(f"color: {QColor(token).name()};")

    def set_key(self, key: str) -> None:
        self.key_label.setText(key)


class SensorRow(QWidget):
    """One row in the sensor explorer, with an optional favourite star."""

    favorite_toggled = Signal(str, bool)

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        name: str,
        kind: str,
        value: str,
        unit: str,
        state_text: str,
        source: str,
        tone: str,
        sensor_id: str = "",
        favorite: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.sensor_id = sensor_id
        self.sensor: SensorDescriptor | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        layout.setSpacing(SPACE["md"])

        self.name_label = _label(name, "body")
        self.kind_label = _label(f"{kind} · {source}", "caption")
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        name_col.addWidget(self.name_label)
        name_col.addWidget(self.kind_label)
        layout.addLayout(name_col, 3)
        layout.addStretch(1)

        self.value_label = _label(value, "mono")
        self.value_label.setStyleSheet(f"color: {tone};")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.value_label)

        unit_label = _label(unit, "caption")
        unit_label.setMinimumWidth(34)
        layout.addWidget(unit_label)

        self.state_badge = StatusBadge(theme, translator, state_text, tone=tone)
        layout.addWidget(self.state_badge)

        self.favorite_button = QPushButton()
        self.favorite_button.setProperty("variant", "ghost")
        self.favorite_button.setCheckable(True)
        self.favorite_button.setChecked(favorite)
        self.favorite_button.setFixedSize(26, 26)
        self.favorite_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.favorite_button.setToolTip(translator.t("sensors.favorites"))
        self.favorite_button.setIcon(
            make_icon("star", theme.color("warning" if favorite else "text_faint"), 14)
        )
        self.favorite_button.toggled.connect(self._on_favorite)
        layout.addWidget(self.favorite_button)

    def _on_favorite(self, checked: bool) -> None:
        self.favorite_button.setIcon(
            make_icon("star", self.theme.color("warning" if checked else "text_faint"), 14)
        )
        if self.sensor_id:
            self.favorite_toggled.emit(self.sensor_id, checked)

    def set_sensor(self, sensor: SensorDescriptor) -> None:
        """Update the row from a fresh :class:`SensorDescriptor`."""
        self.sensor = sensor
        tone = self.theme.metric_color(sensor.state)
        value = "—" if sensor.current is None else _format_value(sensor.current)
        self.update_value(value, sensor.unit or "", str(getattr(sensor.state, "value", sensor.state)), tone)

    def update_value(self, value: str, unit: str, state_text: str, tone: str) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color: {tone};")
        self.state_badge.set_status(state_text, tone)


class AlertItem(QFrame):
    """A single alert card with a severity stripe."""

    clicked = Signal(int)

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        severity: AlertSeverity,
        title: str,
        message: str,
        meta: str,
        dismissed: bool = False,
        alert_id: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", "flat")
        self._tone = theme.severity_color(severity)
        self._alert_id = alert_id
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["md"])

        self._stripe = QFrame()
        self._stripe.setFixedWidth(3)
        self._stripe.setStyleSheet(f"background: {self._tone}; border-radius: 2px;")
        layout.addWidget(self._stripe)

        col = QVBoxLayout()
        col.setSpacing(2)
        title_label = _label(title, "heading")
        if dismissed:
            title_label.setProperty("dismissed", "true")
        col.addWidget(title_label)
        body = _label(message, "muted")
        body.setWordWrap(True)
        col.addWidget(body)
        col.addWidget(_label(meta, "caption"))
        layout.addLayout(col, 1)

        icon_label = QLabel()
        icon_label.setPixmap(
            make_icon("warning" if severity is not AlertSeverity.INFORMATION else "info", self._tone, 18).pixmap(18, 18)
        )
        icon_label.setFixedSize(20, 20)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._alert_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def selected(self) -> bool:
        return self._selected


class ProfileSelector(QWidget):
    """Segmented control for cooling profiles."""

    changed = Signal(str)

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        profiles: list[tuple[str, str]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self._buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])
        for key, label in profiles:
            button = QPushButton(label)
            button.setProperty("segmented", "true")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self._select(k))
            layout.addWidget(button)
            self._buttons[key] = button
        layout.addStretch(1)

    def _select(self, key: str) -> None:
        self.set_active(key)
        self.changed.emit(key)

    def set_active(self, key: str) -> None:
        for name, button in self._buttons.items():
            button.setChecked(name == key)


class SegmentedControl(QWidget):
    """Generic segmented control."""

    changed = Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])
        for key, label in options:
            button = QPushButton(label)
            button.setProperty("segmented", "true")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self._select(k))
            layout.addWidget(button)
            self._buttons[key] = button
        layout.addStretch(1)

    def _select(self, key: str) -> None:
        self.set_active(key)
        self.changed.emit(key)

    def set_active(self, key: str) -> None:
        for name, button in self._buttons.items():
            button.setChecked(name == key)

    def set_label(self, key: str, label: str) -> None:
        if key in self._buttons:
            self._buttons[key].setText(label)


class EmptyState(ThemedWidget):
    """A friendly empty state (spec §107) — never a blank panel."""

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        icon_name: str = "info",
        title: str = "",
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._icon_name = icon_name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xxl"], SPACE["xl"], SPACE["xxl"])
        layout.setSpacing(SPACE["sm"])
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(48, 48)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title_label = _label(title or translator.t("common.empty"), "heading")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        self.hint_label = _label(hint, "caption")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(bool(hint))
        layout.addWidget(self.hint_label)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.icon_label.setPixmap(
            make_icon(self._icon_name, self.theme.color("text_faint"), 44).pixmap(44, 44)
        )

    def set_text(self, title: str, hint: str = "") -> None:
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))


class IconButton(QPushButton):
    """A flat icon-only button sized for toolbars."""

    def __init__(
        self,
        theme: Theme,
        icon_name: str,
        *,
        tooltip: str = "",
        size: int = 30,
        icon_size: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._theme = theme
        self._icon_size = icon_size
        self.setFixedSize(size, size)
        self.setProperty("variant", "ghost")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self._refresh()

    def _refresh(self) -> None:
        self.setIcon(make_icon(self._icon_name, self._theme.color("text_muted"), self._icon_size))

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._refresh()


__all__ = [
    "AlertItem",
    "Card",
    "ConnectionBadge",
    "Divider",
    "EmptyState",
    "IconButton",
    "KeyValueRow",
    "MetricCard",
    "MetricStateBadge",
    "ProfileSelector",
    "SectionHeader",
    "SegmentedControl",
    "SensorRow",
    "Sparkline",
    "StatusBadge",
    "ThermalBadge",
    "paint_icon",
]
