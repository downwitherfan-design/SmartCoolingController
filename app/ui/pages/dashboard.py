"""Dashboard page (spec §27, §28, §29, §30).

The dashboard answers three questions at a glance, in order of urgency:

1. *Is anything wrong?* — alert strip and subsystem health.
2. *How hot is it?* — CPU/GPU gauges with trend.
3. *Is the cooling responding?* — fan visualiser and PWM readout.

Layout uses a 12-column grid mentally: gauges on the left (the eye lands there
first), the live trace in the middle, cooling state on the right.  Everything is
built from the shared atoms so spacing and type match the rest of the app.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from app.core.enums import AlertSeverity, MetricState, ThermalState, ThermalTrend
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import (
    Card,
    EmptyState,
    KeyValueRow,
    MetricCard,
    SectionHeader,
    Sparkline,
    StatusBadge,
)
from app.ui.widgets.charts import ChartPanel
from app.ui.widgets.fan import FanReadout, FanVisualizer
from app.ui.widgets.gauges import TemperatureGauge, ThermalTrendStrip

#: Topics published by the alert service and connection manager.
TOPIC_ALERT = "alerts.raised"
TOPIC_ALERTS_CLEARED = "alerts.cleared"


class DashboardPage(ScrollPage):
    key = "dashboard"
    title_key = "dash.title"
    subtitle_key = "dash.subtitle"
    icon_name = "dashboard"

    def build_body(self) -> None:
        self._gauges: dict[str, TemperatureGauge] = {}
        self._metric_cards: dict[str, MetricCard] = {}
        self._sparklines: dict[str, Sparkline] = {}
        self._trend_history: dict[str, list[float]] = {"cpu": [], "gpu": []}
        self._recent_alerts: list[tuple[AlertSeverity, str, str, str]] = []

        self._build_alert_strip()
        self._build_primary_row()
        self._build_secondary_row()

        self.subscribe(TOPIC_ALERT, self._on_alert)

    # -- sections ----------------------------------------------------------

    def _build_alert_strip(self) -> None:
        self.alert_card = Card()
        self.alert_card.setVisible(False)
        layout = QHBoxLayout(self.alert_card)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(SPACE["md"])
        self.alert_badge = StatusBadge(self.theme, self.tr_, "", tone="danger")
        layout.addWidget(self.alert_badge)
        self.alert_text = QLabel()
        self.alert_text.setProperty("role", "body")
        self.alert_text.setWordWrap(True)
        layout.addWidget(self.alert_text, 1)
        self.body.addWidget(self.alert_card)

    def _build_primary_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        gauge_card = Card()
        gauge_layout = QVBoxLayout(gauge_card)
        gauge_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        gauge_layout.setSpacing(SPACE["md"])
        gauge_layout.addWidget(SectionHeader(self.tr_.t("dash.thermal_trend"), self.tr_.t("dash.subtitle")))
        gauges = QHBoxLayout()
        gauges.setSpacing(SPACE["lg"])
        for key in ("cpu", "gpu"):
            gauge = TemperatureGauge(self.theme, self.tr_)
            gauge.set_label(self.tr_.t(f"dash.{key}"))
            self._gauges[key] = gauge
            gauges.addWidget(gauge)
        gauge_layout.addLayout(gauges, 1)
        self.trend_strip = ThermalTrendStrip(self.theme, self.tr_)
        gauge_layout.addWidget(self.trend_strip)
        row.addWidget(gauge_card, 5)

        fan_card = Card()
        fan_layout = QVBoxLayout(fan_card)
        fan_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        fan_layout.setSpacing(SPACE["md"])
        fan_layout.addWidget(SectionHeader(self.tr_.t("dash.fan_target"), self.tr_.t("dash.pad")))
        fan_body = QHBoxLayout()
        fan_body.setSpacing(SPACE["lg"])
        self.fan_visual = FanVisualizer(self.theme, self.tr_)
        fan_body.addWidget(self.fan_visual, 3)
        self.fan_readout = FanReadout(self.theme, self.tr_)
        fan_body.addWidget(self.fan_readout, 2)
        fan_layout.addLayout(fan_body, 1)
        row.addWidget(fan_card, 4)

        self.body.addLayout(row)

    def _build_secondary_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        self.chart_panel = ChartPanel(
            self.theme,
            self.tr_,
            title=self.tr_.t("dash.live_chart"),
            ranges=[("m1", "1m"), ("m5", "5m"), ("m15", "15m")],
            capacity=3600,
        )
        self.chart_panel.add_series("cpu", self.tr_.t("dash.cpu"), unit="°C", color_index=0)
        self.chart_panel.add_series("gpu", self.tr_.t("dash.gpu"), unit="°C", color_index=1)
        self.chart_panel.add_series("fan", self.tr_.t("dash.fan_pwm"), unit="%", color_index=2)
        self.chart_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.chart_panel.setMinimumHeight(320)
        row.addWidget(self.chart_panel, 7)

        side = QVBoxLayout()
        side.setSpacing(SPACE["lg"])

        tiles = QGridLayout()
        tiles.setSpacing(SPACE["md"])
        specs = [
            ("memory", "dash.ram", "memory", "%"),
            ("storage", "dash.storage", "storage", "°C"),
            ("battery", "common.power", "battery", "%"),
        ]
        for index, (key, title_key, icon, unit) in enumerate(specs):
            card = MetricCard(self.theme, self.tr_, title=self.tr_.t(title_key), unit=unit, icon_name=icon)
            card.setMinimumWidth(150)
            self._metric_cards[key] = card
            tiles.addWidget(card, index // 2, index % 2)
        side.addLayout(tiles)

        health_card = Card()
        health_layout = QVBoxLayout(health_card)
        health_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        health_layout.setSpacing(SPACE["sm"])
        health_layout.addWidget(SectionHeader(self.tr_.t("dash.health")))
        self.health_rows: dict[str, KeyValueRow] = {}
        for key in ("cpu", "gpu", "fan", "sensors", "database"):
            row_widget = KeyValueRow(key.capitalize(), "—")
            self.health_rows[key] = row_widget
            health_layout.addWidget(row_widget)
        side.addWidget(health_card)
        side.addStretch(1)
        row.addLayout(side, 3)
        self.body.addLayout(row)

        self.alerts_card = Card()
        alerts_layout = QVBoxLayout(self.alerts_card)
        alerts_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        alerts_layout.setSpacing(SPACE["sm"])
        alerts_layout.addWidget(SectionHeader(self.tr_.t("dash.recent_alerts")))
        self.alerts_placeholder = EmptyState(
            self.theme,
            self.tr_,
            icon_name="check",
            title=self.tr_.t("dash.no_alerts"),
        )
        alerts_layout.addWidget(self.alerts_placeholder)
        self.alerts_list = QVBoxLayout()
        self.alerts_list.setSpacing(SPACE["sm"])
        alerts_layout.addLayout(self.alerts_list)
        self.body.addWidget(self.alerts_card)
        self.body.addStretch(1)

    # -- data --------------------------------------------------------------

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        cpu = snapshot.cpu
        gpu = snapshot.gpu
        thermal = snapshot.thermal
        fan = snapshot.fan

        cpu_state = thermal.cpu_state if thermal else ThermalState.NORMAL
        gpu_state = thermal.gpu_state if thermal else ThermalState.NORMAL
        cpu_trend = thermal.cpu_trend if thermal else ThermalTrend.STABLE
        gpu_trend = thermal.gpu_trend if thermal else ThermalTrend.STABLE

        self._gauges["cpu"].set_reading(
            cpu.temperature,
            state=cpu_state,
            trend=cpu_trend,
            rate=thermal.cpu_rate_c_per_min if thermal else None,
            metric_state=cpu.state,
        )
        self._gauges["gpu"].set_reading(
            gpu.temperature,
            state=gpu_state,
            trend=gpu_trend,
            rate=thermal.gpu_rate_c_per_min if thermal else None,
            metric_state=gpu.state,
        )

        if thermal is not None:
            self.trend_strip.set_trends(
                [
                    (self.tr_.t("dash.cpu"), cpu_trend, thermal.cpu_rate_c_per_min, self._thermal_token(cpu_state)),
                    (self.tr_.t("dash.gpu"), gpu_trend, thermal.gpu_rate_c_per_min, self._thermal_token(gpu_state)),
                    (
                        self.tr_.t("dash.fan_pwm"),
                        ThermalTrend.STABLE,
                        None,
                        "primary",
                    ),
                ]
            )

        self.fan_visual.set_fan(
            rpm=fan.rpm if fan.available else None,
            pwm=fan.pwm_percent,
            target=fan.target_percent,
            state=fan.state,
            response=fan.response,
        )
        self.fan_readout.set_values(
            state=fan.state,
            target=fan.target_percent,
            pwm=fan.pwm_percent,
            voltage=fan.voltage_v,
            current=fan.current_a,
            power=fan.power_w,
            response=fan.response,
            fault=fan.fault,
        )

        self._update_tiles(snapshot)
        self._update_chart(snapshot)
        self._update_health()

    def _thermal_token(self, state: ThermalState) -> str:
        return {
            ThermalState.COOL: "info",
            ThermalState.NORMAL: "success",
            ThermalState.WARM: "warning",
            ThermalState.HIGH: "warning",
            ThermalState.CRITICAL: "danger",
        }[state]

    def _update_tiles(self, snapshot: TelemetrySnapshot) -> None:
        memory = snapshot.memory
        card = self._metric_cards["memory"]
        if memory.usage_percent is not None:
            card.set_value(
                f"{self.tr_.number(memory.usage_percent, decimals=0)}%",
                token="primary" if memory.usage_percent < 80 else "warning",
                subtitle=self._memory_subtitle(memory),
                state=memory.state,
            )
        else:
            card.set_value("—", token="text_faint", state=MetricState.UNAVAILABLE)

        storage = snapshot.storage
        card = self._metric_cards["storage"]
        if storage.temperature is not None:
            card.set_value(
                f"{self.tr_.number(storage.temperature, decimals=0)}",
                token="text",
                subtitle=storage.name or "",
                state=storage.state,
            )
        else:
            card.set_value("—", token="text_faint", state=MetricState.UNAVAILABLE)

        battery = snapshot.battery
        card = self._metric_cards["battery"]
        if battery.present and battery.level_percent is not None:
            card.set_value(
                f"{self.tr_.number(battery.level_percent, decimals=0)}%",
                token="success" if not battery.charging else "primary",
                subtitle=self.tr_.t("common.power") if battery.charging else "",
                state=battery.state,
            )
        else:
            card.set_value("—", token="text_faint", state=MetricState.UNAVAILABLE)

    def _memory_subtitle(self, memory) -> str:
        if memory.used_mb is None or memory.total_mb is None:
            return ""
        used = self.tr_.number(memory.used_mb / 1024, decimals=1)
        total = self.tr_.number(memory.total_mb / 1024, decimals=1)
        return f"{used} / {total} GB"

    def _update_chart(self, snapshot: TelemetrySnapshot) -> None:
        timestamp = snapshot.timestamp.timestamp()
        self.chart_panel.chart.append_point("cpu", timestamp, snapshot.cpu.temperature)
        self.chart_panel.chart.append_point("gpu", timestamp, snapshot.gpu.temperature)
        self.chart_panel.chart.append_point("fan", timestamp, snapshot.fan.pwm_percent)
        cpu = snapshot.cpu.temperature
        gpu = snapshot.gpu.temperature
        if cpu is not None:
            self._trend_history["cpu"].append(cpu)
        if gpu is not None:
            self._trend_history["gpu"].append(gpu)
        self.chart_panel.set_stats(self._chart_stats(snapshot))

    def _chart_stats(self, snapshot: TelemetrySnapshot) -> str:
        thermal = snapshot.thermal
        if thermal is None:
            return ""
        cpu_avg = thermal.cpu_average_c
        gpu_avg = thermal.gpu_average_c
        parts = []
        if cpu_avg is not None:
            parts.append(f"CPU ⌀{self.tr_.number(cpu_avg, decimals=1)}°")
        if gpu_avg is not None:
            parts.append(f"GPU ⌀{self.tr_.number(gpu_avg, decimals=1)}°")
        return "   ".join(parts)

    def _update_health(self) -> None:
        report = self.services.health.last_report
        if report is None:
            return
        for key, row in self.health_rows.items():
            status = getattr(report, key, None)
            if status is None:
                continue
            row.set_value(
                self.tr_.t(f"health.{status.value}"),
                self.theme.health_color(status),
            )

    # -- alerts ------------------------------------------------------------

    def _on_alert(self, event) -> None:
        alert = event.payload
        severity = getattr(alert, "severity", AlertSeverity.INFORMATION)
        title = getattr(alert, "title", "")
        message = getattr(alert, "message", "")
        self._recent_alerts.insert(0, (severity, title, message, ""))
        self._recent_alerts = self._recent_alerts[:5]
        self._render_alerts()

    def _render_alerts(self) -> None:
        while self.alerts_list.count():
            item = self.alerts_list.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.alerts_placeholder.setVisible(not self._recent_alerts)
        from app.ui.widgets.atoms import AlertItem

        for severity, title, message, meta in self._recent_alerts:
            self.alerts_list.addWidget(
                AlertItem(self.theme, self.tr_, severity=severity, title=title, message=message, meta=meta)
            )
        worst = max((a[0] for a in self._recent_alerts), key=lambda s: list(AlertSeverity).index(s), default=None)
        if worst is not None and worst is not AlertSeverity.INFORMATION:
            self.alert_badge.set_status(self.tr_.t("alerts.title"), self.theme.severity_color(worst))
            self.alert_text.setText(self._recent_alerts[0][1])
            self.alert_card.setVisible(True)
        else:
            self.alert_card.setVisible(False)

    def retranslate(self) -> None:
        if not self._built:
            return
        self._gauges["cpu"].set_label(self.tr_.t("dash.cpu"))
        self._gauges["gpu"].set_label(self.tr_.t("dash.gpu"))
        for key, title_key in (("memory", "dash.ram"), ("storage", "dash.storage"), ("battery", "common.power")):
            self._metric_cards[key].set_title(self.tr_.t(title_key))
