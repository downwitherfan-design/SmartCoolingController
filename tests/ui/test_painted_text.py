"""Custom-painted text must stay inside its box (the class the owner reported).

The layout checker inspects QLabel geometry, but gauges, the fan, the schematic
and the trend strip paint their own text with ``QPainter.drawText``.  A long
value or a Persian translation could therefore spill over the arc or the blades
and no QLabel-based test would notice.  These tests render the real widgets
through the recording painter and assert every run fits.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from app.core.enums import FanState, MetricState, ThermalState, ThermalTrend
from app.ui.layout_check import find_painted_text_problems
from app.ui.widgets.fan import FanReadout, FanVisualizer
from app.ui.widgets.gauges import PowerMetric, TemperatureGauge, ThermalTrendStrip


def _show(widget: QWidget, width: int, height: int, qapp) -> None:
    widget.resize(width, height)
    widget.show()
    for _ in range(4):
        qapp.processEvents()


def test_temperature_gauge_fits_a_long_simulated_sublabel(theme, translator, qapp) -> None:
    gauge = TemperatureGauge(theme, translator)
    gauge.set_label("CPU")
    gauge.set_reading(
        42.0,
        state=ThermalState.NORMAL,
        trend=ThermalTrend.STABLE,
        rate=1.5,
        metric_state=MetricState.SIMULATED,
    )
    _show(gauge, 170, 170, qapp)
    assert find_painted_text_problems(gauge) == []
    gauge.deleteLater()


def test_temperature_gauge_fits_persian(theme, qapp) -> None:
    from app.core.enums import Language
    from app.ui.i18n import Translator

    gauge = TemperatureGauge(theme, Translator(Language.PERSIAN))
    gauge.set_label("پردازنده")
    gauge.set_reading(
        42.0,
        state=ThermalState.NORMAL,
        trend=ThermalTrend.RISING,
        rate=2.0,
        metric_state=MetricState.SIMULATED,
    )
    _show(gauge, 168, 168, qapp)
    assert find_painted_text_problems(gauge) == []
    gauge.deleteLater()


def test_fan_visualiser_fits_unavailable_message(theme, translator, qapp) -> None:
    fan = FanVisualizer(theme, translator)
    fan.set_fan(rpm=None, pwm=None, target=None, state=FanState.UNKNOWN)
    _show(fan, 200, 200, qapp)
    assert find_painted_text_problems(fan) == []
    fan.deleteLater()


def test_fan_readout_rows_do_not_collide(theme, translator, qapp) -> None:
    readout = FanReadout(theme, translator)
    readout.set_values(
        state=FanState.UNKNOWN,
        target=None,
        pwm=None,
        voltage=None,
        current=None,
        power=None,
        fault="",
    )
    _show(readout, 180, 220, qapp)
    assert find_painted_text_problems(readout) == []
    readout.deleteLater()


def test_power_metric_fits(theme, translator, qapp) -> None:
    metric = PowerMetric(theme, translator)
    metric.set_label("Power")
    metric.set_metric(12.34, unit="W", tone="primary")
    _show(metric, 120, 60, qapp)
    assert find_painted_text_problems(metric) == []
    metric.deleteLater()


def test_trend_strip_fits(theme, translator, qapp) -> None:
    strip = ThermalTrendStrip(theme, translator)
    strip.set_trends(
        [
            ("CPU", ThermalTrend.RISING, 3.2, "primary"),
            ("GPU", ThermalTrend.FALLING, -1.1, "secondary"),
        ]
    )
    _show(strip, 240, 52, qapp)
    assert find_painted_text_problems(strip) == []
    strip.deleteLater()


def test_fit_text_measures_ink_not_advance_width(qapp) -> None:
    """Persian glyphs overhang their advance box; fitting must measure the ink.

    ``هیچ`` (Persian "none") reports a 24px advance width but 29px of ink.  A
    fitter that trusted ``boundingRect`` left it 29px wide in a 26px box, which
    the painted-text detector correctly flagged.  ``fit_text`` must use the ink
    metrics so the two agree.
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter

    from app.ui.design import TYPE
    from app.ui.widgets.base import fit_text

    image = QImage(64, 64, QImage.Format.Format_ARGB32)  # keep alive for the painter
    painter = QPainter(image)
    font = QFont()
    font.setPointSizeF(TYPE["caption"])
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    box = QRectF(0, 0, 26, 21)
    _text, _flags, fitted = fit_text(
        painter, "هیچ", box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
    )
    painter.end()
    ink = QFontMetricsF(fitted).tightBoundingRect("هیچ")
    assert ink.width() <= box.width() + 2, f"ink {ink.width()}px still overflows {box.width()}px"


def test_detector_reports_a_deliberate_overflow(qapp) -> None:
    """The detector must actually bite, or the tests above are vacuous."""
    from PySide6.QtCore import QRectF, Qt

    from app.ui import layout_check
    from app.ui.widgets import atoms

    class _Overflow(QWidget):
        def paintEvent(self, event) -> None:
            # Build the painter from the widget module's attribute so the
            # detector's patch of that module actually takes effect, exactly as
            # the real custom widgets do.
            painter = atoms.QPainter(self)
            painter.drawText(
                QRectF(0, 0, 8, 8),
                Qt.AlignmentFlag.AlignLeft,
                "this string cannot possibly fit in an eight pixel box",
            )
            painter.end()

    widget = _Overflow()
    widget.resize(200, 200)
    widget.show()
    qapp.processEvents()
    problems = layout_check.find_painted_text_problems(widget)
    assert problems, "the painted-text detector missed an obvious overflow"
    widget.deleteLater()
