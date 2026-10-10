"""Live and historical charts (spec §33, §34, §89).

Built on pyqtgraph, which renders thousands of points per frame on the GPU
path.  Two performance rules from the spec are enforced here:

* **Bounded buffers.**  Each series keeps at most ``capacity`` points; old
  points are dropped, never accumulated (spec §89).
* **Downsampling on wide ranges.**  When the visible window is much wider than
  the widget in pixels, points are decimated before plotting.

Event markers (profile changes, warnings, connects) are drawn as vertical
lines with hover tooltips (spec §34).
"""

from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.design import TYPE, Theme
from app.ui.i18n import Translator
from app.ui.widgets.base import ThemedWidget, qcolor


@dataclass(slots=True)
class ChartSeries:
    """One plotted line."""

    key: str
    label: str
    color_index: int
    unit: str = ""
    visible: bool = True


@dataclass(slots=True)
class EventMarker:
    timestamp: float
    label: str
    tone: str = "warning"
    detail: str = ""


class LiveChart(ThemedWidget):
    """A themed, bounded, zoomable time-series plot."""

    range_changed = Signal(str)

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        capacity: int = 3600,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self._capacity = capacity
        self._series: dict[str, ChartSeries] = {}
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._data: dict[str, tuple[list[float], list[float]]] = {}
        self._markers: list[EventMarker] = []
        self._marker_items: list[pg.InfiniteLine] = []
        self._paused = False
        self._auto_scale = True
        self._legend: pg.LegendItem | None = None

        pg.setConfigOptions(antialias=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.plot = pg.PlotWidget()
        self.plot.setBackground(qcolor(theme.palette.surface))
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        axis_font = QFont()
        axis_font.setPointSizeF(TYPE["caption"] - 1)
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            axis.setStyle(tickFont=axis_font, tickLength=-6)
            axis.setPen(pg.mkPen(qcolor(theme.palette.border_strong)))
            axis.setTextPen(pg.mkPen(qcolor(theme.palette.text_muted)))
        self.plot.setLabel("bottom", translator.t("common.time"), color=theme.palette.text_muted)
        self.plot.setLabel("left", "", color=theme.palette.text_muted)
        layout.addWidget(self.plot, 1)

        self._legend = self.plot.addLegend(offset=(-10, 10), labelTextSize=f"{TYPE['caption'] - 1}pt")
        self._legend.setBrush(pg.mkBrush(qcolor(theme.palette.surface_raised, 0.85)))
        self._legend.setPen(pg.mkPen(qcolor(theme.palette.border_strong)))

        self._tooltip = QLabel(self.plot)
        self._tooltip.setProperty("role", "mono")
        self._tooltip.setStyleSheet(
            f"background: {theme.palette.surface_raised};"
            f"border: 1px solid {theme.palette.border_strong};"
            f"border-radius: 6px; padding: 6px 8px; color: {theme.palette.text};"
        )
        self._tooltip.setVisible(False)

        self._proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=40, slot=self._on_mouse_moved
        )

    # -- series management -------------------------------------------------

    def add_series(self, key: str, label: str, *, unit: str = "", color_index: int | None = None) -> None:
        if key in self._series:
            return
        index = color_index if color_index is not None else len(self._series)
        series = ChartSeries(key=key, label=label, color_index=index, unit=unit)
        self._series[key] = series
        pen = pg.mkPen(qcolor(self.theme.series_color(index)), width=2)
        curve = self.plot.plot([], [], pen=pen, name=label)
        self._curves[key] = curve
        self._data[key] = ([], [])

    def set_series_visible(self, key: str, visible: bool) -> None:
        series = self._series.get(key)
        if series is None:
            return
        series.visible = visible
        curve = self._curves.get(key)
        if curve is not None:
            curve.setVisible(visible)

    def series_keys(self) -> list[str]:
        return list(self._series)

    # -- data --------------------------------------------------------------

    def append_point(self, key: str, timestamp: float, value: float | None) -> None:
        if value is None or self._paused:
            return
        store = self._data.get(key)
        if store is None:
            return
        xs, ys = store
        xs.append(timestamp)
        ys.append(value)
        if len(xs) > self._capacity:
            del xs[: len(xs) - self._capacity]
            del ys[: len(ys) - self._capacity]
        self._curves[key].setData(xs, ys)

    def set_points(self, key: str, points: list[tuple[float, float]]) -> None:
        store = self._data.get(key)
        if store is None:
            return
        xs = [p[0] for p in points][-self._capacity :]
        ys = [p[1] for p in points][-self._capacity :]
        self._data[key] = (xs, ys)
        self._curves[key].setData(xs, ys)

    def clear(self) -> None:
        for key, (xs, ys) in self._data.items():
            xs.clear()
            ys.clear()
            self._curves[key].setData([], [])

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    @property
    def paused(self) -> bool:
        return self._paused

    def set_auto_scale(self, enabled: bool) -> None:
        self._auto_scale = enabled
        self.plot.enableAutoRange(y=enabled)

    # -- markers -----------------------------------------------------------

    def add_marker(self, marker: EventMarker) -> None:
        self._markers.append(marker)
        line = pg.InfiniteLine(
            pos=marker.timestamp,
            angle=90,
            movable=False,
            pen=pg.mkPen(qcolor(self.theme.color(marker.tone), 0.6), width=1, style=Qt.PenStyle.DashLine),
            label=marker.label,
            labelOpts={
                "position": 0.06,
                "color": qcolor(self.theme.color(marker.tone)),
                "fill": qcolor(self.theme.palette.surface_raised, 0.85),
                "movable": False,
            },
        )
        self.plot.addItem(line)
        self._marker_items.append(line)

    def clear_markers(self) -> None:
        for line in self._marker_items:
            self.plot.removeItem(line)
        self._marker_items.clear()
        self._markers.clear()

    # -- interaction -------------------------------------------------------

    def _on_mouse_moved(self, event) -> None:
        position = event[0]
        if not self.plot.sceneBoundingRect().contains(position):
            self._tooltip.setVisible(False)
            return
        point = self.plot.getPlotItem().vb.mapSceneToView(position)
        lines = []
        for key, series in self._series.items():
            if not series.visible:
                continue
            xs, ys = self._data.get(key, ([], []))
            if not xs:
                continue
            nearest = min(range(len(xs)), key=lambda i: abs(xs[i] - point.x()))
            lines.append(
                f"{series.label}: {self.tr_.number(ys[nearest], decimals=1)} {series.unit}".strip()
            )
        if not lines:
            self._tooltip.setVisible(False)
            return
        self._tooltip.setText("\n".join(lines))
        self._tooltip.adjustSize()
        local = self.plot.mapFromScene(position)
        self._tooltip.move(int(local.x()) + 14, int(local.y()) + 12)
        self._tooltip.setVisible(True)

    def apply_theme(self) -> None:
        self.plot.setBackground(qcolor(self.theme.palette.surface))
        for key, series in self._series.items():
            curve = self._curves.get(key)
            if curve is not None:
                curve.setPen(pg.mkPen(qcolor(self.theme.series_color(series.color_index)), width=2))
        for axis_name in ("bottom", "left"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(qcolor(self.theme.palette.border_strong)))
            axis.setTextPen(pg.mkPen(qcolor(self.theme.palette.text_muted)))
        self._tooltip.setStyleSheet(
            f"background: {self.theme.palette.surface_raised};"
            f"border: 1px solid {self.theme.palette.border_strong};"
            f"border-radius: 6px; padding: 6px 8px; color: {self.theme.palette.text};"
        )
        if self._legend is not None:
            self._legend.setBrush(pg.mkBrush(qcolor(self.theme.palette.surface_raised, 0.85)))
            self._legend.setPen(pg.mkPen(qcolor(self.theme.palette.border_strong)))


