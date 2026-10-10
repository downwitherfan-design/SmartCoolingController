"""Interactive system schematic (spec §31, §32, §98).

A vector diagram of the whole signal path:

    Laptop → Wi-Fi → ESP32 → PWM → 4-wire Fan → TACH → ESP32 → Wi-Fi → Windows

* Nodes are hit-testable; clicking one emits :attr:`Schematic.node_clicked`
  with its key so the page can show a detail panel (spec §32).
* Connections animate travelling particles to show direction and liveness
  (spec §31, §98).  Particle speed is tied to the real data rate, and the
  animation stops entirely when motion is disabled or the link is idle.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.core.enums import ConnectionState, FanState
from app.ui.design import TYPE, Theme
from app.ui.i18n import Translator
from app.ui.widgets.base import ThemedWidget, draw_glow, paint_icon


class _Node:
    __slots__ = ("h", "icon", "key", "label", "tone", "w", "x", "y")

    def __init__(self, key: str, label: str, icon: str, x: float, y: float, w: float, h: float, tone: str) -> None:
        self.key = key
        self.label = label
        self.icon = icon
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.tone = tone

    def rect(self) -> QRectF:
        return QRectF(self.x, self.y, self.w, self.h)

    def center(self) -> QPointF:
        return QPointF(self.x + self.w / 2, self.y + self.h / 2)


class _Link:
    __slots__ = ("bidirectional", "dst", "label", "live", "phase", "src", "tone")

    def __init__(self, src: str, dst: str, label: str, tone: str, bidirectional: bool = False) -> None:
        self.src = src
        self.dst = dst
        self.label = label
        self.tone = tone
        self.bidirectional = bidirectional
        self.live = True
        self.phase = 0.0


#: Layout on a 0..1000 × 0..560 virtual canvas; scaled to the widget at paint.
_NODE_SPECS = [
    ("laptop", "Laptop", "cpu", 30, 60, 180, 92, "primary"),
    ("telemetry", "Telemetry", "telemetry", 30, 250, 180, 92, "info"),
    ("wifi", "Wi-Fi", "wifi", 300, 155, 150, 92, "info"),
    ("esp32", "ESP32", "esp32", 540, 155, 170, 92, "secondary"),
    ("pwm", "PWM", "fan", 780, 60, 170, 88, "primary"),
    ("fan", "4-Wire Fan", "fan", 780, 250, 170, 92, "success"),
    ("tach", "TACH", "sensors", 540, 380, 170, 88, "warm"),
    ("ina219", "INA219", "power", 300, 380, 150, 88, "warm"),
    ("ds18b20", "DS18B20", "thermometer", 30, 400, 180, 88, "warm"),
]

_LINK_SPECS: list[tuple[str, str, str, str, bool]] = [
    ("laptop", "telemetry", "", "primary", False),
    ("telemetry", "wifi", "telemetry", "primary", False),
    ("wifi", "esp32", "Wi-Fi", "info", True),
    ("esp32", "pwm", "PWM", "secondary", False),
    ("pwm", "fan", "drive", "primary", False),
    ("fan", "tach", "tach", "success", False),
    ("tach", "esp32", "RPM", "warm", True),
    ("ina219", "esp32", "I/V/P", "warm", True),
    ("ds18b20", "esp32", "temp", "warm", True),
]

#: How a connection state is drawn.  ``healthy``/``degraded`` move particles
#: (degraded at a reduced speed); ``stale`` and ``idle`` are static.
_LINK_MODE_BY_STATE: dict[ConnectionState, str] = {
    ConnectionState.CONNECTED: "healthy",
    ConnectionState.DEGRADED: "degraded",
    ConnectionState.STALE: "stale",
}

#: Alpha of an idle/degraded link stroke, so a non-live edge reads as inactive.
_LINK_ALPHA = {"healthy": 0.75, "degraded": 0.45, "stale": 0.30, "idle": 0.22}


class Schematic(ThemedWidget):
    """The interactive, animated system diagram."""

    node_clicked = Signal(str)

    def __init__(self, theme: Theme, translator: Translator, *, parent: QWidget | None = None) -> None:
        super().__init__(theme, translator, parent)
        self._nodes = {spec[0]: _Node(*spec) for spec in _NODE_SPECS}
        self._links = [_Link(*spec) for spec in _LINK_SPECS]
        self._hover_key: str | None = None
        self._active_key: str | None = None
        self._mode = "idle"
        self._state = ConnectionState.DISCONNECTED
        self._fan_running = False
        self._fan_rpm: float | None = None
        self._last_tick = time.monotonic()
        self._particles: list[tuple[str, float]] = []
        self._dpr = 1.0
        self.setMinimumSize(560, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # -- state -------------------------------------------------------------

    def set_link_state(self, state: ConnectionState, *, fan_state: FanState, rpm: float | None) -> None:
        # Map the link's real health onto the animation: a link that is merely
        # up (DEGRADED) moves slowly, a link that stopped delivering (STALE)
        # freezes and dims, and only a live link moves at full speed.
        self._mode = _LINK_MODE_BY_STATE.get(state, "idle")
        self._state = state
        self._fan_running = fan_state in (FanState.RUNNING, FanState.BOOSTING, FanState.STARTING)
        self._fan_rpm = rpm
        moving = self._mode in ("healthy", "degraded")
        for link in self._links:
            if link.src in ("wifi", "esp32", "telemetry", "tach", "ina219", "ds18b20"):
                link.live = moving
        self._update_animation()
        self.update()

    def _update_animation(self) -> None:
        should_run = self.theme.motion and self._mode in ("healthy", "degraded")
        if should_run and not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()
            self.update()

    def stop_animation(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = max(0.001, now - self._last_tick)
        self._last_tick = now
        speed = 0.5
        if self._fan_rpm:
            speed = max(0.35, min(1.6, self._fan_rpm / 2200.0))
        if self._mode == "degraded":
            speed *= 0.35
        for link in self._links:
            if link.live:
                link.phase = (link.phase + dt * speed) % 1.0
        self.update()

    # -- interaction -------------------------------------------------------

    def _scale(self) -> tuple[float, float, float]:
        sx = self.width() / 1000.0
        sy = self.height() / 560.0
        scale = min(sx, sy)
        offset_x = (self.width() - 1000.0 * scale) / 2
        offset_y = (self.height() - 560.0 * scale) / 2
        return scale, offset_x, offset_y

    def _node_at(self, pos: QPointF) -> str | None:
        scale, ox, oy = self._scale()
        for key, node in self._nodes.items():
            rect = QRectF(
                ox + node.x * scale,
                oy + node.y * scale,
                node.w * scale,
                node.h * scale,
            )
            if rect.contains(pos):
                return key
        return None

    def mouseMoveEvent(self, event) -> None:
        key = self._node_at(event.position())
        if key != self._hover_key:
            self._hover_key = key
            self.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        key = self._node_at(event.position())
        if key:
            self._active_key = key
            self.node_clicked.emit(key)
            self.update()
        super().mousePressEvent(event)

    def set_active_node(self, key: str | None) -> None:
        self._active_key = key
        self.update()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale, ox, oy = self._scale()
        painter.translate(ox, oy)
        painter.scale(scale, scale)

        for link in self._links:
            self._paint_link(painter, link)
        for key, node in self._nodes.items():
            self._paint_node(painter, node, key)
        painter.end()

    def _paint_link(self, painter: QPainter, link: _Link) -> None:
        src = self._nodes[link.src]
        dst = self._nodes[link.dst]
        start = src.center()
        end = dst.center()
        # Trim the line to the node borders so it does not run under the card.
        start, end = _trim(start, end, src, dst)

        tone = self.theme.color(link.tone)
        color = QColor(tone)
        alpha = _LINK_ALPHA["healthy" if link.live else self._mode]
        color.setAlphaF(alpha)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        path = QPainterPath(start)
        mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
        path.quadTo(mid, end)
        painter.drawPath(path)

        if link.live:
            self._paint_particles(painter, start, mid, end, tone, link)

        if link.label:
            font = QFont(self.font())
            font.setPointSizeF(TYPE["caption"] - 1)
            painter.setFont(font)
            label_color = QColor(self.theme.palette.text_faint)
            painter.setPen(label_color)
            painter.drawText(
                QRectF(mid.x() - 40, mid.y() - 18, 80, 16),
                Qt.AlignmentFlag.AlignCenter,
                self._link_label(link.label),
            )

    def _link_label(self, label: str) -> str:
        """Map a schematic edge tag to a translation, keeping engineering marks."""
        alias = {
            "telemetry": "field.telemetry",
            "wi-fi": "field.wifi",
            "pwm": "field.pwm",
            "tach": "field.tach",
            "rpm": "field.rpm",
            "temp": "field.temperature",
            "drive": "link.drive",
            "i/v/p": "link.ivp",
        }
        key = alias.get(label.lower(), f"link.{label.lower()}")
        translated = self.tr_.t(key)
        return translated if translated != key else label

    def _paint_particles(
        self,
        painter: QPainter,
        start: QPointF,
        mid: QPointF,
        end: QPointF,
        tone: str,
        link: _Link,
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        color = QColor(tone)
        for offset in (0.0, 0.5):
            t = (link.phase + offset) % 1.0
            point = _quad_point(start, mid, end, t)
            radius = 3.0
            glow = QColor(color)
            glow.setAlphaF(0.30)
            painter.setBrush(glow)
            painter.drawEllipse(point, radius * 2.2, radius * 2.2)
            painter.setBrush(color)
            painter.drawEllipse(point, radius, radius)

    def _paint_node(self, painter: QPainter, node: _Node, key: str) -> None:
        rect = node.rect()
        hovered = key == self._hover_key
        active = key == self._active_key
        tone = self.theme.color(node.tone)
        color = QColor(tone)

        if active and self.theme.show_glow:
            draw_glow(painter, rect, color, strength=0.28, radius_factor=1.15)

        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        surface = QColor(self.theme.palette.surface_raised)
        painter.fillPath(path, surface)

        border = QColor(color)
        border.setAlphaF(1.0 if (hovered or active) else 0.45)
        pen = QPen(border, 2.0 if (hovered or active) else 1.4)
        painter.setPen(pen)
        painter.drawPath(path)

        icon_rect = QRectF(rect.left() + 16, rect.top() + 16, 30, 30)
        paint_icon(painter, node.icon, icon_rect, tone, stroke_width=1.8)

        title_font = QFont(self.font())
        title_font.setPointSizeF(TYPE["subheading"])
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(self.theme.palette.text))
        title_key = f"field.{key.lower()}"
        title = self.tr_.t(title_key)
        if title == title_key:
            title = node.label
        painter.drawText(
            QRectF(rect.left() + 56, rect.top() + 14, rect.width() - 68, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )

        subtitle = self._node_subtitle(key)
        if subtitle:
            sub_font = QFont(self.font())
            sub_font.setPointSizeF(TYPE["caption"])
            painter.setFont(sub_font)
            painter.setPen(QColor(self.theme.palette.text_muted))
            painter.drawText(
                QRectF(rect.left() + 56, rect.top() + 38, rect.width() - 68, rect.height() - 46),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                subtitle,
            )

    def _node_subtitle(self, key: str) -> str:
        if key in ("esp32", "wifi"):
            return self.tr_.t(f"conn.{self._state.value}")
        if key == "fan":
            if self._fan_rpm is None:
                return self.tr_.t("dash.rpm_unavailable")
            return f"{self.tr_.number(self._fan_rpm, grouping=True)} RPM"
        return ""

    def apply_theme(self) -> None:
        self._update_animation()


def _trim(start: QPointF, end: QPointF, src: _Node, dst: _Node) -> tuple[QPointF, QPointF]:
    """Shorten a link so it stops at each node's border."""
    direction = QPointF(end.x() - start.x(), end.y() - start.y())
    length = math.hypot(direction.x(), direction.y()) or 1.0
    unit = QPointF(direction.x() / length, direction.y() / length)
    pad = 14.0
    new_start = QPointF(start.x() + unit.x() * pad, start.y() + unit.y() * pad)
    new_end = QPointF(end.x() - unit.x() * pad, end.y() - unit.y() * pad)
    return new_start, new_end


def _quad_point(start: QPointF, control: QPointF, end: QPointF, t: float) -> QPointF:
    """Point on a quadratic Bézier at parameter ``t``."""
    inv = 1.0 - t
    x = inv * inv * start.x() + 2 * inv * t * control.x() + t * t * end.x()
    y = inv * inv * start.y() + 2 * inv * t * control.y() + t * t * end.y()
    return QPointF(x, y)


#: Human-readable node details used by the page's side panel (spec §32).
NODE_DETAIL_GROUPS = {
    "laptop": ("CPU", "GPU", "RAM", "Storage", "Telemetry"),
    "wifi": ("status", "rssi", "latency", "packets"),
    "esp32": ("device_id", "ip", "firmware", "protocol", "uptime", "state"),
    "pwm": ("duty", "frequency"),
    "fan": ("PWM", "RPM", "current", "voltage", "power"),
    "tach": ("RPM", "pulses_per_rev"),
    "ina219": ("voltage", "current", "power"),
    "ds18b20": ("temperature", "state"),
    "telemetry": ("sampling_rate", "sensors"),
}


__all__ = ["NODE_DETAIL_GROUPS", "Schematic"]
