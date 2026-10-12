"""Animated gauges (spec §25 "gauge interpolation", §29).

Design notes
------------
* A gauge reads its colour from the **thermal state**, not from the raw number,
  so the same 70 °C is "warm" on one laptop and "cool" on another.
* Values animate toward their target with an exponential approach driven by one
  shared 30 fps timer that stops itself when settled — no idle CPU burn
  (spec §89).
* Colour is never the only signal: the gauge prints the state name and the
  trend under the number (spec §29, §130).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.core.enums import MetricState, ThermalState, ThermalTrend
from app.core.units import format_rate
from app.ui.design import TYPE, Theme
from app.ui.i18n import Translator
from app.ui.widgets.base import ThemedWidget, draw_arc_track, draw_fitted_text, draw_glow


class _ValueAnimator:
    """Exponential smoothing for a single scalar, with a self-stopping timer."""

    def __init__(self, widget: QWidget, *, speed: float = 0.22) -> None:
        self._widget = widget
        self._speed = speed
        self._current: float | None = None
        self._target: float | None = None
        self._timer = QTimer(widget)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._step)

    @property
    def current(self) -> float | None:
        return self._current

    def set_target(self, value: float | None) -> None:
        self._target = value
        if value is None:
            self._current = None
            self._timer.stop()
            self._widget.update()
            return
        if self._current is None:
            self._current = value
            self._widget.update()
            return
        if abs(value - self._current) < 0.05:
            self._current = value
            self._widget.update()
            return
        if not self._timer.isActive():
            self._timer.start()

    def _step(self) -> None:
        if self._current is None or self._target is None:
            self._timer.stop()
            return
        delta = self._target - self._current
        self._current += delta * self._speed
        if abs(delta) < 0.08:
            self._current = self._target
            self._timer.stop()
        self._widget.update()


class _ArcGauge(ThemedWidget):
    """Common painting for arc gauges."""

    #: Degrees of the arc sweep.  270° leaves a comfortable gap at the bottom.
    SPAN = 270.0
    START = 225.0

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        minimum: float = 0.0,
        maximum: float = 100.0,
        unit: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._min = minimum
        self._max = maximum
        self._unit = unit
        self._label = ""
        self._sublabel = ""
        self._tone = "primary"
        self._state_text = ""
        self._animator = _ValueAnimator(self)
        self._hover = False
        self.setMinimumSize(168, 168)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    # -- api ---------------------------------------------------------------

    def set_range(self, minimum: float, maximum: float) -> None:
        self._min, self._max = minimum, maximum
        self.update()

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.update()

    def set_sublabel(self, text: str) -> None:
        self._sublabel = text
        self.update()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def set_state_text(self, text: str) -> None:
        self._state_text = text
        self.update()

    def set_value(self, value: float | None, *, tone: str | None = None, state_text: str = "") -> None:
        self._animator.set_target(value)
        if tone is not None:
            self._tone = tone
        if state_text:
            self._state_text = state_text
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # -- painting ----------------------------------------------------------

    #: Vertical space reserved above the arc for the caption and below it for
    #: the trend line, so neither collides with the arc's upper/lower stroke.
    BAND = 20.0

    def _geometry(self) -> tuple[QRectF, float]:
        side = min(self.width() - 16, self.height() - 16 - 2 * self.BAND)
        side = max(24.0, side)
        rect = QRectF(0, 0, side, side)
        rect.moveCenter(QPointF(self.width() / 2, self.height() / 2))
        return rect, side

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect, side = self._geometry()
        thickness = max(9.0, side * 0.085)
        arc_rect = rect.adjusted(thickness / 2, thickness / 2, -thickness / 2, -thickness / 2)

        color = self.color(self._tone)
        track = QColor(self.theme.palette.border_strong)

        if self.theme.show_glow and self._animator.current is not None:
            fraction = self._fraction(self._animator.current)
            if fraction > 0.02:
                draw_glow(
                    painter,
                    rect,
                    color,
                    strength=0.16 + 0.14 * fraction,
                    radius_factor=0.92,
                )

        # Track then value arc.
        draw_arc_track(painter, arc_rect, self.START, -self.SPAN, track, thickness, round_cap=True)

        current = self._animator.current
        if current is not None:
            fraction = self._fraction(current)
            sweep = -self.SPAN * fraction
            if abs(sweep) > 0.5:
                draw_arc_track(painter, arc_rect, self.START, sweep, color, thickness, round_cap=True)
                # Marker dot at the leading edge.
                angle = math.radians(self.START + sweep)
                cx = arc_rect.center().x() + arc_rect.width() / 2 * math.cos(angle)
                cy = arc_rect.center().y() - arc_rect.height() / 2 * math.sin(angle)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self.theme.palette.surface))
                painter.drawEllipse(QPointF(cx, cy), thickness * 0.30, thickness * 0.30)
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx, cy), thickness * 0.20, thickness * 0.20)

        self._paint_center(painter, rect, color)
        painter.end()

    def _paint_center(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        center = rect.center()
        current = self._animator.current
        # Boxes are generous and overlap slightly; the fitting step keeps every
        # run inside its box, so a longer value or a translated label can never
        # spill over the arc the way the old magic-number rects allowed.
        left = rect.left() - 4
        width = rect.width() + 8

        value_font = QFont(self.font())
        value_font.setPointSizeF(TYPE["display"] + 2)
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setPen(QColor(self.theme.palette.text))
        painter.setFont(value_font)
        text = self._format_value(current)
        value_rect = QRectF(left, center.y() - rect.height() * 0.26, width, rect.height() * 0.36)
        draw_fitted_text(painter, text, value_rect, Qt.AlignmentFlag.AlignCenter)

        unit_font = QFont(self.font())
        unit_font.setPointSizeF(TYPE["small"])
        painter.setPen(QColor(self.theme.palette.text_muted))
        painter.setFont(unit_font)
        unit_rect = QRectF(left, center.y() + rect.height() * 0.03, width, 18)
        draw_fitted_text(painter, self._unit, unit_rect, Qt.AlignmentFlag.AlignCenter)

        if self._state_text:
            state_font = QFont(self.font())
            state_font.setPointSizeF(TYPE["caption"])
            state_font.setWeight(QFont.Weight.DemiBold)
            painter.setPen(color)
            painter.setFont(state_font)
            state_rect = QRectF(left, center.y() + rect.height() * 0.135, width, 18)
            draw_fitted_text(painter, self._state_text, state_rect, Qt.AlignmentFlag.AlignCenter)

        if self._sublabel:
            sub_font = QFont(self.font())
            sub_font.setPointSizeF(TYPE["caption"])
            painter.setPen(QColor(self.theme.palette.text_faint))
            painter.setFont(sub_font)
            sub_rect = QRectF(left, rect.bottom() + 2, width, self.BAND)
            draw_fitted_text(painter, self._sublabel, sub_rect, Qt.AlignmentFlag.AlignCenter)

        if self._label:
            label_font = QFont(self.font())
            label_font.setPointSizeF(TYPE["caption"])
            label_font.setWeight(QFont.Weight.DemiBold)
            painter.setPen(QColor(self.theme.palette.text_muted))
            painter.setFont(label_font)
            label_rect = QRectF(left, rect.top() - self.BAND, width, self.BAND)
            draw_fitted_text(painter, self._label.upper(), label_rect, Qt.AlignmentFlag.AlignCenter)

    def _fraction(self, value: float) -> float:
        span = (self._max - self._min) or 1.0
        return max(0.0, min(1.0, (value - self._min) / span))

    def _format_value(self, value: float | None) -> str:
        if value is None:
            return "—"
        return self.tr_.number(value, decimals=0)


class TemperatureGauge(_ArcGauge):
    """CPU/GPU temperature gauge with thermal-state colouring and trend."""

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, minimum=20.0, maximum=100.0, unit="°C", parent=parent)
        self._state = ThermalState.NORMAL
        self._trend = ThermalTrend.STABLE
        self._rate: float | None = None

    def set_reading(
        self,
        temperature: float | None,
        *,
        state: ThermalState = ThermalState.NORMAL,
        trend: ThermalTrend = ThermalTrend.STABLE,
        rate: float | None = None,
        metric_state: MetricState = MetricState.VALID,
    ) -> None:
        self._state = state
        self._trend = trend
        self._rate = rate
        tone = self.theme.thermal_color(state)
        if metric_state in (MetricState.UNAVAILABLE, MetricState.ERROR):
            tone = self.theme.color("text_faint")
        elif metric_state is MetricState.SIMULATED:
            tone = self.theme.color("secondary")
        state_text = self.tr_.t(f"state.{state.value}")
        if metric_state is MetricState.SIMULATED:
            state_text += " · " + self.tr_.t("common.simulated")
        elif metric_state is MetricState.STALE:
            state_text += " · " + self.tr_.t("common.stale")
        self.set_value(temperature, tone=tone, state_text=state_text)
        self.set_sublabel(self._trend_text())

    def _trend_text(self) -> str:
        arrow = {
            ThermalTrend.RISING: "▲",
            ThermalTrend.FALLING: "▼",
            ThermalTrend.STABLE: "◆",
        }.get(self._trend, "◆")
        if self._rate is None:
            return arrow
        return f"{arrow} {format_rate(self._rate)}"

    def _format_value(self, value: float | None) -> str:
        if value is None:
            return "—"
        return self.tr_.number(value, decimals=0)


class UsageGauge(_ArcGauge):
    """Percent gauge for CPU/GPU/RAM utilisation."""

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, minimum=0.0, maximum=100.0, unit="%", parent=parent)

    def set_usage(
        self,
        percent: float | None,
        *,
        tone: str = "primary",
        metric_state: MetricState = MetricState.VALID,
    ) -> None:
        resolved = tone
        state_text = ""
        if metric_state is MetricState.SIMULATED:
            resolved = "secondary"
            state_text = self.tr_.t("common.simulated")
        elif metric_state is MetricState.STALE:
            resolved = "warning"
            state_text = self.tr_.t("common.stale")
        elif metric_state in (MetricState.UNAVAILABLE, MetricState.ERROR):
            resolved = "text_faint"
        self.set_value(percent, tone=resolved, state_text=state_text)


class RingGauge(_ArcGauge):
    """Compact ring used for storage, battery and pad metrics."""

    SPAN = 300.0
    START = 210.0

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        maximum: float = 100.0,
        unit: str = "%",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, minimum=0.0, maximum=maximum, unit=unit, parent=parent)
        self.setMinimumSize(120, 120)

    def set_ring(self, value: float | None, *, tone: str = "primary", state_text: str = "") -> None:
        self.set_value(value, tone=tone, state_text=state_text)


class PowerMetric(ThemedWidget):
    """A numeric power/clock tile drawn as a compact horizontal readout."""

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        label: str = "",
        unit: str = "W",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._label = label
        self._unit = unit
        self._value: float | None = None
        self._animator = _ValueAnimator(self, speed=0.25)
        self._tone = "text"
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_metric(
        self,
        value: float | None,
        *,
        unit: str | None = None,
        tone: str = "text",
    ) -> None:
        self._animator.set_target(value)
        if unit is not None:
            self._unit = unit
        self._tone = tone
        self.update()

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        label_font = QFont(self.font())
        label_font.setPointSizeF(TYPE["caption"])
        label_font.setWeight(QFont.Weight.DemiBold)
        painter.setPen(QColor(self.theme.palette.text_faint))
        painter.setFont(label_font)
        draw_fitted_text(
            painter,
            self._label.upper(),
            QRectF(rect.left(), rect.top(), rect.width(), 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        value_font = QFont(self.font())
        value_font.setPointSizeF(TYPE["heading"] + 1)
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setPen(self.color(self._tone))
        painter.setFont(value_font)
        value = self._animator.current
        text = "—" if value is None else self.tr_.number(value, decimals=1)
        draw_fitted_text(
            painter,
            text,
            QRectF(rect.left(), rect.top() + 16, rect.width() - 30, rect.height() - 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        unit_font = QFont(self.font())
        unit_font.setPointSizeF(TYPE["caption"])
        painter.setPen(QColor(self.theme.palette.text_muted))
        painter.setFont(unit_font)
        draw_fitted_text(
            painter,
            self._unit,
            QRectF(rect.right() - 34, rect.top() + 16, 34, rect.height() - 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        painter.end()


class ThermalTrendStrip(ThemedWidget):
    """A slim band showing CPU vs GPU trend arrows and rates (spec §18)."""

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self._entries: list[tuple[str, ThermalTrend, float | None, str]] = []
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_trends(
        self,
        entries: list[tuple[str, ThermalTrend, float | None, str]],
    ) -> None:
        """entries: (name, trend, rate_c_per_min, tone)"""
        self._entries = entries
        self.update()

    def paintEvent(self, event) -> None:
        if not self._entries:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        count = len(self._entries)
        cell = self.rect().width() / count
        for index, (name, trend, rate, tone) in enumerate(self._entries):
            left = index * cell
            color = self.color(tone)
            arrow = {
                ThermalTrend.RISING: "▲",
                ThermalTrend.FALLING: "▼",
                ThermalTrend.STABLE: "◆",
            }.get(trend, "◆")

            name_font = QFont(self.font())
            name_font.setPointSizeF(TYPE["caption"])
            painter.setPen(QColor(self.theme.palette.text_faint))
            painter.setFont(name_font)
            draw_fitted_text(
                painter,
                name.upper(),
                QRectF(left + 4, 2, cell - 8, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )

            value_font = QFont(self.font())
            value_font.setPointSizeF(TYPE["subheading"])
            value_font.setWeight(QFont.Weight.DemiBold)
            painter.setPen(color)
            painter.setFont(value_font)
            text = arrow if rate is None else f"{arrow} {format_rate(rate)}"
            draw_fitted_text(
                painter,
                text,
                QRectF(left + 4, 18, cell - 8, self.height() - 20),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )

            if index < count - 1:
                painter.setPen(QPen(QColor(self.theme.palette.border), 1))
                painter.drawLine(
                    QPointF(left + cell - 1, 8),
                    QPointF(left + cell - 1, self.height() - 8),
                )
        painter.end()


__all__ = [
    "PowerMetric",
    "RingGauge",
    "TemperatureGauge",
    "ThermalTrendStrip",
    "UsageGauge",
]
