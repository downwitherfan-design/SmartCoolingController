"""Data Management page (spec §62, §63, §65, §104, §110, §133).

Everything that touches the local database lives here: where it is, how big it
is, how healthy it is, and the destructive operations (clear, prune, restore)
are grouped and clearly separated from the safe ones (backup, export, open
folder).

Destructive actions are visually de-emphasised into a dedicated card and use
the ``danger`` button variant, which is the UI equivalent of a raised guard
(spec §22 "premium UI", §107 empty states).
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
)

from app.core.enums import ExportFormat
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import KeyValueRow, SectionHeader, StatusBadge
from app.ui.widgets.forms import FormCard


class DataPage(ScrollPage):
    key = "data"
    title_key = "data.title"
    subtitle_key = "data.subtitle"
    icon_name = "storage"
    wants_snapshots = False

    def build_body(self) -> None:
        layout = self.body

        layout.addWidget(SectionHeader(self.tr_.t("data.title"), self.tr_.t("data.subtitle")))

        storage = FormCard(self.theme, self.tr_, self.tr_.t("data.storage"))
        self.path_row = KeyValueRow(self.tr_.t("data.database_path"), "—", mono=True)
        self.size_row = KeyValueRow(self.tr_.t("data.database_size"), "—", mono=True)
        self.rows_row = KeyValueRow(self.tr_.t("telemetry.title"), "—", mono=True)
        self.sessions_row = KeyValueRow(self.tr_.t("sessions.title"), "—", mono=True)
        self.integrity_row = KeyValueRow(self.tr_.t("diag.health"), "—")
        for row in (self.path_row, self.size_row, self.rows_row, self.sessions_row, self.integrity_row):
            storage.rows_layout.addWidget(row)
        layout.addWidget(storage)

        tools = FormCard(self.theme, self.tr_, self.tr_.t("common.actions"))
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACE["sm"])
        self.backup_button = QPushButton(self.tr_.t("data.backup_now"))
        self.backup_button.setProperty("variant", "primary")
        self.backup_button.clicked.connect(self._backup)
        button_row.addWidget(self.backup_button)

        self.optimize_button = QPushButton(self.tr_.t("data.optimize"))
        self.optimize_button.clicked.connect(self._optimize)
        button_row.addWidget(self.optimize_button)

        self.export_button = QPushButton(self.tr_.t("common.export"))
        self.export_button.clicked.connect(self._export)
        button_row.addWidget(self.export_button)

        self.open_folder_button = QPushButton(self.tr_.t("data.open_folder"))
        self.open_folder_button.clicked.connect(lambda: self._open(self.services.paths.root))
        button_row.addWidget(self.open_folder_button)

        self.open_logs_button = QPushButton(self.tr_.t("data.open_logs"))
        self.open_logs_button.clicked.connect(lambda: self._open(self.services.paths.logs))
        button_row.addWidget(self.open_logs_button)

        self.open_exports_button = QPushButton(self.tr_.t("data.open_exports"))
        self.open_exports_button.clicked.connect(lambda: self._open(self.services.paths.exports))
        button_row.addWidget(self.open_exports_button)
        button_row.addStretch(1)
        tools.rows_layout.addLayout(button_row)
        layout.addWidget(tools)

        backups = FormCard(self.theme, self.tr_, self.tr_.t("data.restore"))
        self.backup_combo = QComboBox()
        self.backup_combo.setMinimumWidth(320)
        restore_row = QHBoxLayout()
        restore_row.setSpacing(SPACE["sm"])
        restore_row.addWidget(self.backup_combo, 1)
        self.restore_button = QPushButton(self.tr_.t("data.restore"))
        self.restore_button.clicked.connect(self._restore)
        restore_row.addWidget(self.restore_button)
        backups.rows_layout.addLayout(restore_row)
        layout.addWidget(backups)

        danger = FormCard(self.theme, self.tr_, self.tr_.t("common.delete"))
        danger_row = QHBoxLayout()
        danger_row.setSpacing(SPACE["sm"])
        self.clear_button = QPushButton(self.tr_.t("data.clear_old"))
        self.clear_button.setProperty("variant", "danger")
        self.clear_button.clicked.connect(self._clear_old)
        danger_row.addWidget(self.clear_button)
        self.drop_sim_button = QPushButton(self.tr_.t("sim.title"))
        self.drop_sim_button.setProperty("variant", "danger")
        self.drop_sim_button.clicked.connect(self._drop_simulated)
        danger_row.addWidget(self.drop_sim_button)
        danger_row.addStretch(1)
        danger.rows_layout.addLayout(danger_row)
        layout.addWidget(danger)

        self.status = StatusBadge(self.theme, self.tr_, self.tr_.t("common.none"), tone="text_faint")
        layout.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def on_shown(self) -> None:
        self.reload()

    def reload(self) -> None:
        paths = self.services.paths
        self.path_row.set_value(str(paths.database))
        persistence = self.services.persistence
        if persistence is None:
            self.size_row.set_value("—")
            self.rows_row.set_value("—")
            self.sessions_row.set_value("—")
            self.integrity_row.set_value(self.services.persistence_error or "—")
            return
        size = persistence.database.size_bytes()
        self.size_row.set_value(_human_bytes(size))
        self.rows_row.set_value(self.tr_.number(persistence.snapshots.count()))
        self.sessions_row.set_value(self.tr_.number(len(persistence.sessions.list_recent(1000))))
        healthy = persistence.integrity_check()
        self.integrity_row.set_value(self.tr_.t("health.healthy") if healthy else self.tr_.t("health.error"))
        self._reload_backups()

    def _reload_backups(self) -> None:
        persistence = self.services.persistence
        self.backup_combo.clear()
        if persistence is None:
            return
        for info in persistence.backups.list()[:25]:
            stamp = datetime.fromtimestamp(info.created_at).strftime("%Y-%m-%d %H:%M")
            self.backup_combo.addItem(f"{stamp} · {_human_bytes(info.size_bytes)} · {info.kind}", str(info.path))

    # -- actions -----------------------------------------------------------

    def _backup(self) -> None:
        persistence = self.services.persistence
        if persistence is None:
            return
        info = persistence.backups.create(kind="manual")
        self._reload_backups()
        self.status.set_status(self.tr_.t("data.backup_done"), self.theme.color("success"))
        self.message.emit(f"{self.tr_.t('data.backup_done')}: {info.name}", "success")

    def _optimize(self) -> None:
        persistence = self.services.persistence
        if persistence is None:
            return
        persistence.database.maintain()
        self.reload()
        self.status.set_status(self.tr_.t("data.optimized"), self.theme.color("success"))
        self.message.emit(self.tr_.t("data.optimized"), "success")

    def _restore(self) -> None:
        persistence = self.services.persistence
        if persistence is None or self.backup_combo.count() == 0:
            return
        from pathlib import Path

        target = Path(self.backup_combo.currentData())
        if not target.exists():
            self.status.set_status(self.tr_.t("common.error"), self.theme.color("danger"))
            return
        persistence.backups.restore(target)
        self.reload()
        self.status.set_status(self.tr_.t("common.done"), self.theme.color("success"))
        self.message.emit(self.tr_.t("data.restore"), "info")

    def _clear_old(self) -> None:
        persistence = self.services.persistence
        if persistence is None:
            return
        config = self.services.config.data
        cutoff = time.time() - config.raw_retention_hours * 3600
        removed = persistence.snapshots.delete_before(cutoff)
        persistence.snapshots.prune_to_limit(2_000_000)
        self.reload()
        self.message.emit(f"{self.tr_.t('data.clear_old')}: {self.tr_.number(removed)}", "info")

    def _drop_simulated(self) -> None:
        persistence = self.services.persistence
        if persistence is None:
            return
        removed = persistence.snapshots.delete_all_simulated()
        self.reload()
        self.message.emit(f"{self.tr_.t('sim.title')}: {self.tr_.number(removed)}", "info")

    def _export(self) -> None:
        fmt_label = self.tr_.t("common.export")
        directory = self.services.paths.exports
        directory.mkdir(parents=True, exist_ok=True)
        default = str(directory / f"telemetry-{datetime.now():%Y%m%d-%H%M%S}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, fmt_label, default, "CSV (*.csv);;JSON (*.json)"
        )
        if not path:
            return
        fmt = ExportFormat.JSON if path.lower().endswith(".json") else ExportFormat.CSV
        since = time.time() - 24 * 3600
        result = self.services.export.export_range(since=since, fmt=fmt, name=path)
        self.status.set_status(self.tr_.t("common.done"), self.theme.color("success"))
        self.message.emit(f"{fmt_label}: {getattr(result, 'path', path)}", "success")

    def _open(self, path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        return

    def retranslate(self) -> None:
        if not self._built:
            return
        self.backup_button.setText(self.tr_.t("data.backup_now"))
        self.optimize_button.setText(self.tr_.t("data.optimize"))
        self.export_button.setText(self.tr_.t("common.export"))
        self.open_folder_button.setText(self.tr_.t("data.open_folder"))
        self.open_logs_button.setText(self.tr_.t("data.open_logs"))
        self.open_exports_button.setText(self.tr_.t("data.open_exports"))
        self.restore_button.setText(self.tr_.t("data.restore"))
        self.clear_button.setText(self.tr_.t("data.clear_old"))


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:,.1f} TB"


__all__ = ["DataPage"]
