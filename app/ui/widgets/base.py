"""Theme-aware widget base classes and painting helpers (spec §23, §24).

Every custom widget inherits :class:`ThemedWidget`, which:

* holds a reference to the live :class:`~app.ui.design.Theme`,
* repaints automatically when the theme changes,
* exposes :meth:`apply_theme` for subclasses that cache colours or icons.

This is the mechanism that keeps theme switching instant and leak-free — the
theme object is swapped once and every widget is told to restyle.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QLabel, QWidget

from app.ui.design import Theme
from app.ui.i18n import Translator
from app.ui.icons import make_icon, paint_icon


def qcolor(value: str, alpha: float | None = None) -> QColor:
    """Parse a theme colour token into a QColor, optionally overriding alpha."""
    color = QColor(value)
    if alpha is not None:
        color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


class ThemedWidget(QWidget):
    """Base class for widgets that follow the application theme."""

    def __init__(self, theme: Theme, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.tr_ = translator
        # Subclasses call ``apply_theme`` at the end of their own ``__init__``
        # once their child widgets exist.

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.apply_theme()
        self.update()

    def set_translator(self, translator: Translator) -> None:
        self.tr_ = translator

    def apply_theme(self) -> None:  # pragma: no cover - overridden
        """Subclasses cache colours/icons here."""

    def color(self, token: str) -> QColor:
        return QColor(self.theme.color(token))

    def icon(self, name: str, token: str = "text", size: int = 18):
        return make_icon(name, self.theme.color(token), size)


class ThemedFrame(QFrame):
    """Theme-aware frame (used for cards that need a QFrame base)."""

    def __init__(self, theme: Theme, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.tr_ = translator

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.apply_theme()
        self.update()

    def apply_theme(self) -> None:  # pragma: no cover
        pass

    def color(self, token: str) -> QColor:
        return QColor(self.theme.color(token))


def add_shadow(widget: QWidget, *, blur: int = 24, y_offset: int = 6, alpha: int = 90) -> None:
    """Attach a soft elevation shadow (spec §22 "refined shadows")."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def draw_glow(
    painter: QPainter,
    rect: QRectF,
    color: QColor,
    *,
    strength: float = 0.35,
    radius_factor: float = 1.0,
) -> None:
    """Paint a soft radial glow behind ``rect`` (used by gauges and the fan)."""
    center = rect.center()
    radius = max(rect.width(), rect.height()) * 0.5 * radius_factor
    gradient = QRadialGradient(center, radius)
    inner = QColor(color)
    inner.setAlphaF(strength)
    outer = QColor(color)
    outer.setAlphaF(0.0)
    gradient.setColorAt(0.0, inner)
    gradient.setColorAt(1.0, outer)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(center, radius, radius)
    painter.restore()


def draw_arc_track(
    painter: QPainter,
    rect: QRectF,
    start_deg: float,
    span_deg: float,
    color: QColor,
    width: float,
    *,
    round_cap: bool = True,
) -> None:
    """Draw a stroked arc, used by gauges and progress rings."""
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap if round_cap else Qt.PenCapStyle.FlatCap)
    painter.save()
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Qt angles are in 1/16th degree and counter-clockwise positive.
    painter.drawArc(rect, int(start_deg * 16), int(span_deg * 16))
    painter.restore()


def draw_gradient_arc(
    painter: QPainter,
    rect: QRectF,
    start_deg: float,
    span_deg: float,
    color: QColor,
    width: float,
) -> None:
    """Draw an arc whose colour fades from translucent to solid."""
    center = rect.center()
    gradient = QConicalGradient(center, -start_deg)
    faded = QColor(color)
    faded.setAlphaF(0.35)
    gradient.setColorAt(0.0, faded)
    gradient.setColorAt(max(0.0, min(0.999, span_deg / 360.0)), color)
    gradient.setColorAt(1.0, color)
    pen = QPen()
    pen.setBrush(gradient)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.save()
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(rect, int(start_deg * 16), int(span_deg * 16))
    painter.restore()


