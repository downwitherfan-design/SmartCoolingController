"""Cooling sessions page (spec §36, §37, §38).

A session is a recorded cooling run with summary statistics.  The page shows:

* a live recorder card (elapsed time, samples, peak temperatures) while running,
* the list of past sessions with their key statistics,
* a side-by-side comparison of any two sessions, and
* baseline capture so a "known good" run can be referenced later.

Statistics come from :class:`SessionRecorder`, never recomputed here, so the
numbers on screen are exactly the numbers stored in the database (spec §108).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.enums import SessionState
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import Page
from app.ui.widgets.atoms import Card, EmptyState, KeyValueRow, SectionHeader, StatusBadge
from app.ui.widgets.base import qcolor


class SessionsPage(Page):
    key = "sessions"
    title_key = "sessions.title"
    subtitle_key = "sessions.subtitle"
    icon_name = "sessions"

    def _build_content(self, content: QWidget) -> None:
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])

        top = QHBoxLayout()
        top.setSpacing(SPACE["lg"])

        recorder = Card()
        rec = QVBoxLayout(recorder)
        rec.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        rec.setSpacing(SPACE["md"])
        rec.addWidget(SectionHeader(self.tr_.t("sessions.title"), self.tr_.t("sessions.subtitle")))
        self.state_badge = StatusBadge(self.theme, self.tr_, self.tr_.t("common.none"), tone="text_faint")
        rec.addWidget(self.state_badge, 0, Qt.AlignmentFlag.AlignLeft)
        self.record_button = QPushButton(self.tr_.t("sessions.start"))
        self.record_button.setProperty("variant", "primary")
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self._toggle_recording)
        rec.addWidget(self.record_button)
        self.recorder_rows: dict[str, KeyValueRow] = {}
        for key, label_key in (
            ("duration", "common.duration"),
            ("samples", "common.value"),
            ("max_cpu", "dash.cpu"),
            ("max_gpu", "dash.gpu"),
            ("peak_rpm", "dash.fan_rpm"),
            ("alerts", "nav.alerts"),
        ):
            row = KeyValueRow(self.tr_.t(label_key), "—", mono=True)
            self.recorder_rows[key] = row
            rec.addWidget(row)
        rec.addStretch(1)
        top.addWidget(recorder, 3)

        compare = Card()
        cmp_layout = QVBoxLayout(compare)
        cmp_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        cmp_layout.setSpacing(SPACE["md"])
        cmp_layout.addWidget(SectionHeader(self.tr_.t("sessions.compare")))
        pickers = QHBoxLayout()
        self.left_combo = QComboBox()
        self.right_combo = QComboBox()
        pickers.addWidget(self.left_combo, 1)
        pickers.addWidget(self.right_combo, 1)
        cmp_layout.addLayout(pickers)
        self.compare_button = QPushButton(self.tr_.t("sessions.compare"))
        self.compare_button.clicked.connect(self._compare)
        cmp_layout.addWidget(self.compare_button)
        self.compare_rows: dict[str, KeyValueRow] = {}
        for key, label_key in (
            ("max_cpu", "dash.cpu"),
            ("max_gpu", "dash.gpu"),
            ("avg_rpm", "dash.fan_rpm"),
            ("time_above", "history.time_above"),
            ("samples", "common.value"),
        ):
            row = KeyValueRow(self.tr_.t(label_key), "—", mono=True)
            self.compare_rows[key] = row
            cmp_layout.addWidget(row)
        cmp_layout.addStretch(1)
        top.addWidget(compare, 2)
        layout.addLayout(top)

        table_card = Card()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
        self.table = QTableWidget(0, 6)
        self._headers = ("common.name", "common.time", "common.profile", "common.duration", "common.maximum", "nav.alerts")
        self.table.setHorizontalHeaderLabels([self.tr_.t(h) for h in self._headers])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection)
        table_layout.addWidget(self.table)
        layout.addWidget(table_card, 1)

        self.empty = EmptyState(self.theme, self.tr_, icon_name="sessions", title=self.tr_.t("sessions.empty"))
        layout.addWidget(self.empty)
        self._sessions: list = []
        self._selected: str | None = None

    # -- data --------------------------------------------------------------

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        self._update_recorder()
        if self.services.sessions.recording:
            current = self.services.sessions.current
            if current is not None:
                self._render_recorder(current)

    def _toggle_recording(self, checked: bool) -> None:
        if checked:
            profile = self.services.profiles.active
            session = self.services.sessions.start(
                name=f"Session {self.tr_.number(len(self.services.sessions.recent(200)) + 1)}",
                profile_key=profile.key if profile else "",
                simulated=self.services.registry.selected_backend().value == "simulation",
            )
            self.record_button.setText(self.tr_.t("sessions.stop"))
            self.state_badge.set_status(self.tr_.t("sessions.recording"), self.theme.color("danger"))
            self.message.emit(f"{self.tr_.t('sessions.start')}: {session.session_id}", "info")
        else:
            self.services.sessions.stop()
            self.record_button.setText(self.tr_.t("sessions.start"))
            self.state_badge.set_status(self.tr_.t("common.none"), self.theme.color("text_faint"))
            self.reload()

    def _update_recorder(self) -> None:
        recording = self.services.sessions.recording
        self.record_button.blockSignals(True)
        self.record_button.setChecked(recording)
        self.record_button.blockSignals(False)
        self.record_button.setText(
            self.tr_.t("sessions.stop") if recording else self.tr_.t("sessions.start")
        )
        if not recording:
            self.state_badge.set_status(self.tr_.t("common.none"), self.theme.color("text_faint"))

    def _render_recorder(self, session) -> None:
        num = self.tr_.number
        duration = ""
        if session.started_at:
            from app.core.clock import utc_now

            seconds = (utc_now() - session.started_at).total_seconds()
            duration = _format_duration(seconds, self.tr_)
        values = {
            "duration": duration,
            "samples": num(session.sample_count, grouping=True),
            "max_cpu": f"{num(session.max_cpu_c, decimals=1)} °C" if session.max_cpu_c else "—",
            "max_gpu": f"{num(session.max_gpu_c, decimals=1)} °C" if session.max_gpu_c else "—",
            "peak_rpm": num(session.peak_rpm, grouping=True) if session.peak_rpm else "—",
            "alerts": num(session.alert_count),
        }
        for key, value in values.items():
            self.recorder_rows[key].set_value(value)

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        self._sessions = self.services.sessions.recent(100)
        self.empty.setVisible(not self._sessions)
        self.table.setVisible(bool(self._sessions))
        self.table.setRowCount(0)
        for session in self._sessions:
            row = self.table.rowCount()
            self.table.insertRow(row)
            items = (
                session.name or session.session_id or "—",
                _format_timestamp(session.started_at, self.tr_),
                session.profile_key or "—",
                _format_duration(_duration_s(session), self.tr_),
                f"{self.tr_.number(session.max_cpu_c, decimals=1)} °C" if session.max_cpu_c else "—",
                self.tr_.number(session.alert_count),
            )
            for column, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, session.session_id)
                if session.state is SessionState.RUNNING:
                    item.setForeground(qcolor(self.theme.color("danger")))
                self.table.setItem(row, column, item)
        self._refresh_pickers()

    def _refresh_pickers(self) -> None:
        for combo in (self.left_combo, self.right_combo):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for session in self._sessions:
                combo.addItem(session.name or session.session_id or "—", session.session_id)
            if current:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)
        if self.left_combo.count() >= 2 and self.right_combo.currentIndex() == 0:
            self.right_combo.setCurrentIndex(1)

    def _on_selection(self) -> None:
        items = self.table.selectedItems()
        if items:
            self._selected = items[0].data(Qt.ItemDataRole.UserRole)

    def _compare(self) -> None:
        left = self.left_combo.currentData()
        right = self.right_combo.currentData()
        if not left or not right or left == right:
            self.message.emit(self.tr_.t("sessions.compare"), "warning")
            return
        comparison = self.services.sessions.compare(left, right)
        if comparison is None:
            return
        left_session = comparison.left
        right_session = comparison.right
        num = self.tr_.number

        def _fmt(value: float | None, **kwargs) -> str:
            return num(value, **kwargs) if value is not None else "—"

        rows = {
            "max_cpu": f"{_fmt(left_session.max_cpu_c, decimals=1)} / {_fmt(right_session.max_cpu_c, decimals=1)} °C",
            "max_gpu": f"{_fmt(left_session.max_gpu_c, decimals=1)} / {_fmt(right_session.max_gpu_c, decimals=1)} °C",
            "avg_rpm": f"{_fmt(left_session.avg_rpm, grouping=True)} / {_fmt(right_session.avg_rpm, grouping=True)}",
            "time_above": f"{num(left_session.time_above_threshold_s / 60, decimals=1)} / {num(right_session.time_above_threshold_s / 60, decimals=1)} min",
            "samples": f"{num(left_session.sample_count)} / {num(right_session.sample_count)}",
        }
        for key, value in rows.items():
            self.compare_rows[key].set_value(value)

    def retranslate(self) -> None:
        if not self._built:
            return
        self.record_button.setText(
            self.tr_.t("sessions.stop") if self.services.sessions.recording else self.tr_.t("sessions.start")
        )
        self.compare_button.setText(self.tr_.t("sessions.compare"))
        for index, key in enumerate(self._headers):
            item = self.table.horizontalHeaderItem(index)
            if item is not None:
                item.setText(self.tr_.t(key))


def _duration_s(session) -> float:
    if session.ended_at and session.started_at:
        return max(0.0, (session.ended_at - session.started_at).total_seconds())
    if session.started_at:
        from app.core.clock import utc_now

        return max(0.0, (utc_now() - session.started_at).total_seconds())
    return 0.0


def _format_duration(seconds: float, translator) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{translator.number(hours)}:{translator.number(minutes):0>2}:{translator.number(secs):0>2}"
    return f"{translator.number(minutes):0>2}:{translator.number(secs):0>2}"


def _format_timestamp(moment, translator) -> str:
    if moment is None:
        return "—"
    local = moment.astimezone() if moment.tzinfo else moment
    return local.strftime("%Y-%m-%d %H:%M")


__all__ = ["SessionsPage"]
