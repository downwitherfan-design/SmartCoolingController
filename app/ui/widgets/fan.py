"""Animated vector fan and cooling readouts (spec §30, §100, §101, §102).

Honesty rules baked in:

* Blade rotation is driven by the **reported RPM**.  When RPM is unavailable the
  blades stop and the widget prints "RPM Unavailable" — it never spins to look
  busy (spec §30).
* Blade angle advances from wall-clock time × RPM, so the visual speed tracks
  the real speed instead of a decorative constant.
* The fan state machine (stopped / starting / running / boosting / stalled /
  fault) is shown as text, not just colour (spec §100).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.core.enums import FanResponseState, FanState
from app.ui.design import TYPE, Theme
from app.ui.i18n import Translator
from app.ui.widgets.base import ThemedWidget, draw_glow


class FanVisualizer(ThemedWidget):
    """A large vector fan whose blades track the real RPM."""

    BLADES = 7

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self._rpm: float | None = None
        self._pwm: float | None = None
        self._target: float | None = None
        self._state = FanState.UNKNOWN
        self._response = FanResponseState.UNKNOWN
        self._angle = 0.0
        self._last_tick = 0.0
        self._sweep = 0.0
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # -- api ---------------------------------------------------------------

    def set_fan(
        self,
        *,
        rpm: float | None,
        pwm: float | None,
        target: float | None,
        state: FanState,
        response: FanResponseState = FanResponseState.UNKNOWN,
    ) -> None:
        self._rpm = rpm
        self._pwm = pwm
        self._target = target
        self._state = state
        self._response = response
        self._update_animation()
        self.update()

    def _update_animation(self) -> None:
        spinning = self._rpm is not None and self._rpm > 1 and self.theme.motion
        if spinning and not self._timer.isActive():
            import time

            self._last_tick = time.monotonic()
            self._timer.start()
        elif not spinning and self._timer.isActive():
            self._timer.stop()
            self._angle = 0.0
            self.update()

    def _tick(self) -> None:
        import time

        now = time.monotonic()
        dt = max(0.001, now - self._last_tick)
        self._last_tick = now
        if self._rpm is None:
            return
        # Degrees per second = RPM * 360 / 60.  Capped so the blades stay
        # readable at very high speeds (a strobe effect, not a lie about speed).
        degrees_per_s = min(self._rpm * 6.0, 900.0)
        self._angle = (self._angle + degrees_per_s * dt) % 360.0
        self.update()

    def stop_animation(self) -> None:
        self._timer.stop()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height()) - 16
        rect = QRectF(0, 0, side, side)
        rect.moveCenter(QPointF(self.width() / 2, self.height() / 2))
        center = rect.center()
        radius = side / 2

        color = QColor(self.theme.fan_color(self._state))
        if self.theme.show_glow and self._state in (FanState.RUNNING, FanState.BOOSTING):
            draw_glow(painter, rect, color, strength=0.18, radius_factor=1.05)

        # Outer housing ring.
        painter.setPen(QColor(self.theme.palette.border))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius - 2, radius - 2)
        painter.setPen(QColor(self.theme.palette.border_strong))
        painter.drawEllipse(center, radius - 8, radius - 8)

        self._paint_blades(painter, center, radius - 12, color)
        self._paint_hub(painter, center, radius, color)
        painter.end()

    def _paint_blades(self, painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
        blade_color = QColor(color)
        blade_color.setAlphaF(0.55 if self._state in (FanState.STOPPED, FanState.UNKNOWN) else 0.92)
        painter.save()
        painter.translate(center)
        painter.rotate(self._angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(blade_color)
        for index in range(self.BLADES):
            painter.save()
            painter.rotate(index * 360.0 / self.BLADES)
            path = QPainterPath()
            path.moveTo(0, 0)
            path.cubicTo(
                radius * 0.20, -radius * 0.28,
                radius * 0.72, -radius * 0.52,
                radius * 0.94, -radius * 0.14,
            )
            path.cubicTo(
                radius * 0.70, radius * 0.10,
                radius * 0.34, radius * 0.16,
                0, 0,
            )
            painter.drawPath(path)
            painter.restore()
        painter.restore()

    def _paint_hub(self, painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
        hub = max(16.0, radius * 0.22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme.palette.surface_raised))
        painter.drawEllipse(center, hub, hub)
        painter.setPen(QColor(self.theme.palette.border_strong))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, hub, hub)

        # Central RPM / PWM readout.
        main = self.tr_.t("dash.rpm_unavailable") if self._rpm is None else self.tr_.number(self._rpm, grouping=True)
        font = QFont(self.font())
        font.setPointSizeF(TYPE["heading"] if self._rpm is not None else TYPE["caption"])
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self.theme.palette.text) if self._rpm is not None else QColor(self.theme.palette.text_faint))
        text_rect = QRectF(center.x() - hub, center.y() - 12, hub * 2, 18)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, main)
        if self._rpm is not None:
            unit_font = QFont(self.font())
            unit_font.setPointSizeF(TYPE["caption"] - 1)
            painter.setFont(unit_font)
            painter.setPen(QColor(self.theme.palette.text_muted))
            painter.drawText(
                QRectF(center.x() - hub, center.y() + 3, hub * 2, 14),
                Qt.AlignmentFlag.AlignCenter,
                self.tr_.t("unit.rpm"),
            )

    def apply_theme(self) -> None:
        self._update_animation()


class FanReadout(ThemedWidget):
    """The numeric panel beside the fan: target/actual PWM, power, state."""

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self._rows: list[tuple[str, str, str]] = []
        self._state = FanState.UNKNOWN
        self._response = FanResponseState.UNKNOWN
        self._fault = "none"
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(
        self,
        *,
        state: FanState,
        target: float | None,
        pwm: float | None,
        voltage: float | None,
        current: float | None,
        power: float | None,
        response: FanResponseState = FanResponseState.UNKNOWN,
        fault: str = "none",
    ) -> None:
        self._state = state
        self._response = response
        self._fault = fault
        self._rows = [
            (self.tr_.t("dash.fan_target"), self._pct(target), "primary"),
            (self.tr_.t("dash.fan_pwm"), self._pct(pwm), "text"),
            (self.tr_.t("common.voltage"), self._num(voltage, "V"), "text_muted"),
            (self.tr_.t("common.current"), self._num(current, "A"), "text_muted"),
            (self.tr_.t("common.power"), self._num(power, "W"), "warm"),
        ]
        self.update()

    def _pct(self, value: float | None) -> str:
        return "—" if value is None else f"{self.tr_.number(value, decimals=0)}%"

    def _num(self, value: float | None, unit: str) -> str:
        return "—" if value is None else f"{self.tr_.number(value, decimals=2)} {unit}"

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        padding = 10

        # State header.
        state_text = self.tr_.t(f"fan.state.{self._state.value}")
        if self._fault and self._fault not in ("none", ""):
            state_text += f" · {self._fault}"
        head_font = QFont(self.font())
        head_font.setPointSizeF(TYPE["subheading"])
        head_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(head_font)
        painter.setPen(self.theme.fan_color(self._state))
        painter.drawText(
            QRectF(padding, 0, rect.width() - padding * 2, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            state_text,
        )

        if self._rows:
            row_height = (rect.height() - 28) / len(self._rows)
            label_font = QFont(self.font())
            label_font.setPointSizeF(TYPE["small"])
            value_font = QFont(self.font())
            value_font.setPointSizeF(TYPE["subheading"])
            value_font.setWeight(QFont.Weight.DemiBold)
            for index, (label, value, tone) in enumerate(self._rows):
                top = 26 + index * row_height
                painter.setFont(label_font)
                painter.setPen(QColor(self.theme.palette.text_muted))
                painter.drawText(
                    QRectF(padding, top, rect.width() - padding * 2, row_height),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
                painter.setFont(value_font)
                painter.setPen(self.color(tone))
                painter.drawText(
                    QRectF(padding, top, rect.width() - padding * 2, row_height),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    value,
                )
        painter.end()


__all__ = ["FanReadout", "FanVisualizer"]