#: Width/height a fitted text run may never exceed, relative to its box.  The
#: last-resort elide can still leave a hair of ink over the edge, and Qt's
#: metrics round by a pixel.
FIT_TOL = 2


def fit_text(
    painter: QPainter,
    text: str,
    rect: QRectF,
    flags: Qt.AlignmentFlag | int = Qt.AlignmentFlag.AlignCenter,
    *,
    min_scale: float = 0.62,
    wrap: bool = False,
) -> tuple[str, Qt.AlignmentFlag | int, QFont]:
    """Shrink then elide ``text`` so it is actually drawn inside ``rect``.

    Custom-painted widgets used to size their boxes with magic pixel heights and
    hard-coded point sizes, so a long label or a non-Latin string was painted
    straight over the widget edge — invisible to any QLabel-based layout check.
    This fits the *real* font metrics to the *real* box:

    1. shrink the point size, down to ``min_scale`` of the base size;
    2. if it still does not fit, elide horizontally;
    3. return the (possibly modified) text, flags and the font to use.

    The caller must ``painter.setFont(font)`` before drawing.  This is the single
    definition of "fitted text" shared by every custom painter, so a new one
    cannot reintroduce the defect.
    """
    base = QFont(painter.font())
    size = base.pointSizeF()
    if size <= 0:  # font specified in pixels
        size = float(base.pixelSize())
    floor = max(1.0, size * min_scale)
    flags = int(flags)
    if wrap:
        flags |= int(Qt.TextFlag.TextWordWrap)
    width = int(rect.width())
    height = int(rect.height())

    font = QFont(base)
    while True:
        metrics = QFontMetricsF(font)
        if wrap:
            needed = metrics.boundingRect(
                QRectF(0, 0, width, height), Qt.TextFlag(flags), text
            ).height()
            fits = needed <= height + FIT_TOL
        else:
            # Measure the *ink*, not the advance box: Persian and Arabic glyphs
            # overhang their advance width, so a run that fits by boundingRect
            # can still paint past the box edge.
            ink = metrics.tightBoundingRect(text)
            fits = ink.width() <= width + FIT_TOL and ink.height() <= height + FIT_TOL
        if fits:
            break
        if font.pointSizeF() > floor:
            font.setPointSizeF(max(floor, font.pointSizeF() - 0.5))
            continue
        if not wrap:
            available = max(8, width - 2)
            elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available)
            if elided != text:
                text = elided
                continue
        break
    return text, Qt.AlignmentFlag(flags), font


def draw_fitted_text(
    painter: QPainter,
    text: str,
    rect: QRectF,
    flags: Qt.AlignmentFlag | int = Qt.AlignmentFlag.AlignCenter,
    *,
    min_scale: float = 0.62,
    wrap: bool = False,
) -> None:
    """Fit ``text`` to ``rect`` and draw it, restoring the painter's font after."""
    fitted, fitted_flags, font = fit_text(
        painter, text, rect, flags, min_scale=min_scale, wrap=wrap
    )
    painter.save()
    painter.setFont(font)
    painter.drawText(rect, fitted_flags, fitted)
    painter.restore()


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def vertical_fade(rect: QRectF, top: QColor, bottom: QColor) -> QLinearGradient:
    gradient = QLinearGradient(QPointF(rect.topLeft()), QPointF(rect.bottomLeft()))
    gradient.setColorAt(0.0, top)
    gradient.setColorAt(1.0, bottom)
    return gradient


class ElidedLabel(QLabel):
    """A label that elides long text instead of stretching the layout."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        super().setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        super().setText(text)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QFontMetrics

        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        if elided != super().text():
            QLabel.setText(self, elided)
        super().paintEvent(event)


__all__ = [
    "ElidedLabel",
    "FIT_TOL",
    "ThemedFrame",
    "ThemedWidget",
    "add_shadow",
    "draw_arc_track",
    "draw_fitted_text",
    "draw_glow",
    "fit_text",
    "draw_gradient_arc",
    "paint_icon",
    "qcolor",
    "rounded_path",
    "vertical_fade",
]
