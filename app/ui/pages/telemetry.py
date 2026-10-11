"""Telemetry page (spec §28, §29, §108).

A dense, source-aware table of every normalised metric.  Columns are chosen to
make provenance visible at a glance (spec §108): value, unit, state, source
backend, source hardware and age.  Rows update in place — the table is built
once and values are rewritten, so there is no per-frame widget churn.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.core.enums import MetricState
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import Card, SectionHeader, StatusBadge
from app.ui.widgets.base import qcolor


class TelemetryPage(ScrollPage):
    key = "telemetry"
    title_key = "telemetry.title"
    subtitle_key = "telemetry.subtitle"
    icon_name = "telemetry"

    COLUMNS = ("common.name", "common.value", "common.unit", "common.state", "common.source")

    def build_body(self) -> None:
        layout = self.body

        header = QHBoxLayout()
        header.addWidget(SectionHeader(self.tr_.t("telemetry.title"), self.tr_.t("telemetry.subtitle")), 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr_.t("common.search"))
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._apply_filter)
        header.addWidget(self.search)
        self.state_filter = QComboBox()
        self.state_filter.addItem(self.tr_.t("alerts.all"), "")
        for state in MetricState:
            self.state_filter.addItem(state.value, state.value)
        self.state_filter.currentIndexChanged.connect(self._apply_filter)
        header.addWidget(self.state_filter)
        layout.addLayout(header)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([self.tr_.t(key) for key in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.COLUMNS)):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        card_layout.addWidget(self.table)
        layout.addWidget(card, 1)

        self.rate_badge = StatusBadge(self.theme, self.tr_, "", tone="text_muted")
        layout.addWidget(self.rate_badge, 0, Qt.AlignmentFlag.AlignLeft)
        self._rows: dict[str, int] = {}
        self._last_metrics: dict[str, tuple] = {}

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        metrics = snapshot.metrics
        if not metrics:
            return
        for key, metric in metrics.items():
            name = key.value
            if name not in self._rows:
                self._add_row(name, metric)
            else:
                self._update_row(self._rows[name], metric)
        self._apply_filter()
        interval = self.services.telemetry.interval_s
        hz = 1.0 / interval if interval else 0.0
        self.rate_badge.set_status(
            f"{self.tr_.t('telemetry.rate')}: {self.tr_.number(hz, decimals=1)} Hz",
            self.theme.color("primary"),
        )

    def _add_row(self, name: str, metric) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[name] = row
        name_item = QTableWidgetItem(_humanise(name))
        name_item.setData(Qt.ItemDataRole.UserRole, name)
        self.table.setItem(row, 0, name_item)
        for column in range(1, len(self.COLUMNS)):
            self.table.setItem(row, column, QTableWidgetItem("—"))
        self._update_row(row, metric)

    def _update_row(self, row: int, metric) -> None:
        value_text = "—" if metric.value is None else self.tr_.number(metric.value, decimals=1)
        state_text = self.tr_.t(f"common.{metric.state.value}") if metric.state.value in (
            "simulated", "measured", "derived", "estimated", "stale", "unavailable"
        ) else metric.state.value
        source = metric.source_hardware or metric.source_backend or "—"
        values = (value_text, metric.unit or "", state_text, source)
        for offset, text in enumerate(values, start=1):
            item = self.table.item(row, offset)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, offset, item)
            item.setText(text)
        tone = self.theme.metric_color(metric.state)
        for column in range(len(self.COLUMNS)):
            item = self.table.item(row, column)
            if item is not None:
                item.setForeground(qcolor(tone if column in (1, 3) else self.theme.palette.text))
        self._last_metrics[metric.key.value] = (metric.value, metric.state.value)

    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower()
        state = self.state_filter.currentData()
        for name, row in self._rows.items():
            item = self.table.item(row, 0)
            label = item.text().lower() if item else name.lower()
            visible = (not query or query in label) and (not state or name.lower().endswith(state.lower()))
            self.table.setRowHidden(row, not visible)

    def retranslate(self) -> None:
        if not self._built:
            return
        self.search.setPlaceholderText(self.tr_.t("common.search"))
        for index, key in enumerate(self.COLUMNS):
            item = self.table.horizontalHeaderItem(index)
            if item is not None:
                item.setText(self.tr_.t(key))


def _humanise(metric_name: str) -> str:
    """Turn ``CPU_TEMPERATURE_CORE_MAX`` into ``CPU · Temperature Core Max``."""
    parts = metric_name.split("_")
    if not parts:
        return metric_name
    head = parts[0]
    tail = " ".join(part.capitalize() for part in parts[1:])
    return f"{head} · {tail}" if tail else head


__all__ = ["TelemetryPage"]
