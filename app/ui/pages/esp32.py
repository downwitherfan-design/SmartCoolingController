"""ESP32 link page (spec §53, §103, §104).

Manages the device link end to end: manual address entry, the in-process mock
device (so the app is fully explorable without hardware — spec §53), connect /
disconnect, and a live protocol panel with latency, packet loss and the fan
readings the firmware reports.

The page is explicit about what is real: a mock device is labelled
"Simulated" everywhere it appears (spec §104), and the "never bypass firmware
safety" rule is stated in the panel (spec §100).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import (
    Card,
    ConnectionBadge,
    Divider,
    KeyValueRow,
    SectionHeader,
    StatusBadge,
)


class Esp32Page(ScrollPage):
    key = "esp32"
    title_key = "esp32.title"
    subtitle_key = "esp32.subtitle"
    icon_name = "esp32"

    #: Emitted from the discovery worker thread with the devices it found.
    _discovered = Signal(object)

    def build_body(self) -> None:
        self._discovering = False
        self._discovered.connect(self._on_discovered)
        layout = self.body

        header = QHBoxLayout()
        header.addWidget(SectionHeader(self.tr_.t("esp32.title"), self.tr_.t("esp32.subtitle")), 1)
        self.connection_badge = ConnectionBadge(self.theme, self.tr_, "", tone="text_faint")
        header.addWidget(self.connection_badge)
        layout.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        link_card = Card()
        link = QVBoxLayout(link_card)
        link.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        link.setSpacing(SPACE["md"])
        link.addWidget(SectionHeader(self.tr_.t("esp32.manual_ip")))

        host_row = QHBoxLayout()
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("192.168.1.50")
        self.host_input.setText(self.services.config.esp32.manual_host or "")
        host_row.addWidget(self.host_input, 3)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(self.services.config.esp32.port or 8765)
        host_row.addWidget(self.port_input, 1)
        link.addLayout(host_row)

        buttons = QHBoxLayout()
        self.connect_button = QPushButton(self.tr_.t("common.connect"))
        self.connect_button.setProperty("variant", "primary")
        self.connect_button.clicked.connect(self._connect_manual)
        buttons.addWidget(self.connect_button)
        self.mock_button = QPushButton(self.tr_.t("esp32.use_mock"))
        self.mock_button.clicked.connect(self._enable_mock)
        buttons.addWidget(self.mock_button)
        self.disconnect_button = QPushButton(self.tr_.t("common.disconnect"))
        self.disconnect_button.setProperty("variant", "danger")
        self.disconnect_button.clicked.connect(self._disconnect)
        buttons.addWidget(self.disconnect_button)
        link.addLayout(buttons)

        discover_row = QHBoxLayout()
        self.discover_button = QPushButton(self.tr_.t("esp32.discover"))
        self.discover_button.clicked.connect(self._discover)
        discover_row.addWidget(self.discover_button)
        self.retry_button = QPushButton(self.tr_.t("esp32.retry"))
        self.retry_button.clicked.connect(self._retry)
        discover_row.addWidget(self.retry_button)
        link.addLayout(discover_row)

        link.addWidget(Divider())
        safety = QLabel(self.tr_.t("about.safety_text"))
        safety.setProperty("role", "caption")
        safety.setWordWrap(True)
        link.addWidget(safety)
        link.addStretch(1)
        row.addWidget(link_card, 1)

        protocol_card = Card()
        protocol = QVBoxLayout(protocol_card)
        protocol.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        protocol.setSpacing(SPACE["sm"])
        protocol.addWidget(SectionHeader(self.tr_.t("esp32.protocol")))
        self.protocol_rows: dict[str, KeyValueRow] = {}
        for key, label_key in (
            ("device_id", "esp32.device_id"),
            ("firmware", "esp32.firmware"),
            ("protocol", "esp32.protocol"),
            ("latency", "esp32.latency"),
            ("loss", "esp32.packet_loss"),
            ("uptime", "esp32.uptime"),
            ("rssi", "common.voltage"),
        ):
            row_widget = KeyValueRow(self.tr_.t(label_key), "—", mono=True)
            self.protocol_rows[key] = row_widget
            protocol.addWidget(row_widget)
        self.mock_badge = StatusBadge(self.theme, self.tr_, self.tr_.t("esp32.mock"), tone="secondary")
        self.mock_badge.setVisible(False)
        protocol.addWidget(self.mock_badge, 0, Qt.AlignmentFlag.AlignLeft)
        protocol.addStretch(1)
        row.addWidget(protocol_card, 1)
        layout.addLayout(row)

        fan_card = Card()
        fan_layout = QVBoxLayout(fan_card)
        fan_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        fan_layout.setSpacing(SPACE["sm"])
        fan_layout.addWidget(SectionHeader(self.tr_.t("dash.fan_target")))
        self.fan_rows: dict[str, KeyValueRow] = {}
        for key, label_key in (
            ("target", "dash.fan_target"),
            ("pwm", "dash.fan_pwm"),
            ("rpm", "dash.fan_rpm"),
            ("voltage", "common.voltage"),
            ("current", "common.current"),
            ("power", "common.power"),
            ("state", "common.state"),
        ):
            row_widget = KeyValueRow(self.tr_.t(label_key), "—", mono=True)
            self.fan_rows[key] = row_widget
            fan_layout.addWidget(row_widget)
        layout.addWidget(fan_card)

        diag_card = Card()
        diag_layout = QVBoxLayout(diag_card)
        diag_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        diag_layout.setSpacing(SPACE["sm"])
        diag_layout.addWidget(
            SectionHeader(self.tr_.t("esp32.connection_center"), self.tr_.t("esp32.diag_subtitle"))
        )
        self.diag_rows: dict[str, KeyValueRow] = {}
        for key in (
            "state", "transport", "endpoint", "connect", "handshake",
            "first_telemetry", "freshness", "reconnects", "failures", "last_error",
        ):
            row_widget = KeyValueRow(self.tr_.t(f"esp32.diag.{key}"), "—", mono=True)
            self.diag_rows[key] = row_widget
            diag_layout.addWidget(row_widget)
        layout.addWidget(diag_card)
        layout.addStretch(1)

    # -- actions -----------------------------------------------------------

    def _retry(self) -> None:
        """Reconnect to the configured/last endpoint without rediscovering."""
        device = self.services.last_known_device()
        if device is None:
            host = self.host_input.text().strip()
            port = int(self.port_input.value())
            ok, reason = self.services.connect_manual(host, port)
            if not ok:
                self.message.emit(f"{self.tr_.t('esp32.retry')}: {reason}", "warning")
                return
        else:
            self.services.connect_device(device)
        self.message.emit(self.tr_.t("esp32.retry"), "info")

    def _connect_manual(self) -> None:
        host = self.host_input.text().strip()
        port = int(self.port_input.value())
        ok, reason = self.services.connect_manual(host, port)
        if not ok:
            self.message.emit(f"{self.tr_.t('esp32.manual_ip')}: {reason}", "warning")
            return
        self.message.emit(f"{self.tr_.t('common.connect')} {host}:{port}", "info")

    def _discover(self) -> None:
        """Run a bounded discovery scan on a worker thread (never blocks the UI)."""
        if self._discovering:
            return
        self._discovering = True
        self.discover_button.setEnabled(False)
        self.discover_button.setText(self.tr_.t("esp32.discovering"))

        import threading

        def _worker() -> None:
            try:
                found = self.services.discover_devices()
            except Exception as exc:  # noqa: BLE001 - discovery must never crash the UI
                found = []
                self.message.emit(f"{self.tr_.t('esp32.discover')}: {exc}", "warning")
            self._discovered.emit(found)

        threading.Thread(target=_worker, name="Esp32Discovery", daemon=True).start()

    def _on_discovered(self, found: object) -> None:
        self._discovering = False
        self.discover_button.setEnabled(True)
        self.discover_button.setText(self.tr_.t("esp32.discover"))
        devices = list(found) if isinstance(found, list) else []
        if not devices:
            self.message.emit(self.tr_.t("esp32.discover_none"), "warning")
            return
        device = devices[0]
        self.host_input.setText(str(device.host))
        self.port_input.setValue(int(device.port))
        self.services.connect_device(device)
        self.message.emit(f"{self.tr_.t('esp32.discover')}: {device.address}", "success")

    def _enable_mock(self) -> None:
        device = self.services.enable_mock_device(host="127.0.0.1", port=int(self.port_input.value()))
        self.services.connect_device(device)
        self.mock_badge.setVisible(True)
        self.message.emit(self.tr_.t("esp32.use_mock"), "info")

    def _disconnect(self) -> None:
        self.services.connection.disconnect()
        self.mock_badge.setVisible(False)

    # -- data --------------------------------------------------------------

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        connection = self.services.connection
        state = connection.state
        status = connection.status
        device = connection.device
        self.connection_badge.set_connection(state)
        num = self.tr_.number

        rows = {
            "device_id": status.device_id or (device.device_id if device else "") or "—",
            "firmware": status.firmware_version or "—",
            "protocol": str(status.protocol_version) if status.protocol_version else "—",
            "latency": f"{num(status.latency_ms, decimals=0)} ms" if status.latency_ms else "—",
            "loss": f"{num(connection.packet_loss_percent, decimals=1)} %"
            if connection.packet_loss_percent is not None
            else "—",
            "uptime": f"{num(status.uptime_s, decimals=0)} s" if status.uptime_s else "—",
            "rssi": f"{num(status.wifi_rssi_dbm, decimals=0)} dBm" if status.wifi_rssi_dbm else "—",
        }
        for key, value in rows.items():
            self.protocol_rows[key].set_value(str(value))
        self._refresh_diagnostics(connection)

        fan = snapshot.fan
        fan_rows = {
            "target": f"{num(fan.target_percent, decimals=0)} %" if fan.target_percent is not None else "—",
            "pwm": f"{num(fan.pwm_percent, decimals=0)} %" if fan.pwm_percent is not None else "—",
            "rpm": num(fan.rpm, grouping=True) if fan.rpm is not None else "—",
            "voltage": f"{num(fan.voltage_v, decimals=2)} V" if fan.voltage_v is not None else "—",
            "current": f"{num(fan.current_a, decimals=2)} A" if fan.current_a is not None else "—",
            "power": f"{num(fan.power_w, decimals=2)} W" if fan.power_w is not None else "—",
            "state": self.tr_.t(f"fan.state.{fan.state.value}"),
        }
        for key, value in fan_rows.items():
            self.fan_rows[key].set_value(value)
        self.mock_badge.setVisible(bool(status.is_mock))

    def _refresh_diagnostics(self, connection) -> None:
        """Fill the Connection Center rows from the manager's diagnostics."""
        diag = connection.diagnostics
        num = self.tr_.number

        def ms(value: float | None) -> str:
            return f"{num(value, decimals=0)} ms" if value is not None else "—"

        age = diag.telemetry_age_s()
        state = connection.state
        rows = {
            "state": self.tr_.t(f"conn.{state.value}"),
            "transport": diag.transport or ("websocket" if self.services.config.esp32.use_websocket else "tcp"),
            "endpoint": diag.endpoint or "—",
            "connect": ms(diag.connect_ms),
            "handshake": ms(diag.handshake_ms),
            "first_telemetry": ms(diag.time_to_first_telemetry_ms),
            "freshness": f"{num(age, decimals=1)} s" if age is not None else "—",
            "reconnects": num(diag.reconnect_count),
            "failures": num(diag.consecutive_failures),
            "last_error": diag.last_error or "—",
        }
        for key, value in rows.items():
            self.diag_rows[key].set_value(str(value))

    def retranslate(self) -> None:
        if not self._built:
            return
        self.connect_button.setText(self.tr_.t("common.connect"))
        self.mock_button.setText(self.tr_.t("esp32.use_mock"))
        self.disconnect_button.setText(self.tr_.t("common.disconnect"))
        self.discover_button.setText(self.tr_.t("esp32.discover"))
        self.retry_button.setText(self.tr_.t("esp32.retry"))


__all__ = ["Esp32Page"]