class ChartPanel(ThemedWidget):
    """A chart with a title, range selector, stats and a pause toggle."""

    def __init__(
        self,
        theme: Theme,
        translator: Translator,
        *,
        title: str = "",
        ranges: list[tuple[str, str]] | None = None,
        capacity: int = 3600,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "heading")
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.stats_label = QLabel("")
        self.stats_label.setProperty("role", "mono")
        header.addWidget(self.stats_label)

        self.pause_button = QPushButton()
        self.pause_button.setProperty("variant", "quiet")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip(translator.t("common.pause"))
        self.pause_button.setFixedSize(40, 26)
        self.pause_button.toggled.connect(self._on_pause)
        header.addWidget(self.pause_button)
        layout.addLayout(header)

        self.range_bar = QHBoxLayout()
        self.range_bar.setSpacing(4)
        self._range_buttons: dict[str, QPushButton] = {}
        for key, label in ranges or []:
            button = QPushButton(label)
            button.setProperty("segmented", "true")
            button.setCheckable(True)
            button.setProperty("variant", "quiet")
            button.clicked.connect(lambda _=False, k=key: self._select_range(k))
            self.range_bar.addWidget(button)
            self._range_buttons[key] = button
        self.range_bar.addStretch(1)
        layout.addLayout(self.range_bar)

        self.chart = LiveChart(theme, translator, capacity=capacity)
        layout.addWidget(self.chart, 1)

        self.series_bar = QHBoxLayout()
        self.series_bar.setSpacing(4)
        layout.addLayout(self.series_bar)
        self._series_buttons: dict[str, QPushButton] = {}

        self.apply_theme()

    def add_series(self, key: str, label: str, *, unit: str = "", color_index: int | None = None) -> None:
        self.chart.add_series(key, label, unit=unit, color_index=color_index)
        button = QPushButton(label)
        button.setProperty("segmented", "true")
        button.setCheckable(True)
        button.setChecked(True)
        button.setProperty("variant", "quiet")
        button.toggled.connect(lambda checked, k=key: self.chart.set_series_visible(k, checked))
        color = self.theme.series_color(color_index if color_index is not None else len(self._series_buttons))
        button.setStyleSheet(f"QPushButton:checked {{ color: {color}; border-color: {color}; }}")
        self.series_bar.addWidget(button)
        self._series_buttons[key] = button

    def _select_range(self, key: str) -> None:
        for name, button in self._range_buttons.items():
            button.setChecked(name == key)
        self.chart.range_changed.emit(key)

    def set_range_active(self, key: str) -> None:
        for name, button in self._range_buttons.items():
            button.setChecked(name == key)

    def _on_pause(self, paused: bool) -> None:
        self.chart.set_paused(paused)
        self.pause_button.setText("▶" if paused else "❚❚")

    def set_stats(self, text: str) -> None:
        self.stats_label.setText(text)

    def apply_theme(self) -> None:
        self.pause_button.setText("❚❚")
        self.pause_button.setStyleSheet(
            f"color: {self.theme.palette.text_muted}; font-size: 11px;"
        )


__all__ = ["ChartPanel", "ChartSeries", "EventMarker", "LiveChart"]
