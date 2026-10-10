"""System diagram page (spec §31, §32, §98).

The schematic plus a live detail panel.  Clicking a node shows that component's
current readings; the panel is rebuilt from the last snapshot so it always
reflects the newest values without a second data path.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.core.enums import ConnectionState
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import Page
from app.ui.widgets.atoms import Card, KeyValueRow, SectionHeader, StatusBadge
from app.ui.widgets.schematic import NODE_DETAIL_GROUPS, Schematic

#: Tone used for the connection badge on the diagram detail panel.
_STATE_TONES: dict[ConnectionState, str] = {
    ConnectionState.CONNECTED: "success",
    ConnectionState.DEGRADED: "warning",
    ConnectionState.STALE: "warning",
    ConnectionState.PAIRING: "info",
    ConnectionState.CONNECTING: "info",
    ConnectionState.DISCOVERING: "info",
    ConnectionState.RECONNECTING: "info",
    ConnectionState.PROTOCOL_MISMATCH: "danger",
    ConnectionState.UNAUTHORIZED: "danger",
    ConnectionState.ERROR: "danger",
}


class DiagramPage(Page):
    key = "diagram"
    title_key = "nav.diagram"
    subtitle_key = "dash.subtitle"
    icon_name = "diagram"

    def _build_content(self, content: QWidget) -> None:
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])

        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        diagram_card = Card()
        diagram_layout = QVBoxLayout(diagram_card)
        diagram_layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        self.schematic = Schematic(self.theme, self.tr_)
        self.schematic.node_clicked.connect(self._select_node)
        diagram_layout.addWidget(self.schematic, 1)
        row.addWidget(diagram_card, 7)

        detail_card = Card()
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        detail_layout.setSpacing(SPACE["sm"])
        self.detail_header = SectionHeader(_field_label(self.tr_, "esp32"))
        detail_layout.addWidget(self.detail_header)
        self.detail_badge = StatusBadge(self.theme, self.tr_, "", tone="text_faint")
        detail_layout.addWidget(self.detail_badge, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail_rows: dict[str, KeyValueRow] = {}
        self.detail_body = QVBoxLayout()
        self.detail_body.setSpacing(SPACE["xs"])
        detail_layout.addLayout(self.detail_body)
        detail_layout.addStretch(1)
        row.addWidget(detail_card, 3)
        layout.addLayout(row, 1)

        self._selected = "esp32"
        self._build_detail_rows("esp32")
        self.schematic.set_active_node("esp32")

    # -- detail panel ------------------------------------------------------

    def _build_detail_rows(self, key: str) -> None:
        while self.detail_body.count():
            item = self.detail_body.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.detail_rows.clear()
        for field in NODE_DETAIL_GROUPS.get(key, ()):
            row = KeyValueRow(_field_label(self.tr_, field), "—", mono=True)
            self.detail_rows[field] = row
            self.detail_body.addWidget(row)

    def _select_node(self, key: str) -> None:
        self._selected = key
        self.detail_header.set_text(_field_label(self.tr_, key))
        self._build_detail_rows(key)
        self.schematic.set_active_node(key)
        if self._last_snapshot is not None:
            self._fill_detail(self._last_snapshot)

    # -- data --------------------------------------------------------------

    _last_snapshot: TelemetrySnapshot | None = None

    def retranslate(self) -> None:
        if not self._built:
            return
        self.detail_header.set_text(_field_label(self.tr_, self._selected))
        self._build_detail_rows(self._selected)
        if self._last_snapshot is not None:
            self._fill_detail(self._last_snapshot)

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        self._last_snapshot = snapshot
        fan = snapshot.fan
        state = self.services.connection.state if hasattr(self.services.connection, "state") else ConnectionState.DISCONNECTED
        self.schematic.set_link_state(state, fan_state=fan.state, rpm=fan.rpm)
        self.detail_badge.set_status(
            self.tr_.t(f"conn.{state.value}"), _STATE_TONES.get(state, "text_faint")
        )
        self._fill_detail(snapshot)

    def teardown(self) -> None:
        # The schematic runs a 33 ms timer; stop it before the page (and its
        # language-switch rebuild) drops the widget, or it keeps firing.
        self.schematic.stop_animation()
        super().teardown()

    def _fill_detail(self, snapshot: TelemetrySnapshot) -> None:
        values = self._node_values(self._selected, snapshot)
        for field, row in self.detail_rows.items():
            value = values.get(field)
            row.set_value(value if value is not None else "—")

    def _node_values(self, key: str, snapshot: TelemetrySnapshot) -> dict[str, str]:
        cpu, gpu, fan, pad = snapshot.cpu, snapshot.gpu, snapshot.fan, snapshot.pad
        conn = self.services.connection
        num = self.tr_.number
        values: dict[str, str] = {}
        if key == "laptop":
            values = {
                "CPU": f"{num(cpu.temperature, decimals=1)} °C" if cpu.temperature is not None else "—",
                "GPU": f"{num(gpu.temperature, decimals=1)} °C" if gpu.temperature is not None else "—",
                "RAM": f"{num(snapshot.memory.usage_percent, decimals=0)} %" if snapshot.memory.usage_percent else "—",
                "Storage": snapshot.storage.name or "—",
                "Telemetry": f"{num(1 / self.services.telemetry.interval_s, decimals=1)} Hz"
                if self.services.telemetry.interval_s
                else "—",
            }
        elif key == "fan":
            values = {
                "PWM": f"{num(fan.pwm_percent, decimals=0)} %" if fan.pwm_percent is not None else "—",
                "RPM": num(fan.rpm, grouping=True) if fan.rpm is not None else "—",
                "current": f"{num(fan.current_a, decimals=2)} A" if fan.current_a is not None else "—",
                "voltage": f"{num(fan.voltage_v, decimals=2)} V" if fan.voltage_v is not None else "—",
                "power": f"{num(fan.power_w, decimals=2)} W" if fan.power_w is not None else "—",
            }
        elif key == "pwm":
            values = {
                "duty": f"{num(fan.target_percent, decimals=0)} %" if fan.target_percent is not None else "—",
                "frequency": "25 kHz",
            }
        elif key == "tach":
            values = {
                "RPM": num(fan.rpm, grouping=True) if fan.rpm is not None else "—",
                "pulses_per_rev": "2",
            }
        elif key == "esp32":
            status = conn.status
            device = conn.device
            values = {
                "device_id": status.device_id or (device.device_id if device else "—"),
                "ip": (device.host if device else "—") or "—",
                "firmware": status.firmware_version or "—",
                "protocol": str(status.protocol_version or "—"),
                "uptime": f"{num(status.uptime_s, decimals=0)} s" if status.uptime_s else "—",
                "state": self.tr_.t(f"conn.{conn.state.value}"),
            }
        elif key == "wifi":
            # Never fabricate "online": the Wi-Fi node is only up when the link
            # is actually delivering telemetry, and RSSI/latency come from the
            # controller's real readings.
            status = conn.status
            online = conn.state is ConnectionState.CONNECTED and status.wifi_rssi_dbm is not None
            values = {
                "status": self.tr_.t("common.online") if online else self.tr_.t("common.offline"),
                "rssi": f"{num(status.wifi_rssi_dbm, decimals=0)} dBm"
                if status.wifi_rssi_dbm is not None
                else "—",
                "latency": f"{num(status.latency_ms, decimals=1)} ms"
                if status.latency_ms is not None
                else "—",
                "packets": f"{num(status.packet_loss_percent, decimals=1)} %"
                if status.packet_loss_percent is not None
                else "—",
            }
        elif key == "ina219":
            values = {
                "voltage": f"{num(fan.voltage_v, decimals=2)} V" if fan.voltage_v is not None else "—",
                "current": f"{num(fan.current_a, decimals=2)} A" if fan.current_a is not None else "—",
                "power": f"{num(fan.power_w, decimals=2)} W" if fan.power_w is not None else "—",
            }
        elif key == "ds18b20":
            values = {
                "temperature": f"{num(pad.temperature, decimals=1)} °C" if pad.temperature is not None else "—",
                "state": self.tr_.t("common.available") if pad.available else self.tr_.t("common.unavailable"),
            }
        elif key == "telemetry":
            values = {
                "sampling_rate": f"{num(1 / self.services.telemetry.interval_s, decimals=1)} Hz"
                if self.services.telemetry.interval_s
                else "—",
                "sensors": num(len(self.services.telemetry.sensors)),
            }
        return values


def _field_label(tr, field: str) -> str:
    """Translate a node/field identifier, falling back to a title-cased name."""
    key = f"field.{field.lower()}"
    translated = tr.t(key)
    return translated if translated != key else field.replace("_", " ").title()


__all__ = ["DiagramPage"]
