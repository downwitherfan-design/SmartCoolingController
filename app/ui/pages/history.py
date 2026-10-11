"""History page (spec §35, §36, §37).

Reads recorded snapshots from the database, renders them on the shared chart and
computes summary analytics: min/max/average, time above the warning threshold,
and a count of thermal excursions.  All statistics are labelled as *derived*
(spec §108).  The range is aggregated inside SQLite so the chart and the figures
always describe the whole selected window; the page reports the number of
samples that window contained.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout

from app.models.telemetry import TelemetrySnapshot
from app.persistence.repositories import SeriesPoint
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
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

#: Maps the persisted :class:`ChartRange` value onto a history preset, so the
#: "default range" setting actually selects the opening view.  Ranges the page
#: does not offer fall back to the hour preset rather than an invalid key.
_RANGE_BY_SETTING = {
    "1h": "h1",
    "6h": "h1",
    "24h": "h24",
    "7d": "d7",
    "1m": "h1",
    "5m": "h1",
    "15m": "h1",
    "30m": "h1",
}

DEFAULT_RANGE = "h1"

MAX_POINTS = 20_000


class HistoryPage(ScrollPage):
    key = "history"
    title_key = "history.title"
    subtitle_key = "history.subtitle"
    icon_name = "history"
    wants_snapshots = False

    def build_body(self) -> None:
        layout = self.body

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
            ("fan_avg", "dash.fan_rpm", "fan", self.tr_.t("unit.rpm")),
            ("time_above", "history.time_above", "alerts", self.tr_.t("common.minutes")),
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
            capacity=MAX_POINTS,
            series_visibility=self.services.config.charts.series_visibility,
        )
        self.chart_panel.add_series("cpu", self.tr_.t("dash.cpu"), unit="°C", color_index=0)
        self.chart_panel.add_series("gpu", self.tr_.t("dash.gpu"), unit="°C", color_index=1)
        self.chart_panel.add_series("fan", self.tr_.t("dash.fan_pwm"), unit="%", color_index=2)
        self.chart_panel.setMinimumHeight(360)
        self.chart_panel.chart.range_changed.connect(self.reload)
        self.chart_panel.series_visibility_changed.connect(self._on_series_visibility)
        self._apply_chart_settings()
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

        self._range = DEFAULT_RANGE
        self._shown_once = False
        self._last_stats: dict[str, float | None] = {}

    # -- data --------------------------------------------------------------

    def _apply_chart_settings(self) -> None:
        chart = self.services.config.charts
        self.chart_panel.apply_display_settings(
            auto_scale=chart.auto_scale, show_grid=chart.show_grid, show_legend=chart.show_legend
        )

    def _on_series_visibility(self, key: str, visible: bool) -> None:
        state = dict(self.services.config.charts.series_visibility)
        state[key] = visible
        self.services.update_config(charts={"series_visibility": state})

    def on_shown(self) -> None:
        # Honour the persisted default range the first time the page appears;
        # afterwards the user's in-session choice wins.
        if not self._shown_once:
            self._shown_once = True
            self._range = _RANGE_BY_SETTING.get(
                self.services.config.charts.default_range.value, DEFAULT_RANGE
            )
        self.reload(self._range)

    def reload(self, range_key: str = DEFAULT_RANGE) -> None:
        self._range = range_key
        self.chart_panel.set_range_active(range_key)
        preset = next((r for r in RANGES if r[0] == range_key), RANGES[0])
        _, _, hours, bucket_s = preset
        until = datetime.now().timestamp()
        since = until - hours * 3600
        self._render(since, until, bucket_s)

    def _render(self, since: float, until: float, bucket_s: int) -> None:
        """Load and draw one range.

        The database path aggregates inside SQLite, so the whole range is drawn
        (never a row-capped prefix that would hide the newest data) and the
        statistics cover every sample.  Only when the database has nothing for
        the range — before the writer flushes, or in portable mode — does it fall
        back to the in-memory ring.
        """
        threshold = self.services.config.alerts.cpu_warning_c
        points: list[SeriesPoint] = []
        stats: dict[str, float | None] = {}
        persistence = self.services.persistence
        if persistence is not None:
            try:
                points = persistence.snapshots.series_bucketed(since, until, bucket_s)
                stats = persistence.snapshots.aggregate(
                    since, until, threshold_c=threshold, bucket_s=bucket_s
                )
            except Exception:  # noqa: BLE001 - a broken query must not crash the UI
                points, stats = [], {}
        if not points:
            rows = _rows_from_history(self.services.telemetry.history(), since, until)
            points = _bucket_rows(rows, bucket_s)
            stats = _summarise(rows, bucket_s, threshold)
        self._apply(points, stats)

    def _apply(self, points: list[SeriesPoint], stats: dict[str, float | None]) -> None:
        samples = int(stats.get("samples") or 0)
        self.empty.setVisible(not points)
        self.chart_panel.setVisible(bool(points))
        self.row_badge.set_status(
            f"{self.tr_.number(samples, grouping=True)} · {self.tr_.t('common.derived')}",
            self.theme.color("secondary"),
        )
        if not points:
            return
        self.chart_panel.chart.clear()
        cpu: list[tuple[float, float]] = []
        gpu: list[tuple[float, float]] = []
        fan: list[tuple[float, float]] = []
        for point in points:
            ts = point["ts"]
            if point["cpu_temp"] is not None:
                cpu.append((ts, point["cpu_temp"]))
            if point["gpu_temp"] is not None:
                gpu.append((ts, point["gpu_temp"]))
            if point["fan_pwm"] is not None:
                fan.append((ts, point["fan_pwm"]))
        self.chart_panel.chart.set_points("cpu", cpu)
        self.chart_panel.chart.set_points("gpu", gpu)
        self.chart_panel.chart.set_points("fan", fan)
        self._last_stats = stats
        self._update_cards(stats, samples)
        self._update_rows(stats, samples)

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

        show("cpu_max", stats.get("cpu_max"), 1, "warm")
        show("gpu_max", stats.get("gpu_max"), 1, "warm")
        show("fan_avg", stats.get("fan_avg"), 0, "primary")
        above_minutes = (stats.get("time_above_s") or 0.0) / 60.0
        show("time_above", above_minutes, 1, "danger")

    def _update_rows(self, stats: dict[str, float | None], count: int) -> None:
        def value(key: str, decimals: int, suffix: str = "") -> str:
            raw = stats.get(key)
            if raw is None:
                return "—"
            return f"{self.tr_.number(raw, decimals=decimals)}{suffix}"

        self.event_rows["cpu_min"].set_value(value("cpu_min", 1, " °C"))
        self.event_rows["cpu_avg"].set_value(value("cpu_avg", 1, " °C"))
        self.event_rows["gpu_min"].set_value(value("gpu_min", 1, " °C"))
        self.event_rows["gpu_avg"].set_value(value("gpu_avg", 1, " °C"))
        peak_fan = stats.get("peak_fan")
        self.event_rows["peak_fan"].set_value(
            self.tr_.number(peak_fan, grouping=True) if peak_fan is not None else "—"
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


class _HistoryRow(TypedDict):
    """A raw sample projected from the in-memory ring for the fallback path."""

    ts: float
    cpu_temp: float | None
    gpu_temp: float | None
    fan_rpm: float | None
    fan_pwm: float | None


def _rows_from_history(
    history: list[TelemetrySnapshot], since: float, until: float
) -> list[_HistoryRow]:
    """Project in-memory snapshots into the same shape as database rows."""
    rows: list[_HistoryRow] = []
    for snapshot in history:
        ts = snapshot.timestamp.timestamp()
        if ts < since or ts > until:
            continue
        rows.append(
            {
                "ts": ts,
                "cpu_temp": snapshot.cpu.temperature,
                "gpu_temp": snapshot.gpu.temperature,
                "fan_rpm": snapshot.fan.rpm,
                "fan_pwm": snapshot.fan.pwm_percent,
            }
        )
    return rows


def _bucket_rows(rows: list[_HistoryRow], bucket_s: int) -> list[SeriesPoint]:
    """Average in-memory rows into the same bucket shape as the SQL query."""
    if not rows:
        return []
    bucket = max(1, int(bucket_s))
    buckets: dict[int, dict[str, list[float]]] = {}
    for row in rows:
        slot = buckets.setdefault(int(float(row["ts"]) // bucket), {"cpu": [], "gpu": [], "fan": []})
        if row["cpu_temp"] is not None:
            slot["cpu"].append(float(row["cpu_temp"]))
        if row["gpu_temp"] is not None:
            slot["gpu"].append(float(row["gpu_temp"]))
        if row["fan_pwm"] is not None:
            slot["fan"].append(float(row["fan_pwm"]))

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return [
        {
            "ts": float(slot_index * bucket),
            "cpu_temp": mean(values["cpu"]),
            "gpu_temp": mean(values["gpu"]),
            "fan_pwm": mean(values["fan"]),
        }
        for slot_index, values in sorted(buckets.items())
    ]


def _summarise(
    rows: list[_HistoryRow], bucket_s: int, threshold_c: float
) -> dict[str, float | None]:
    """Whole-range summary in the same shape as ``SnapshotRepository.aggregate``."""
    cpu = [float(r["cpu_temp"]) for r in rows if r["cpu_temp"] is not None]
    gpu = [float(r["gpu_temp"]) for r in rows if r["gpu_temp"] is not None]
    fan = [float(r["fan_rpm"]) for r in rows if r["fan_rpm"] is not None]

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    above = 0.0
    previous_ts: float | None = None
    for row in rows:
        ts = float(row["ts"])
        cpu_temp = row["cpu_temp"]
        if cpu_temp is not None and previous_ts is not None and float(cpu_temp) >= threshold_c:
            above += min(bucket_s, ts - previous_ts)
        previous_ts = ts

    return {
        "samples": float(len(rows)),
        "cpu_min": min(cpu) if cpu else None,
        "cpu_max": max(cpu) if cpu else None,
        "cpu_avg": mean(cpu),
        "gpu_min": min(gpu) if gpu else None,
        "gpu_max": max(gpu) if gpu else None,
        "gpu_avg": mean(gpu),
        "fan_avg": mean(fan),
        "peak_fan": max(fan) if fan else None,
        "time_above_s": above,
    }


__all__ = ["HistoryPage", "RANGES"]
