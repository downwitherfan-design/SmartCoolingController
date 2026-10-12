"""About page (spec §153, §154, §167, §196).

Product identity, versions, the firmware-authority safety statement, and the
third-party licence summary.  The safety notice is deliberately the most
prominent block on the page: the firmware is the final authority for fan
limits and the desktop application never bypasses it (spec §187).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import Card, KeyValueRow, SectionHeader, StatusBadge


class AboutPage(ScrollPage):
    key = "about"
    title_key = "about.title"
    subtitle_key = "about.subtitle"
    icon_name = "about"
    wants_snapshots = False

    def build_body(self) -> None:
        layout = self.body

        hero = Card()
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        hero_layout.setSpacing(SPACE["lg"])

        self.logo = QLabel()
        self.logo.setFixedSize(72, 72)
        hero_layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(SPACE["xs"])
        self.name_label = QLabel(self.tr_.t("app.name"))
        self.name_label.setProperty("role", "display")
        text_col.addWidget(self.name_label)
        self.description = QLabel(self.tr_.t("about.description"))
        self.description.setProperty("role", "muted")
        self.description.setWordWrap(True)
        text_col.addWidget(self.description)
        self.version_badge = StatusBadge(self.theme, self.tr_, "", tone="primary")
        text_col.addWidget(self.version_badge, 0, Qt.AlignmentFlag.AlignLeft)
        hero_layout.addLayout(text_col, 1)
        layout.addWidget(hero)

        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        versions = Card()
        versions_layout = QVBoxLayout(versions)
        versions_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        versions_layout.setSpacing(SPACE["sm"])
        versions_layout.addWidget(SectionHeader(self.tr_.t("about.title")))
        self.version_row = KeyValueRow(self.tr_.t("about.version"), "—", mono=True)
        self.build_row = KeyValueRow(self.tr_.t("about.build"), "—", mono=True)
        self.protocol_row = KeyValueRow(self.tr_.t("about.protocol"), "—", mono=True)
        self.python_row = KeyValueRow(self.tr_.t("about.python"), "—", mono=True)
        self.qt_row = KeyValueRow(self.tr_.t("about.qt"), "—", mono=True)
        for widget in (
            self.version_row,
            self.build_row,
            self.protocol_row,
            self.python_row,
            self.qt_row,
        ):
            versions_layout.addWidget(widget)
        versions_layout.addStretch(1)
        row.addWidget(versions, 1)

        safety = Card()
        safety_layout = QVBoxLayout(safety)
        safety_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        safety_layout.setSpacing(SPACE["sm"])
        safety_layout.addWidget(SectionHeader(self.tr_.t("about.safety")))
        self.safety_body = QLabel(self.tr_.t("about.safety_text"))
        self.safety_body.setProperty("role", "body")
        self.safety_body.setWordWrap(True)
        safety_layout.addWidget(self.safety_body)
        self.authority_badge = StatusBadge(self.theme, self.tr_, self.tr_.t("about.firmware_authority"), tone="warning")
        safety_layout.addWidget(self.authority_badge, 0, Qt.AlignmentFlag.AlignLeft)
        safety_layout.addStretch(1)
        row.addWidget(safety, 1)
        layout.addLayout(row)

        licenses = Card()
        licenses_layout = QVBoxLayout(licenses)
        licenses_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        licenses_layout.setSpacing(SPACE["xs"])
        licenses_layout.addWidget(SectionHeader(self.tr_.t("about.licenses"), self.tr_.t("about.thanks")))
        for name, license_name in _LICENSES:
            licenses_layout.addWidget(KeyValueRow(name, license_name, mono=False))
        layout.addWidget(licenses)
        layout.addStretch(1)

    def on_shown(self) -> None:
        from PySide6.QtGui import QPixmap

        from app.services.health import APP_VERSION, BUILD_ID, PROTOCOL_VERSION
        from app.ui.icons import make_icon

        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        icon = make_icon("fan", self.theme.color("primary"), 64)
        pixmap = icon.pixmap(64, 64)
        self.logo.setPixmap(pixmap)
        self.version_badge.set_status(f"v{APP_VERSION}", self.theme.color("primary"))
        self.version_row.set_value(APP_VERSION)
        self.build_row.set_value(BUILD_ID)
        self.protocol_row.set_value(str(PROTOCOL_VERSION))
        import platform
        import sys

        self.python_row.set_value(f"{platform.python_version()} ({sys.executable})")
        try:
            from PySide6 import __version__ as qt_version

            self.qt_row.set_value(f"PySide6 {qt_version}")
        except Exception:  # noqa: BLE001 - version lookup is best-effort
            self.qt_row.set_value("PySide6")

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        return

    def retranslate(self) -> None:
        if not self._built:
            return
        self.name_label.setText(self.tr_.t("app.name"))
        self.description.setText(self.tr_.t("about.description"))
        self.safety_body.setText(self.tr_.t("about.safety_text"))


_LICENSES = [
    ("PySide6 / Qt", "LGPL-3.0"),
    ("pyqtgraph", "MIT"),
    ("psutil", "BSD-3-Clause"),
    ("websockets", "BSD-3-Clause"),
    ("pydantic", "MIT"),
    ("cryptography", "Apache-2.0 / BSD-3-Clause"),
]


__all__ = ["AboutPage"]
