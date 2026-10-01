"""Reusable settings controls (spec §23, §24, §131, §132).

These are the building blocks for the Settings and Data Management pages:

* :class:`FormRow` — label + helper text on the left, control on the right.
* :class:`Switch` — an animated pill toggle.  Motion is *state communication*,
  not decoration: the knob travel tells the eye what changed (spec §25).
* :class:`SettingsCombo`, :class:`SettingsSlider`, :class:`SettingsSpin` —
  value controls that emit a single ``changed`` signal with a typed payload.
* :class:`FormCard` — a titled group of rows with a consistent rhythm.

Every control is keyboard reachable and exposes a visible focus ring through
the global stylesheet, so the whole settings surface works without a mouse
(spec §130).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.ui.design import SPACE
from app.ui.i18n import Translator
from app.ui.widgets.atoms import Card, SectionHeader


class FormRow(QWidget):
    """A settings row: title/description on the left, control on the right."""

    def __init__(
        self,
        theme,
        translator: Translator | None = None,
        title: str = "",
        control: QWidget | None = None,
        *,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, SPACE["sm"], 0, SPACE["sm"])
        layout.setSpacing(SPACE["lg"])

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "body")
        text_col.addWidget(self.title_label)
        self.description_label = QLabel(description)
        self.description_label.setProperty("role", "caption")
        self.description_label.setWordWrap(True)
        self.description_label.setVisible(bool(description))
        text_col.addWidget(self.description_label)
        layout.addLayout(text_col, 1)

        if control is not None:
            layout.addWidget(
                control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.control = control

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_description(self, description: str) -> None:
        self.description_label.setText(description)
        self.description_label.setVisible(bool(description))


class Switch(QAbstractButton):
    """An animated on/off pill.  Colour alone never carries the state."""

    def __init__(self, theme, *, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._knob = 1.0 if checked else 0.0
        self._animation = QPropertyAnimation(self, b"knob", self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setDuration(self._theme.duration(170))
        self._animation.setStartValue(self._knob)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _get_knob(self) -> float:
        return self._knob

    def _set_knob(self, value: float) -> None:
        self._knob = value
        self.update()

    # PySide6 exposes ``Property`` as a function at runtime but ships an
    # overload-less stub, so mypy cannot see the getter/setter form.
    knob = Property(float, _get_knob, _set_knob)  # type: ignore[call-arg]

    def set_theme(self, theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = track.height() / 2

        on_color = QColor(self._theme.color("primary"))
        off_color = QColor(self._theme.color("surface_alt"))
        track_color = QColor(on_color)
        track_color.setAlphaF(0.18 + 0.82 * self._knob)
        if self._knob == 0.0:
            track_color = off_color
        painter.setPen(QColor(self._theme.color("border_strong")))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, radius, radius)

        diameter = track.height() - 6
        x = track.left() + 3 + (track.width() - diameter - 6) * self._knob
        knob_rect = QRectF(x, track.top() + 3, diameter, diameter)
        knob_color = QColor(self._theme.color("bg"))
        if self._knob == 0.0:
            knob_color = QColor(self._theme.color("text_faint"))
        if self.hasFocus():
            painter.setPen(QColor(self._theme.color("primary")))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(knob_rect)
        painter.end()


class SettingsCombo(QComboBox):
    """A combo box whose items carry typed values and emit ``changed``."""

    changed = Signal(object)

    def __init__(
        self,
        options: list[tuple[str, Any]],
        *,
        current: Any = None,
        minimum_width: int = 190,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(minimum_width)
        for label, value in options:
            self.addItem(label, value)
        if current is not None:
            index = self.findData(current)
            if index >= 0:
                self.setCurrentIndex(index)
        self.currentIndexChanged.connect(self._emit)

    def _emit(self, _index: int) -> None:
        self.changed.emit(self.currentData())

    def set_options(self, options: list[tuple[str, Any]], *, current: Any = None) -> None:
        self.blockSignals(True)
        self.clear()
        for label, value in options:
            self.addItem(label, value)
        if current is not None:
            index = self.findData(current)
            if index >= 0:
                self.setCurrentIndex(index)
        self.blockSignals(False)

    def value(self) -> Any:
        return self.currentData()

    def set_value(self, value: Any) -> None:
        index = self.findData(value)
        if index >= 0 and index != self.currentIndex():
            self.setCurrentIndex(index)


class SettingsSlider(QWidget):
    """A slider with a live numeric readout (psychology: immediate feedback)."""

    changed = Signal(float)

    def __init__(
        self,
        *,
        minimum: float,
        maximum: float,
        step: float = 1.0,
        value: float = 0.0,
        decimals: int = 0,
        suffix: str = "",
        width: int = 260,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._decimals = decimals
        self._suffix = suffix
        self._factor = 1.0 if step >= 1 else 10.0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(width)
        self.slider.setMinimum(round(minimum * self._factor))
        self.slider.setMaximum(round(maximum * self._factor))
        self.slider.setSingleStep(max(1, round(step * self._factor)))
        self.slider.setPageStep(max(1, round(step * self._factor * 5)))
        self.slider.setValue(round(value * self._factor))
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(self.slider, 1)
        self.readout = QLabel()
        self.readout.setProperty("role", "mono")
        self.readout.setMinimumWidth(64)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.readout)
        self.slider.valueChanged.connect(self._on_change)
        self._render()

    def _on_change(self, _raw: int) -> None:
        self._render()
        self.changed.emit(self.value())

    def _render(self) -> None:
        text = f"{self.value():.{self._decimals}f}"
        if self._suffix:
            text = f"{text} {self._suffix}"
        self.readout.setText(text)

    def value(self) -> float:
        return self.slider.value() / self._factor

    def set_value(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self._factor))
        self.slider.blockSignals(False)
        self._render()


class SettingsSpin(QDoubleSpinBox):
    """A numeric field that emits ``changed`` with a float."""

    changed = Signal(float)

    def __init__(
        self,
        *,
        minimum: float,
        maximum: float,
        step: float = 1.0,
        value: float = 0.0,
        decimals: int = 0,
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setSingleStep(step)
        self.setDecimals(decimals)
        self.setValue(value)
        self.setMinimumWidth(120)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        if suffix:
            self.setSuffix(f" {suffix}")
        self.valueChanged.connect(lambda v: self.changed.emit(float(v)))

    def value(self) -> float:
        return float(super().value())


class FormCard(Card):
    """A titled card that holds a group of :class:`FormRow` widgets."""

    def __init__(
        self,
        theme,
        translator: Translator,
        title: str,
        *,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(SPACE["xxs"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["sm"])
        self.header = SectionHeader(title, description)
        layout.addWidget(self.header)
        layout.addLayout(self.rows_layout)
        self._rows: list[FormRow] = []

    def add_row(
        self,
        title: str,
        control: QWidget,
        *,
        description: str = "",
        search_text: str = "",
    ) -> FormRow:
        row = FormRow(self.theme, None, title, control, description=description)  # type: ignore[arg-type]
        row.setProperty("search", search_text.lower())
        self.rows_layout.addWidget(row)
        self._rows.append(row)
        return row

    def rows(self) -> list[FormRow]:
        return list(self._rows)


def connect(control: QWidget, handler: Callable[[Any], None]) -> None:
    """Attach a typed change handler to any settings control."""
    signal = getattr(control, "changed", None)
    if signal is None:
        signal = getattr(control, "toggled", None)
    if signal is not None:
        signal.connect(handler)


__all__ = [
    "FormCard",
    "FormRow",
    "SettingsCombo",
    "SettingsSlider",
    "SettingsSpin",
    "Switch",
    "connect",
]
