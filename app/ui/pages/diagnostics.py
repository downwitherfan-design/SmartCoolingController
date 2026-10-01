"""Diagnostics page (spec §47, §88, §91).

Two jobs:

* **Health** — each subsystem reported independently (never collapsed into a
  single score, spec §88) with the probe's own detail string.
* **Support bundle** — a redacted JSON snapshot written to the exports folder so
  a user can attach it to a bug report without leaking secrets (spec §91).

The page never blocks: the bundle is assembled synchronously (it is small) and
the result path is shown with a copy-to-clipboard affordance.
"""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.telemetry import TelemetrySnapshot
from app.services.health import HealthStatus
from app.ui.design import SPACE
from app.ui.pages.base import Page
from app.ui.widgets.atoms import Card, KeyValueRow, SectionHeader, StatusBadge

TOPIC_HEALTH = "health.updated"

_HEALTH_KEYS = ("cpu", "gpu", "fan", "esp32", "sensors", "database", "application")


class DiagnosticsPage(Page):
    key = "diagnostics"
    title_key = "diagnostics.title"
    subtitle_key = "diagnostics.subtitle"
    icon_name = "diagnostics"

    def _build_content(self, content: QWidget) -> None:
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])

        layout.addWidget(SectionHeader(self.tr_.t("diagnostics.title"), self.tr_.t("diagnostics.subtitle")))

        health_card = Card()
        health_layout = QVBoxLayout(health_card)
        health_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        health_layout.setSpacing(SPACE["md"])
        health_layout.addWidget(SectionHeader(self.tr_.t("dash.health")))
        grid = QGridLayout()
        grid.setSpacing(SPACE["md"])
        self.health_cards: dict[str, _HealthTile] = {}
        for index, key in enumerate(_HEALTH_KEYS):
            tile = _HealthTile(self.theme, self.tr_, key)
            self.health_cards[key] = tile
            grid.addWidget(tile, index // 4, index % 4)
        health_layout.addLayout(grid)
        layout.addWidget(health_card)

        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        env_card = Card()
        env_layout = QVBoxLayout(env_card)
        env_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        env_layout.setSpacing(SPACE["sm"])
        env_layout.addWidget(SectionHeader(self.tr_.t("about.title")))
        self.env_rows: dict[str, KeyValueRow] = {}
        for key in ("app_version", "build_id", "python", "platform", "mode", "database_path"):
            row_widget = KeyValueRow(key.replace("_", " ").title(), "—", mono=True)
            self.env_rows[key] = row_widget
            env_layout.addWidget(row_widget)
        env_layout.addStretch(1)
        row.addWidget(env_card, 1)

        bundle_card = Card()
        bundle_layout = QVBoxLayout(bundle_card)
        bundle_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        bundle_layout.setSpacing(SPACE["md"])
        bundle_layout.addWidget(SectionHeader(self.tr_.t("diagnostics.bundle"), self.tr_.t("about.privacy_note")))
        self.bundle_button = QPushButton(self.tr_.t("diagnostics.create_bundle"))
        self.bundle_button.setProperty("variant", "primary")
        self.bundle_button.clicked.connect(self._create_bundle)
        bundle_layout.addWidget(self.bundle_button)
        self.bundle_status = StatusBadge(self.theme, self.tr_, self.tr_.t("common.none"), tone="text_faint")
        bundle_layout.addWidget(self.bundle_status, 0, Qt.AlignmentFlag.AlignLeft)
        self.bundle_path = QLabel("—")
        self.bundle_path.setProperty("role", "mono")
        self.bundle_path.setWordWrap(True)
        self.bundle_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bundle_layout.addWidget(self.bundle_path)
        self.copy_button = QPushButton(self.tr_.t("diagnostics.copy_path"))
        self.copy_button.clicked.connect(self._copy_path)
        bundle_layout.addWidget(self.copy_button)
        bundle_layout.addStretch(1)
        row.addWidget(bundle_card, 1)
        layout.addLayout(row)

        self.log_card = Card()
        log_layout = QVBoxLayout(self.log_card)
        log_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        log_layout.setSpacing(SPACE["sm"])
        log_layout.addWidget(SectionHeader(self.tr_.t("diagnostics.events")))
        self.log_rows: list[KeyValueRow] = []
        for _ in range(8):
            row_widget = KeyValueRow("", "—", mono=True)
            self.log_rows.append(row_widget)
            log_layout.addWidget(row_widget)
        layout.addWidget(self.log_card)
        layout.addStretch(1)

        self._last_bundle: str | None = None
        self.subscribe(TOPIC_HEALTH, lambda event: self._apply_report(event.payload))

    def on_shown(self) -> None:
        report = self.services.health.check()
        self._apply_report(report)
        self._update_env()
        self._update_events()

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        report = self.services.health.last_report
        if report is not None:
            self._apply_report(report)

    # -- rendering ---------------------------------------------------------

    def _apply_report(self, report) -> None:
        for key, tile in self.health_cards.items():
            status = getattr(report, key, HealthStatus.UNKNOWN)
            tile.set_status(status, report.detail.get(key, ""))

    def _update_env(self) -> None:
        environment = self.services.diagnostics.environment()
        mapping = {
            "app_version": environment.get("app_version"),
            "build_id": environment.get("build_id"),
            "python": environment.get("python"),
            "platform": environment.get("platform"),
            "mode": environment.get("data_mode"),
            "database_path": environment.get("database"),
        }
        for key, row in self.env_rows.items():
            value = mapping.get(key)
            row.set_value(str(value) if value not in (None, "") else "—")

    def _update_events(self) -> None:
        events = self.services.diagnostics.recent_diagnostics(8)
        for index, row in enumerate(self.log_rows):
            if index < len(events):
                event = events[index]
                row.set_key(str(event.get("level", "")).upper())
                row.set_value(str(event.get("message", ""))[:120])
                row.setVisible(True)
            else:
                row.setVisible(False)

    def _create_bundle(self) -> None:
        bundle = self.services.diagnostics.support_bundle()
        directory = self.services.paths.exports
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = directory / f"support-bundle-{stamp}.json"
        try:
            path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            self.bundle_status.set_status(self.tr_.t("common.error"), self.theme.color("danger"))
            self.bundle_path.setText(str(exc))
            return
        self._last_bundle = str(path)
        self.bundle_path.setText(str(path))
        self.bundle_status.set_status(self.tr_.t("common.done"), self.theme.color("success"))
        self.message.emit(f"{self.tr_.t('diagnostics.create_bundle')}: {path.name}", "success")

    def _copy_path(self) -> None:
        if self._last_bundle:
            QApplication.clipboard().setText(self._last_bundle)
            self.message.emit(self.tr_.t("diagnostics.copy_path"), "info")

    def retranslate(self) -> None:
        if not self._built:
            return
        self.bundle_button.setText(self.tr_.t("diagnostics.create_bundle"))
        self.copy_button.setText(self.tr_.t("diagnostics.copy_path"))


class _HealthTile(QWidget):
    """One subsystem's health: name, status badge and the probe's detail."""

    def __init__(self, theme, translator, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["xs"])
        self.name_label = QLabel(translator.t(f"health.{key}") if key in _HEALTH_KEYS else key)
        self.name_label.setProperty("role", "caption")
        layout.addWidget(self.name_label)
        self.badge = StatusBadge(theme, translator, "", tone="text_faint")
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail_label = QLabel("")
        self.detail_label.setProperty("role", "caption")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

    def set_status(self, status: HealthStatus, detail: str = "") -> None:
        token = {
            HealthStatus.HEALTHY: "success",
            HealthStatus.WARNING: "warning",
            HealthStatus.ERROR: "danger",
            HealthStatus.UNKNOWN: "text_faint",
        }[status]
        self.badge.set_status(status.value, self.theme.color(token))
        self.detail_label.setText(detail)


__all__ = ["DiagnosticsPage"]
