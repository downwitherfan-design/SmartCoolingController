"""Interactive fan-curve editor (spec §40, §39).

Points can be dragged, added (double-click) and removed (right-click).  The
editor enforces the invariants the firmware relies on:

* points are sorted by temperature,
* the first and last points are pinned to the profile's PWM bounds,
* the curve is always monotonic in temperature (no backwards steps).

Edits emit :attr:`FanCurveEditor.curve_changed` with a validated point list so
the page can persist it; the widget itself never talks to storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.ui.design import TYPE, Theme
from app.ui.i18n import Translator
from app.ui.widgets.base import ThemedWidget

MIN_TEMP = 20.0
MAX_TEMP = 100.0
MIN_DUTY = 0.0
MAX_DUTY = 100.0


@dataclass(slots=True)
class CurvePoint:
    temperature_c: float
    duty_percent: float


class FanCurveEditor(ThemedWidget):
    """Drag-and-drop fan curve editor."""

    curve_changed = Signal(list)
    point_selected = Signal(int)

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        points: list[CurvePoint] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._points: list[CurvePoint] = points or [
            CurvePoint(35, 0),
            CurvePoint(50, 20),
            CurvePoint(60, 35),
            CurvePoint(70, 55),
            CurvePoint(80, 80),
            CurvePoint(90, 100),
        ]
        self._dragging: int | None = None
        self._hover: int | None = None
        self._selected: int | None = None
        self._read_only = False
        self.setMinimumSize(420, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    # -- api ---------------------------------------------------------------

    def set_points(self, points: list[CurvePoint]) -> None:
        self._points = [CurvePoint(p.temperature_c, p.duty_percent) for p in points]
        self._normalise()
        self.update()

    def points(self) -> list[CurvePoint]:
        return [CurvePoint(p.temperature_c, p.duty_percent) for p in self._points]

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = read_only
        self.update()

    def add_point(self, temperature_c: float | None = None, duty_percent: float | None = None) -> None:
        if self._read_only:
            return
        if temperature_c is None:
            temperature_c = (self._points[-1].temperature_c + MAX_TEMP) / 2 if self._points else 60.0
        if duty_percent is None:
            duty_percent = self._interpolate(temperature_c)
        self._points.append(CurvePoint(temperature_c, duty_percent))
        self._normalise()
        self._selected = None
        self.curve_changed.emit(self.points())
        self.update()

    def remove_selected(self) -> None:
        if self._read_only or self._selected is None:
            return
        if len(self._points) <= 2:
            return
        del self._points[self._selected]
        self._selected = None
        self._normalise()
        self.curve_changed.emit(self.points())
        self.update()

    # -- geometry ----------------------------------------------------------

    def _plot_rect(self) -> QRect:
        return self.rect().adjusted(48, 18, -20, -34)

    def _to_pixel(self, point: CurvePoint) -> QPointF:
        rect = self._plot_rect()
        x = rect.left() + (point.temperature_c - MIN_TEMP) / (MAX_TEMP - MIN_TEMP) * rect.width()
        y = rect.bottom() - (point.duty_percent - MIN_DUTY) / (MAX_DUTY - MIN_DUTY) * rect.height()
        return QPointF(x, y)

    def _to_curve(self, pos: QPointF) -> CurvePoint:
        rect = self._plot_rect()
        temp = MIN_TEMP + (pos.x() - rect.left()) / max(1.0, rect.width()) * (MAX_TEMP - MIN_TEMP)
        duty = MIN_DUTY + (rect.bottom() - pos.y()) / max(1.0, rect.height()) * (MAX_DUTY - MIN_DUTY)
        return CurvePoint(_clamp(temp, MIN_TEMP, MAX_TEMP), _clamp(duty, MIN_DUTY, MAX_DUTY))

    def _interpolate(self, temperature_c: float) -> float:
        if not self._points:
            return 0.0
        ordered = sorted(self._points, key=lambda p: p.temperature_c)
        if temperature_c <= ordered[0].temperature_c:
            return ordered[0].duty_percent
        if temperature_c >= ordered[-1].temperature_c:
            return ordered[-1].duty_percent
        for left, right in pairwise(ordered):
            if left.temperature_c <= temperature_c <= right.temperature_c:
                span = (right.temperature_c - left.temperature_c) or 1.0
                ratio = (temperature_c - left.temperature_c) / span
                return left.duty_percent + ratio * (right.duty_percent - left.duty_percent)
        return ordered[-1].duty_percent

    def _normalise(self) -> None:
        self._points.sort(key=lambda p: p.temperature_c)
        # Clamp the endpoints to the axis limits.
        if self._points:
            self._points[0].temperature_c = max(MIN_TEMP, self._points[0].temperature_c)
            self._points[-1].temperature_c = min(MAX_TEMP, self._points[-1].temperature_c)
        # Enforce monotonic duty so the firmware never sees a backwards curve.
        for index in range(1, len(self._points)):
            self._points[index].duty_percent = max(
                self._points[index].duty_percent, self._points[index - 1].duty_percent
            )

    def _point_at(self, pos: QPointF) -> int | None:
        for index, point in enumerate(self._points):
            if (self._to_pixel(point) - pos).manhattanLength() <= 14:
                return index
        return None

    # -- events ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if self._read_only:
            return
        index = self._point_at(event.position())
        if event.button() is Qt.MouseButton.LeftButton and index is not None:
            self._dragging = index
            self._selected = index
            self.point_selected.emit(index)
            self.update()
        elif event.button() is Qt.MouseButton.RightButton and index is not None:
            self._selected = index
            self.remove_selected()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is not None:
            curve = self._to_curve(event.position())
            self._points[self._dragging] = curve
            self._normalise()
            # Keep the drag index pointing at the same logical point after sort.
            self._dragging = self._points.index(
                min(self._points, key=lambda p: abs(p.temperature_c - curve.temperature_c))
            )
            self.curve_changed.emit(self.points())
            self.update()
        else:
            hover = self._point_at(event.position())
            if hover != self._hover:
                self._hover = hover
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor if hover is not None else Qt.CursorShape.CrossCursor
                )
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = None
        self.curve_changed.emit(self.points())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._read_only:
            return
        if self._point_at(event.position()) is None:
            curve = self._to_curve(event.position())
            self.add_point(curve.temperature_c, curve.duty_percent)
        super().mouseDoubleClickEvent(event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._plot_rect()

        # Grid and axis labels.
        grid_pen = QPen(QColor(self.theme.palette.border), 1)
        painter.setPen(grid_pen)
        for duty in range(0, 101, 25):
            y = rect.bottom() - duty / 100.0 * rect.height()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for temp in range(20, 101, 20):
            x = rect.left() + (temp - MIN_TEMP) / (MAX_TEMP - MIN_TEMP) * rect.width()
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        axis_font = QFont(self.font())
        axis_font.setPointSizeF(TYPE["caption"] - 1)
        painter.setFont(axis_font)
        painter.setPen(QColor(self.theme.palette.text_faint))
        for duty in range(0, 101, 25):
            y = rect.bottom() - duty / 100.0 * rect.height()
            painter.drawText(QRectF(0, y - 8, 42, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{duty}%")
        for temp in range(20, 101, 20):
            x = rect.left() + (temp - MIN_TEMP) / (MAX_TEMP - MIN_TEMP) * rect.width()
            painter.drawText(QRectF(x - 20, rect.bottom() + 4, 40, 16), Qt.AlignmentFlag.AlignCenter, f"{temp}°")

        # Curve area fill.
        if len(self._points) >= 2:
            path = QPainterPath()
            first = self._to_pixel(self._points[0])
            path.moveTo(first)
            for point in self._points[1:]:
                path.lineTo(self._to_pixel(point))
            line_path = QPainterPath(path)
            fill = QPainterPath(path)
            fill.lineTo(rect.right(), rect.bottom())
            fill.lineTo(first.x(), rect.bottom())
            fill.closeSubpath()
            from PySide6.QtGui import QLinearGradient

            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            top = QColor(self.theme.color("primary"))
            top.setAlphaF(0.22)
            bottom = QColor(self.theme.color("primary"))
            bottom.setAlphaF(0.0)
            gradient.setColorAt(0.0, top)
            gradient.setColorAt(1.0, bottom)
            painter.fillPath(fill, gradient)

            pen = QPen(QColor(self.theme.color("primary")), 2.4)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(line_path)

        # Handles.
        for index, point in enumerate(self._points):
            pos = self._to_pixel(point)
            selected = index == self._selected
            hovered = index == self._hover
            radius = 7.0 if (selected or hovered) else 5.0
            painter.setPen(QPen(QColor(self.theme.color("primary")), 2))
            painter.setBrush(QColor(self.theme.palette.surface_raised))
            painter.drawEllipse(pos, radius, radius)
            if selected:
                painter.setBrush(QColor(self.theme.color("primary")))
                painter.drawEllipse(pos, radius * 0.4, radius * 0.4)

        painter.end()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


__all__ = ["CurvePoint", "FanCurveEditor", "MAX_DUTY", "MAX_TEMP", "MIN_DUTY", "MIN_TEMP"]
