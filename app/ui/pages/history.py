"""History page (spec §35, §36, §37).

Reads recorded snapshots from the database, renders them on the shared chart and
computes summary analytics: min/max/average, time above the warning threshold,
and a count of thermal excursions.  All statistics are labelled as *derived*
(spec §108) and the page states the row count it actually loaded, so a truncated
query never masquerades as the whole history.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import Page
from app.ui.widgets.atoms import (
    Card,
    EmptyState,
    KeyValueRow,
    MetricCard,
    SectionHeader,
    StatusBadge,
)
from app.ui.widgets.charts import ChartPanel

#: Range presets: (key, label_key, hours, bucket seconds).
RANGES = [
    ("h1", "history.range.hour", 1.0, 5),
    ("h24", "history.range.day", 24.0, 60),
    ("d7", "history.range.week", 168.0, 900),
]

MAX_ROWS = 20_000


class HistoryPage(Page):
    key = "history"
    title_key = "history.title"
    subtitle_key = "history.subtitle"
    icon_name = "history"
    wants_snapshots = False

    def _build_content(self, content: QWidget) -> None:
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])

        header = QHBoxLayout()
        header.addWidget(SectionHeader(self.tr_.t("history.title"), self.tr_.t("history.subtitle")), 1)
        self.row_badge = StatusBadge(self.theme, self.tr_, "", tone="text_muted")
        header.addWidget(self.row_badge)
        layout.addLayout(header)

        stats = QGridLayout()
        stats.setSpacing(SPACE["md"])
        self.stat_cards: dict[str, MetricCard] = {}
        specs = [
            ("cpu_max", "dash.cpu", "cpu", "°C"),
            ("gpu_max", "dash.gpu", "gpu", "°C"),
            ("fan_avg", "dash.fan_rpm", "fan", "RPM"),
            ("time_above", "history.time_above", "alerts", "min"),
        ]
        for index, (key, title_key, icon, unit) in enumerate(specs):
            card = MetricCard(self.theme, self.tr_, title=self.tr_.t(title_key), unit=unit, icon_name=icon)
            self.stat_cards[key] = card
            stats.addWidget(card, 0, index)
        layout.addLayout(stats)

        self.chart_panel = ChartPanel(
            self.theme,
            self.tr_,
            title=self.tr_.t("history.title"),
            ranges=[(key, self.tr_.t(label)) for key, label, _, _ in RANGES],
            capacity=MAX_ROWS,
        )
        self.chart_panel.add_series("cpu", self.tr_.t("dash.cpu"), unit="°C", color_index=0)
        self.chart_panel.add_series("gpu", self.tr_.t("dash.gpu"), unit="°C", color_index=1)
        self.chart_panel.add_series("fan", self.tr_.t("dash.fan_pwm"), unit="%", color_index=2)
        self.chart_panel.setMinimumHeight(360)
        self.chart_panel.chart.range_changed.connect(self.reload)
        layout.addWidget(self.chart_panel, 1)

        detail_card = Card()
        detail = QVBoxLayout(detail_card)
        detail.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        detail.setSpacing(SPACE["sm"])
        detail.addWidget(SectionHeader(self.tr_.t("history.thermal_events")))
        self.event_rows: dict[str, KeyValueRow] = {}
        for key, label in (
            ("cpu_min", "common.minimum"),
            ("cpu_avg", "common.average"),
            ("gpu_min", "common.minimum"),
            ("gpu_avg", "common.average"),
            ("peak_fan", "common.peak"),
            ("samples", "common.value"),
        ):
            row = KeyValueRow(f"{self.tr_.t(label)} · {key.replace('_', ' ')}", "—", mono=True)
            self.event_rows[key] = row
            detail.addWidget(row)
        layout.addWidget(detail_card)

        self.empty = EmptyState(self.theme, self.tr_, icon_name="history", title=self.tr_.t("common.empty"))
        layout.addWidget(self.empty)

        self._range = "h1"
        self._last_stats: dict[str, float | None] = {}

    # -- data --------------------------------------------------------------

    def on_shown(self) -> None:
        self.reload(self._range)

    def reload(self, range_key: str = "h1") -> None:
        self._range = range_key
        self.chart_panel.set_range_active(range_key)
        preset = next((r for r in RANGES if r[0] == range_key), RANGES[0])
        _, _, hours, bucket_s = preset
        until = datetime.now().timestamp()
        since = until - hours * 3600
        rows = self._query(since, until)
        self._render(rows, since, until, bucket_s)

    def _query(self, since: float, until: float) -> list:
        persistence = self.services.persistence
        rows: list = []
        if persistence is not None:
            try:
                rows = persistence.snapshots.range(since, until, limit=MAX_ROWS)
            except Exception:  # noqa: BLE001 - a broken query must not crash the UI
                rows = []
        if rows:
            return rows
        # Before the writer flushes (or in portable mode) fall back to the
        # in-memory ring so the page is never mysteriously empty mid-session.
        return _rows_from_history(self.services.telemetry.history(), since, until)

    def _render(self, rows: list, since: float, until: float, bucket_s: int) -> None:
        self.empty.setVisible(not rows)
        self.chart_panel.setVisible(bool(rows))
        self.row_badge.set_status(
            f"{self.tr_.number(len(rows), grouping=True)} · {self.tr_.t('common.derived')}",
            self.theme.color("secondary"),
        )
        if not rows:
            return
        cpu_points, gpu_points, fan_points = [], [], []
        cpu_values, gpu_values, fan_values = [], [], []
        above = 0.0
        threshold = self.services.config.alerts.cpu_warning_c
        previous_ts: float | None = None
        for row in rows:
            ts = float(row["ts"])
            cpu = row["cpu_temp"]
            gpu = row["gpu_temp"]
            fan_pwm = row["fan_pwm"]
            if cpu is not None:
                cpu_points.append((ts, float(cpu)))
                cpu_values.append(float(cpu))
                if previous_ts is not None and cpu >= threshold:
                    above += min(bucket_s, ts - previous_ts)
            if gpu is not None:
                gpu_points.append((ts, float(gpu)))
                gpu_values.append(float(gpu))
            if fan_pwm is not None:
                fan_points.append((ts, float(fan_pwm)))
                fan_values.append(float(fan_pwm))
            previous_ts = ts

        self.chart_panel.chart.clear()
        self.chart_panel.chart.set_points("cpu", _decimate(cpu_points, bucket_s))
        self.chart_panel.chart.set_points("gpu", _decimate(gpu_points, bucket_s))
        self.chart_panel.chart.set_points("fan", _decimate(fan_points, bucket_s))

        fan_rpm_values = [float(row["fan_rpm"]) for row in rows if row["fan_rpm"] is not None]
        stats = {
            "cpu_max": max(cpu_values) if cpu_values else None,
            "cpu_min": min(cpu_values) if cpu_values else None,
            "cpu_avg": sum(cpu_values) / len(cpu_values) if cpu_values else None,
            "gpu_max": max(gpu_values) if gpu_values else None,
            "gpu_min": min(gpu_values) if gpu_values else None,
            "gpu_avg": sum(gpu_values) / len(gpu_values) if gpu_values else None,
            "fan_avg": sum(fan_rpm_values) / len(fan_rpm_values) if fan_rpm_values else None,
            "peak_fan": max(fan_rpm_values) if fan_rpm_values else None,
            "time_above": above / 60.0,
        }
        self._last_stats = stats
        self._update_cards(stats, len(rows))
        self._update_rows(stats, len(rows), fan_values)

    def _update_cards(self, stats: dict[str, float | None], count: int) -> None:
        def show(key: str, value: float | None, decimals: int, token: str) -> None:
            card = self.stat_cards[key]
            if value is None:
                card.set_value("—", token="text_faint")
            else:
                card.set_value(
                    self.tr_.number(value, decimals=decimals),
                    token=token,
                    subtitle=f"{self.tr_.number(count, grouping=True)} · {self.tr_.t('common.derived')}",
                )

        show("cpu_max", stats["cpu_max"], 1, "warm")
        show("gpu_max", stats["gpu_max"], 1, "warm")
        show("fan_avg", stats["fan_avg"], 0, "primary")
        show("time_above", stats["time_above"], 1, "danger")

    def _update_rows(self, stats: dict[str, float | None], count: int, fan_values: list[float]) -> None:
        def value(key: str, decimals: int, suffix: str = "") -> str:
            raw = stats.get(key)
            if raw is None:
                return "—"
            return f"{self.tr_.number(raw, decimals=decimals)}{suffix}"

        self.event_rows["cpu_min"].set_value(value("cpu_min", 1, " °C"))
        self.event_rows["cpu_avg"].set_value(value("cpu_avg", 1, " °C"))
        self.event_rows["gpu_min"].set_value(value("gpu_min", 1, " °C"))
        self.event_rows["gpu_avg"].set_value(value("gpu_avg", 1, " °C"))
        self.event_rows["peak_fan"].set_value(
            self.tr_.number(stats["peak_fan"], grouping=True) if stats["peak_fan"] is not None else "—"
        )
        self.event_rows["samples"].set_value(self.tr_.number(count, grouping=True))

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        # History is on-demand; the shell still calls refresh when the page is
        # visible, so nothing to do here.
        return

    def retranslate(self) -> None:
        if not self._built:
            return
        for key, card in self.stat_cards.items():
            label_key = {"cpu_max": "dash.cpu", "gpu_max": "dash.gpu", "fan_avg": "dash.fan_rpm", "time_above": "history.time_above"}[key]
            card.set_title(self.tr_.t(label_key))


def _rows_from_history(
    history: list[TelemetrySnapshot], since: float, until: float
) -> list[dict[str, float | None]]:
    """Project in-memory snapshots into the same shape as database rows."""
    rows: list[dict[str, float | None]] = []
    for snapshot in history:
        ts = snapshot.timestamp.timestamp()
        if ts < since or ts > until:
            continue
        rows.append(
            {
                "ts": ts,
                "cpu_temp": snapshot.cpu.temperature,
                "gpu_temp": snapshot.gpu.temperature,
                "cpu_usage": snapshot.cpu.usage,
                "gpu_usage": snapshot.gpu.usage,
                "ram_usage": snapshot.memory.usage_percent,
                "fan_rpm": snapshot.fan.rpm,
                "fan_pwm": snapshot.fan.pwm_percent,
                "fan_target": snapshot.fan.target_percent,
                "fan_power_w": snapshot.fan.power_w,
                "pad_temp": snapshot.pad.temperature,
            }
        )
    return rows


def _decimate(points: list[tuple[float, float]], bucket_s: int) -> list[tuple[float, float]]:
    """Average points into time buckets to keep wide ranges cheap to draw."""
    if bucket_s <= 1 or len(points) <= 2:
        return points
    buckets: dict[int, list[float]] = {}
    for ts, value in points:
        buckets.setdefault(int(ts // bucket_s), []).append(value)
    result: list[tuple[float, float]] = []
    for bucket, values in sorted(buckets.items()):
        result.append((float(bucket * bucket_s), sum(values) / len(values)))
    return result


__all__ = ["HistoryPage", "RANGES"]
