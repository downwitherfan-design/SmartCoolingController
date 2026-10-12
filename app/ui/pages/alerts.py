"""Alerts page (spec §42, §43).

Active alerts and history, grouped by severity.  Each alert shows its value
against its threshold so the trigger is auditable (spec §108), and every alert
can be acknowledged or dismissed.  A filter bar lets the operator focus on
criticals during an incident.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
)

from app.core.enums import AlertSeverity
from app.models.alerts import AlertEvent
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import AlertItem, Card, EmptyState, SectionHeader, StatusBadge

TOPIC_ALERT = "alerts.raised"
TOPIC_ALERT_CLEARED = "alerts.cleared"


class AlertsPage(ScrollPage):
    key = "alerts"
    title_key = "alerts.title"
    subtitle_key = "alerts.subtitle"
    icon_name = "alerts"

    def build_body(self) -> None:
        layout = self.body

        header = QHBoxLayout()
        header.addWidget(SectionHeader(self.tr_.t("alerts.title"), self.tr_.t("alerts.subtitle")), 1)
        self.count_badge = StatusBadge(self.theme, self.tr_, "0", tone="text_muted")
        header.addWidget(self.count_badge)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        self.severity_filter = QComboBox()
        self.severity_filter.addItem(self.tr_.t("alerts.all"), "")
        for severity in AlertSeverity:
            self.severity_filter.addItem(self.tr_.t(f"alerts.severity.{severity.value}"), severity.value)
        self.severity_filter.currentIndexChanged.connect(lambda _: self.reload())
        toolbar.addWidget(self.severity_filter)
        self.ack_button = QPushButton(self.tr_.t("alerts.acknowledge"))
        self.ack_button.clicked.connect(self._acknowledge_selected)
        toolbar.addWidget(self.ack_button)
        self.dismiss_button = QPushButton(self.tr_.t("alerts.dismiss"))
        self.dismiss_button.clicked.connect(self._dismiss_selected)
        toolbar.addWidget(self.dismiss_button)
        toolbar.addStretch(1)
        self.clear_button = QPushButton(self.tr_.t("alerts.clear"))
        self.clear_button.setProperty("variant", "danger")
        self.clear_button.clicked.connect(self._clear)
        toolbar.addWidget(self.clear_button)
        layout.addLayout(toolbar)

        self.list_card = Card()
        self.list_layout = QVBoxLayout(self.list_card)
        self.list_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        self.list_layout.setSpacing(SPACE["sm"])
        layout.addWidget(self.list_card, 1)

        self.empty = EmptyState(self.theme, self.tr_, icon_name="check", title=self.tr_.t("dash.no_alerts"))
        layout.addWidget(self.empty, 1)
        layout.addStretch(1)

        self._items: dict[int, AlertItem] = {}
        self._selected: int | None = None
        self.subscribe(TOPIC_ALERT, lambda event: self.reload())
        self.subscribe(TOPIC_ALERT_CLEARED, lambda event: self.reload())

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        while self.list_layout.count():
            layout_item = self.list_layout.takeAt(0)
            if layout_item is None:
                continue
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()
        self._items.clear()
        self._selected = None

        severity_filter = self.severity_filter.currentData()
        include_dismissed = True
        alerts = self.services.alerts.recent(200, include_dismissed=include_dismissed)
        if severity_filter:
            alerts = [a for a in alerts if a.severity.value == severity_filter]

        self.empty.setVisible(not alerts)
        self.list_card.setVisible(bool(alerts))
        for alert in alerts:
            alert_id = alert.alert_id
            if alert_id is None:
                continue
            item = AlertItem(
                self.theme,
                self.tr_,
                severity=alert.severity,
                title=alert.title,
                message=_detail(alert, self.tr_),
                meta=_timestamp(alert, self.tr_),
                dismissed=alert.dismissed,
                alert_id=alert_id,
            )
            item.clicked.connect(lambda alert_id=alert_id: self._select(alert_id))
            self.list_layout.addWidget(item)
            self._items[alert_id] = item
        self.list_layout.addStretch(1)

        active = [a for a in alerts if not a.dismissed]
        worst = _worst(active)
        self.count_badge.set_status(
            self.tr_.number(len(active)),
            self.theme.severity_color(worst) if worst else self.theme.color("text_muted"),
        )

    def _select(self, alert_id: int) -> None:
        self._selected = alert_id
        for item_id, item in self._items.items():
            item.set_selected(item_id == alert_id)

    def _acknowledge_selected(self) -> None:
        if self._selected is None:
            return
        self.services.alerts.acknowledge(self._selected)
        self.reload()

    def _dismiss_selected(self) -> None:
        if self._selected is None:
            return
        self.services.alerts.dismiss(self._selected)
        self.reload()

    def _clear(self) -> None:
        self.services.alerts.clear_history()
        self.reload()

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        return

    def retranslate(self) -> None:
        if not self._built:
            return
        self.ack_button.setText(self.tr_.t("alerts.acknowledge"))
        self.dismiss_button.setText(self.tr_.t("alerts.dismiss"))
        self.clear_button.setText(self.tr_.t("alerts.clear"))


def _detail(alert: AlertEvent, translator) -> str:
    if alert.value is None or alert.threshold is None:
        return alert.message
    value = translator.number(alert.value, decimals=1)
    threshold = translator.number(alert.threshold, decimals=1)
    unit = alert.unit or ""
    return f"{alert.message}  ({value} {unit} / {threshold} {unit})"


def _timestamp(alert: AlertEvent, translator) -> str:
    moment = alert.timestamp
    if moment is None:
        return ""
    local = moment.astimezone() if moment.tzinfo else moment
    return local.strftime("%H:%M:%S")


def _worst(alerts: list[AlertEvent]) -> AlertSeverity | None:
    if not alerts:
        return None
    order = list(AlertSeverity)
    return max(alerts, key=lambda a: order.index(a.severity)).severity


__all__ = ["AlertsPage"]
