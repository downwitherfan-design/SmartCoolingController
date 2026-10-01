"""Sensors page (spec §28, §34).

Every sensor the backend exposes, with its live value, unit, range and mapped
metric.  Search and category filters keep the list usable on machines with
hundreds of sensors.  Starring a sensor stores it in ``ui.favorite_metrics`` so
the dashboard and the compact overlay can surface it (spec §34).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.sensors import SensorDescriptor
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import Page
from app.ui.widgets.atoms import EmptyState, SectionHeader, SensorRow, StatusBadge


class SensorsPage(Page):
    key = "sensors"
    title_key = "sensors.title"
    subtitle_key = "sensors.subtitle"
    icon_name = "sensors"

    def _build_content(self, content: QWidget) -> None:
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(SectionHeader(self.tr_.t("sensors.title"), self.tr_.t("sensors.subtitle")), 1)
        self.count_badge = StatusBadge(self.theme, self.tr_, "0", tone="text_muted")
        header.addWidget(self.count_badge)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr_.t("common.search"))
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search, 1)
        self.category_filter = QComboBox()
        self.category_filter.addItem(self.tr_.t("alerts.all"), "")
        self.category_filter.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.category_filter)
        self.favorites_only = QComboBox()
        self.favorites_only.addItem(self.tr_.t("sensors.favorites"), "favorites")
        self.favorites_only.addItem(self.tr_.t("alerts.all"), "all")
        self.favorites_only.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.favorites_only)
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("Root")
        self.rows_layout = QVBoxLayout(inner)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(SPACE["xs"])
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self.empty = EmptyState(self.theme, self.tr_, icon_name="sensors", title=self.tr_.t("common.empty"))
        layout.addWidget(self.empty, 1)

        self._rows: dict[str, SensorRow] = {}
        self._favorites: set[str] = set(self.services.config.appearance.favorite_metrics)

    # -- data --------------------------------------------------------------

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        sensors = self.services.telemetry.sensors
        if not sensors:
            return
        for sensor in sensors:
            if sensor.sensor_id not in self._rows:
                self._add_row(sensor)
            self._rows[sensor.sensor_id].set_sensor(sensor)
        self._sync_categories(sensors)
        self._apply_filter()
        self.count_badge.set_status(
            f"{self.tr_.number(len(sensors))} · {self.tr_.t('telemetry.title')}",
            self.theme.color("primary"),
        )

    def _add_row(self, sensor: SensorDescriptor) -> None:
        row = SensorRow(
            self.theme,
            self.tr_,
            name=f"{sensor.hardware_name} · {sensor.sensor_name}",
            kind=sensor.sensor_type or "sensor",
            value=self._value_text(sensor),
            unit=sensor.unit or "",
            state_text=str(getattr(sensor.state, "value", sensor.state)),
            source=str(sensor.backend or ""),
            tone=self.theme.metric_color(sensor.state),
            sensor_id=sensor.sensor_id,
            favorite=sensor.sensor_id in self._favorites,
        )
        row.favorite_toggled.connect(self._toggle_favorite)
        self._rows[sensor.sensor_id] = row
        self.rows_layout.addWidget(row)

    def _value_text(self, sensor: SensorDescriptor) -> str:
        if sensor.current is None:
            return "—"
        return self.tr_.number(sensor.current, decimals=1)

    def _sync_categories(self, sensors: list[SensorDescriptor]) -> None:
        existing = {self.category_filter.itemData(i) for i in range(self.category_filter.count())}
        for sensor in sensors:
            category = sensor.hardware_category or ""
            if category and category not in existing:
                self.category_filter.addItem(category, category)
                existing.add(category)

    def _toggle_favorite(self, sensor_id: str, favorite: bool) -> None:
        if favorite:
            self._favorites.add(sensor_id)
        else:
            self._favorites.discard(sensor_id)
        self.services.config_store.update(appearance={"favorite_metrics": sorted(self._favorites)})
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower()
        category = self.category_filter.currentData()
        favorites_only = self.favorites_only.currentData() == "favorites"
        visible = 0
        for sensor_id, row in self._rows.items():
            sensor = row.sensor
            if sensor is None:
                continue
            haystack = f"{sensor.hardware_name} {sensor.sensor_name} {sensor.sensor_type}".lower()
            matches = not query or query in haystack
            matches = matches and (not category or sensor.hardware_category == category)
            matches = matches and (not favorites_only or sensor_id in self._favorites)
            row.setVisible(matches)
            visible += 1 if matches else 0
        self.empty.setVisible(visible == 0 and bool(self._rows))

    def retranslate(self) -> None:
        if not self._built:
            return
        self.search.setPlaceholderText(self.tr_.t("common.search"))
        self.favorites_only.setItemText(0, self.tr_.t("sensors.favorites"))
        self.favorites_only.setItemText(1, self.tr_.t("alerts.all"))


__all__ = ["SensorsPage"]
