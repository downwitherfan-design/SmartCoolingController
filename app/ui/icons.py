"""Vector icon set drawn with QPainter (spec §22).

Icons are drawn as strokes on a 24×24 grid instead of being shipped as image
files.  Three benefits: they stay razor-sharp on any DPI, they take the theme
colour at paint time, and the installer carries no binary assets.

Usage::

    icon = make_icon("fan", theme.color("primary"), size=20)
    button.setIcon(icon)
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

#: Every drawing routine takes a painter already scaled to a 24×24 box.
Drawer = "callable"


def _pen(painter: QPainter, color: QColor, width: float) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _polyline(painter: QPainter, points: list[tuple[float, float]]) -> None:
    path = QPainterPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    painter.drawPath(path)


# --------------------------------------------------------------------------
# Drawers — each works inside a 0..24 coordinate space.
# --------------------------------------------------------------------------


def _draw_dashboard(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3, 3, 7.5, 7.5), 1.6, 1.6)
    p.drawRoundedRect(QRectF(13.5, 3, 7.5, 7.5), 1.6, 1.6)
    p.drawRoundedRect(QRectF(3, 13.5, 7.5, 7.5), 1.6, 1.6)
    p.drawRoundedRect(QRectF(13.5, 13.5, 7.5, 7.5), 1.6, 1.6)


def _draw_telemetry(p: QPainter) -> None:
    _polyline(p, [(3, 16), (8, 10), (12, 14), (17, 5), (21, 8)])


def _draw_fan(p: QPainter) -> None:
    center = QPointF(12, 12)
    for i in range(3):
        path = QPainterPath()
        angle = math.radians(i * 120)
        path.moveTo(center)
        path.cubicTo(
            center.x() + 8 * math.cos(angle),
            center.y() + 8 * math.sin(angle),
            center.x() + 8 * math.cos(angle + 1.1),
            center.y() + 8 * math.sin(angle + 1.1),
            center.x() + 3.2 * math.cos(angle + 2.4),
            center.y() + 3.2 * math.sin(angle + 2.4),
        )
        p.drawPath(path)
    p.drawEllipse(center, 2.4, 2.4)


def _draw_diagram(p: QPainter) -> None:
    p.drawEllipse(QPointF(5, 12), 2.6, 2.6)
    p.drawEllipse(QPointF(19, 6), 2.6, 2.6)
    p.drawEllipse(QPointF(19, 18), 2.6, 2.6)
    _polyline(p, [(7.3, 10.9), (16.7, 7.1)])
    _polyline(p, [(7.3, 13.1), (16.7, 16.9)])


def _draw_history(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.4, 8.4)
    _polyline(p, [(12, 7), (12, 12), (15.6, 14)])


def _draw_sessions(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.4, 8.4)
    _polyline(p, [(12, 12), (12, 6.6)])
    _polyline(p, [(12, 12), (16.2, 14.6)])


def _draw_wifi(p: QPainter) -> None:
    for radius in (3.4, 6.4, 9.4):
        rect = QRectF(12 - radius, 12 - radius, radius * 2, radius * 2)
        p.drawArc(rect, 45 * 16, 90 * 16)
    p.drawEllipse(QPointF(12, 16.5), 1.2, 1.2)


def _draw_esp32(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 2.0, 2.0)
    p.drawRoundedRect(QRectF(9.5, 9.5, 5, 5), 1.0, 1.0)
    for offset in (8.5, 12.0, 15.5):
        _polyline(p, [(offset, 3.2), (offset, 6.0)])
        _polyline(p, [(offset, 18.0), (offset, 20.8)])
        _polyline(p, [(3.2, offset), (6.0, offset)])
        _polyline(p, [(18.0, offset), (20.8, offset)])


def _draw_sensors(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 2.6, 2.6)
    p.drawEllipse(QPointF(12, 12), 6.4, 6.4)
    for i in range(4):
        angle = math.radians(i * 90 + 45)
        _polyline(
            p,
            [
                (12 + 7.4 * math.cos(angle), 12 + 7.4 * math.sin(angle)),
                (12 + 9.6 * math.cos(angle), 12 + 9.6 * math.sin(angle)),
            ],
        )


def _draw_alerts(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(12, 3.5)
    path.lineTo(20.5, 18.5)
    path.lineTo(3.5, 18.5)
    path.closeSubpath()
    p.drawPath(path)
    _polyline(p, [(12, 9.2), (12, 13.6)])
    p.drawEllipse(QPointF(12, 16.2), 0.9, 0.9)


def _draw_diagnostics(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 3.0, 3.0)
    _polyline(p, [(8, 14.5), (10.5, 11), (13, 13.5), (16.5, 8.5)])


def _draw_settings(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    for i in range(8):
        angle = math.radians(i * 45)
        _polyline(
            p,
            [
                (12 + 6.6 * math.cos(angle), 12 + 6.6 * math.sin(angle)),
                (12 + 9.0 * math.cos(angle), 12 + 9.0 * math.sin(angle)),
            ],
        )


def _draw_about(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
    _polyline(p, [(12, 10.6), (12, 16.4)])
    p.drawEllipse(QPointF(12, 7.6), 0.9, 0.9)


def _draw_search(p: QPainter) -> None:
    p.drawEllipse(QPointF(10.5, 10.5), 6.2, 6.2)
    _polyline(p, [(15.2, 15.2), (20.0, 20.0)])


def _draw_play(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(8, 5.5)
    path.lineTo(19, 12)
    path.lineTo(8, 18.5)
    path.closeSubpath()
    p.drawPath(path)


def _draw_stop(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(7, 7, 10, 10), 2.0, 2.0)


def _draw_pause(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(7.5, 6, 3.4, 12), 1.2, 1.2)
    p.drawRoundedRect(QRectF(13.1, 6, 3.4, 12), 1.2, 1.2)


def _draw_export(p: QPainter) -> None:
    _polyline(p, [(12, 4), (12, 15)])
    _polyline(p, [(7.5, 10.5), (12, 15), (16.5, 10.5)])
    _polyline(p, [(4.5, 15.5), (4.5, 19.5), (19.5, 19.5), (19.5, 15.5)])


def _draw_import(p: QPainter) -> None:
    _polyline(p, [(12, 15), (12, 4)])
    _polyline(p, [(7.5, 8.5), (12, 4), (16.5, 8.5)])
    _polyline(p, [(4.5, 15.5), (4.5, 19.5), (19.5, 19.5), (19.5, 15.5)])


def _draw_refresh(p: QPainter) -> None:
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 40 * 16, 250 * 16)
    _polyline(p, [(16.5, 3.5), (18.6, 7.2), (14.6, 8.2)])


def _draw_connect(p: QPainter) -> None:
    _polyline(p, [(9, 9), (6.5, 6.5)])
    _polyline(p, [(15, 15), (17.5, 17.5)])
    p.drawRoundedRect(QRectF(9.2, 9.2, 5.6, 5.6), 1.4, 1.4)


def _draw_disconnect(p: QPainter) -> None:
    _polyline(p, [(5, 5), (19, 19)])
    p.drawRoundedRect(QRectF(9.2, 9.2, 5.6, 5.6), 1.4, 1.4)


def _draw_thermometer(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(10, 3.5, 4, 12.5), 2.0, 2.0)
    p.drawEllipse(QPointF(12, 18.5), 3.4, 3.4)
    _polyline(p, [(12, 8), (12, 16)])


def _draw_cpu(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(7, 7, 10, 10), 2.0, 2.0)
    p.drawRoundedRect(QRectF(10.2, 10.2, 3.6, 3.6), 0.8, 0.8)
    for offset in (9.0, 12.0, 15.0):
        _polyline(p, [(offset, 4.0), (offset, 7.0)])
        _polyline(p, [(offset, 17.0), (offset, 20.0)])
        _polyline(p, [(4.0, offset), (7.0, offset)])
        _polyline(p, [(17.0, offset), (20.0, offset)])


def _draw_gpu(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 6.5, 17, 11), 2.2, 2.2)
    p.drawEllipse(QPointF(9.5, 12), 3.0, 3.0)
    _polyline(p, [(15.5, 9.5), (18.0, 9.5)])
    _polyline(p, [(15.5, 14.5), (18.0, 14.5)])


def _draw_memory(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 7, 17, 10), 2.0, 2.0)
    for offset in (7.5, 12.0, 16.5):
        _polyline(p, [(offset, 17.0), (offset, 20.0)])


def _draw_storage(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.4, 8.4)
    p.drawEllipse(QPointF(12, 12), 2.4, 2.4)
    _polyline(p, [(12, 9.6), (15.6, 6.8)])


def _draw_power(p: QPainter) -> None:
    _polyline(p, [(12.5, 3.5), (7.0, 13.0), (12, 13.0), (11.5, 20.5), (17, 11.0), (12, 11.0), (12.5, 3.5)])


def _draw_battery(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3, 8, 15, 8), 2.0, 2.0)
    p.drawRoundedRect(QRectF(19, 10.5, 2.4, 3), 1.0, 1.0)
    p.drawRect(QRectF(5.2, 10.2, 7.5, 3.6))


def _draw_check(p: QPainter) -> None:
    _polyline(p, [(5, 12.5), (10, 17.5), (19, 7)])


def _draw_warning(p: QPainter) -> None:
    _draw_alerts(p)


def _draw_error(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
    _polyline(p, [(8.8, 8.8), (15.2, 15.2)])
    _polyline(p, [(15.2, 8.8), (8.8, 15.2)])


def _draw_info(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.6, 8.6)
    _polyline(p, [(12, 11), (12, 16.6)])
    p.drawEllipse(QPointF(12, 7.8), 0.9, 0.9)


def _draw_close(p: QPainter) -> None:
    _polyline(p, [(6, 6), (18, 18)])
    _polyline(p, [(18, 6), (6, 18)])


def _draw_chevron_down(p: QPainter) -> None:
    _polyline(p, [(6.5, 9.5), (12, 15), (17.5, 9.5)])


def _draw_chevron_right(p: QPainter) -> None:
    _polyline(p, [(9.5, 6), (15, 12), (9.5, 18)])


def _draw_plus(p: QPainter) -> None:
    _polyline(p, [(12, 5.5), (12, 18.5)])
    _polyline(p, [(5.5, 12), (18.5, 12)])


def _draw_minus(p: QPainter) -> None:
    _polyline(p, [(5.5, 12), (18.5, 12)])


def _draw_trash(p: QPainter) -> None:
    _polyline(p, [(4.5, 6.5), (19.5, 6.5)])
    _polyline(p, [(9, 6.5), (9, 4.0), (15, 4.0), (15, 6.5)])
    _polyline(p, [(6.5, 6.5), (7.8, 19.5), (16.2, 19.5), (17.5, 6.5)])


def _draw_folder(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(3.5, 7.5)
    path.lineTo(9.5, 7.5)
    path.lineTo(11.5, 10.0)
    path.lineTo(20.5, 10.0)
    path.lineTo(20.5, 18.5)
    path.lineTo(3.5, 18.5)
    path.closeSubpath()
    p.drawPath(path)


def _draw_sun(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 4.2, 4.2)
    for i in range(8):
        angle = math.radians(i * 45)
        _polyline(
            p,
            [
                (12 + 6.4 * math.cos(angle), 12 + 6.4 * math.sin(angle)),
                (12 + 9.0 * math.cos(angle), 12 + 9.0 * math.sin(angle)),
            ],
        )


def _draw_moon(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(19, 14.5)
    path.arcTo(QRectF(5, 5, 14, 14), 30, 300)
    path.arcTo(QRectF(9.5, 2.5, 14, 14), 300, -300)
    p.drawPath(path)


def _draw_pin(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 9.5), 5.0, 5.0)
    _polyline(p, [(12, 14.5), (12, 20.5)])


def _draw_menu(p: QPainter) -> None:
    for y in (7.0, 12.0, 17.0):
        _polyline(p, [(4.5, y), (19.5, y)])


def _draw_star(p: QPainter) -> None:
    path = QPainterPath()
    cx, cy, outer, inner = 12.0, 12.4, 8.6, 3.7
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = -math.pi / 2 + index * math.pi / 5
        point = QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    p.drawPath(path)


def _draw_grid(p: QPainter) -> None:
    _draw_dashboard(p)


def _draw_globe(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.4, 8.4)
    p.drawEllipse(QPointF(12, 12), 3.6, 8.4)
    _polyline(p, [(3.8, 12), (20.2, 12)])


_DRAWERS = {
    "dashboard": _draw_dashboard,
    "telemetry": _draw_telemetry,
    "fan": _draw_fan,
    "diagram": _draw_diagram,
    "history": _draw_history,
    "sessions": _draw_sessions,
    "wifi": _draw_wifi,
    "esp32": _draw_esp32,
    "sensors": _draw_sensors,
    "alerts": _draw_alerts,
    "diagnostics": _draw_diagnostics,
    "settings": _draw_settings,
    "about": _draw_about,
    "search": _draw_search,
    "play": _draw_play,
    "stop": _draw_stop,
    "pause": _draw_pause,
    "export": _draw_export,
    "import": _draw_import,
    "refresh": _draw_refresh,
    "connect": _draw_connect,
    "disconnect": _draw_disconnect,
    "thermometer": _draw_thermometer,
    "cpu": _draw_cpu,
    "gpu": _draw_gpu,
    "memory": _draw_memory,
    "storage": _draw_storage,
    "power": _draw_power,
    "battery": _draw_battery,
    "check": _draw_check,
    "warning": _draw_warning,
    "error": _draw_error,
    "info": _draw_info,
    "close": _draw_close,
    "chevron_down": _draw_chevron_down,
    "chevron_right": _draw_chevron_right,
    "plus": _draw_plus,
    "minus": _draw_minus,
    "trash": _draw_trash,
    "folder": _draw_folder,
    "sun": _draw_sun,
    "moon": _draw_moon,
    "pin": _draw_pin,
    "star": _draw_star,
    "menu": _draw_menu,
    "grid": _draw_grid,
    "globe": _draw_globe,
}

AVAILABLE_ICONS = tuple(sorted(_DRAWERS))


def paint_icon(
    painter: QPainter,
    name: str,
    rect: QRectF,
    color: QColor | str,
    *,
    stroke_width: float = 1.7,
) -> None:
    """Draw icon ``name`` inside ``rect`` using ``color``."""
    drawer = _DRAWERS.get(name, _draw_about)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = min(rect.width(), rect.height()) / 24.0
    painter.translate(rect.center())
    painter.scale(scale, scale)
    painter.translate(-12, -12)
    _pen(painter, QColor(color), stroke_width)
    drawer(painter)
    painter.restore()


def render_pixmap(
    name: str,
    color: QColor | str,
    size: int = 24,
    *,
    dpr: float = 2.0,
    stroke_width: float = 1.7,
) -> QPixmap:
    """Render an icon to a device-pixel-ratio aware pixmap."""
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_icon(painter, name, QRectF(0, 0, size, size), color, stroke_width=stroke_width)
    painter.end()
    return pixmap


def make_icon(
    name: str,
    color: QColor | str,
    size: int = 20,
    *,
    stroke_width: float = 1.7,
) -> QIcon:
    """Build a QIcon from a vector drawer."""
    return QIcon(render_pixmap(name, color, size, stroke_width=stroke_width))


__all__ = ["AVAILABLE_ICONS", "make_icon", "paint_icon", "render_pixmap"]
