"""Fan control page (spec §39, §40, §100, §101, §102).

Three control surfaces in one page:

* **Mode** — Automatic / Manual / Safe / Calibration.  Manual reveals a PWM
  slider; Safe explains that firmware will hold maximum cooling.
* **Profile** — the five built-in curves, with the active one highlighted.
* **Curve** — a draggable editor that writes back to the profile.

Safety is explicit: the page states that the ESP32 firmware is the final
authority (spec §100) and disables editing for built-in profiles unless the user
copies them to a custom profile.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from app.core.enums import ControllerMode
from app.models.telemetry import TelemetrySnapshot
from app.ui.design import SPACE
from app.ui.pages.base import ScrollPage
from app.ui.widgets.atoms import Card, Divider, SectionHeader, StatusBadge
from app.ui.widgets.curve import CurvePoint, FanCurveEditor
from app.ui.widgets.fan import FanReadout, FanVisualizer
from app.ui.widgets.gauges import PowerMetric

_MODE_LABELS = {
    ControllerMode.AUTO: "fan.mode.auto",
    ControllerMode.MANUAL: "fan.mode.manual",
    ControllerMode.SAFE: "fan.mode.safe",
    ControllerMode.CALIBRATION: "fan.mode.calibration",
}


class FanPage(ScrollPage):
    key = "fan"
    title_key = "fan.title"
    subtitle_key = "fan.subtitle"
    icon_name = "fan"

    def build_body(self) -> None:
        self._build_control_row()
        self._build_curve_row()
        self._build_response_row()
        self._sync_profile_buttons()

    # -- sections ----------------------------------------------------------

    def _build_control_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])

        fan_card = Card()
        layout = QVBoxLayout(fan_card)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])
        layout.addWidget(SectionHeader(self.tr_.t("dash.fan_target"), self.tr_.t("fan.subtitle")))
        body = QHBoxLayout()
        body.setSpacing(SPACE["lg"])
        self.fan_visual = FanVisualizer(self.theme, self.tr_)
        body.addWidget(self.fan_visual, 3)
        self.fan_readout = FanReadout(self.theme, self.tr_)
        body.addWidget(self.fan_readout, 2)
        layout.addLayout(body, 1)
        row.addWidget(fan_card, 6)

        control_card = Card()
        control = QVBoxLayout(control_card)
        control.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        control.setSpacing(SPACE["md"])
        control.addWidget(SectionHeader(self.tr_.t("fan.mode")))

        self.enable_badge = StatusBadge(self.theme, self.tr_, self.tr_.t("fan.enabled"), tone="success")
        control.addWidget(self.enable_badge, 0, Qt.AlignmentFlag.AlignLeft)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACE["xs"])
        self._mode_buttons: dict[ControllerMode, QPushButton] = {}
        for mode in (ControllerMode.AUTO, ControllerMode.MANUAL, ControllerMode.SAFE, ControllerMode.CALIBRATION):
            button = QPushButton(self.tr_.t(_MODE_LABELS[mode]))
            button.setProperty("segmented", "true")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, m=mode: self._set_mode(m))
            mode_row.addWidget(button)
            self._mode_buttons[mode] = button
        mode_row.addStretch(1)
        control.addLayout(mode_row)

        control.addWidget(Divider())

        self.manual_label = QLabel(self.tr_.t("fan.manual_pwm"))
        self.manual_label.setProperty("role", "caption")
        control.addWidget(self.manual_label)
        manual_row = QHBoxLayout()
        self.manual_slider = QSlider(Qt.Orientation.Horizontal)
        self.manual_slider.setRange(0, 100)
        self.manual_slider.valueChanged.connect(self._on_manual_changed)
        manual_row.addWidget(self.manual_slider, 1)
        self.manual_value = QLabel("0%")
        self.manual_value.setProperty("role", "mono")
        self.manual_value.setMinimumWidth(44)
        manual_row.addWidget(self.manual_value)
        control.addLayout(manual_row)
        self._manual_visible = False
        self.manual_slider.setVisible(False)
        self.manual_value.setVisible(False)

        control.addWidget(Divider())
        control.addWidget(SectionHeader(self.tr_.t("fan.profile")))
        self.profile_combo = QComboBox()
        for profile in self.services.profiles.all():
            self.profile_combo.addItem(profile.name, profile.key)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        control.addWidget(self.profile_combo)

        control.addWidget(Divider())
        safety = QLabel(self.tr_.t("about.safety_text"))
        safety.setProperty("role", "caption")
        safety.setWordWrap(True)
        control.addWidget(safety)
        control.addStretch(1)
        row.addWidget(control_card, 4)
        self.body.addLayout(row)

    def _build_curve_row(self) -> None:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])
        header_row = QHBoxLayout()
        header_row.addWidget(SectionHeader(self.tr_.t("fan.curve"), self.tr_.t("fan.curve.hint")), 1)
        self.curve = FanCurveEditor(self.theme, self.tr_)
        self.add_point_button = QPushButton(self.tr_.t("fan.add_point"))
        self.add_point_button.clicked.connect(lambda: self.curve.add_point())
        self.remove_point_button = QPushButton(self.tr_.t("fan.remove_point"))
        self.remove_point_button.clicked.connect(lambda: self.curve.remove_selected())
        header_row.addWidget(self.add_point_button)
        header_row.addWidget(self.remove_point_button)
        layout.addLayout(header_row)
        self.curve.curve_changed.connect(self._on_curve_changed)
        self.curve.setMinimumHeight(300)
        layout.addWidget(self.curve, 1)
        self.body.addWidget(card)

    def _build_response_row(self) -> None:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])
        layout.addWidget(SectionHeader(self.tr_.t("fan.response")))
        metrics = QHBoxLayout()
        metrics.setSpacing(SPACE["lg"])
        self.power_metric = PowerMetric(self.theme, self.tr_, label=self.tr_.t("common.power"), unit="W")
        self.efficiency_metric = PowerMetric(self.theme, self.tr_, label="ΔT / W", unit="°C/W")
        self.pwm_metric = PowerMetric(self.theme, self.tr_, label=self.tr_.t("dash.fan_pwm"), unit="%")
        for widget in (self.power_metric, self.efficiency_metric, self.pwm_metric):
            metrics.addWidget(widget)
        layout.addLayout(metrics)
        self.response_badge = StatusBadge(self.theme, self.tr_, "", tone="text_faint")
        layout.addWidget(self.response_badge, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.addWidget(card)

    # -- actions -----------------------------------------------------------

    def _set_mode(self, mode: ControllerMode) -> None:
        self.services.cooling.set_mode(mode)
        for name, button in self._mode_buttons.items():
            button.setChecked(name == mode)
        visible = mode is ControllerMode.MANUAL
        self.manual_slider.setVisible(visible)
        self.manual_value.setVisible(visible)
        if mode is ControllerMode.SAFE:
            self.message.emit(self.tr_.t("fan.mode.safe"), "warning")
        if mode is ControllerMode.MANUAL:
            self.services.cooling.set_manual_pwm(float(self.manual_slider.value()))

    def _on_manual_changed(self, value: int) -> None:
        self.manual_value.setText(f"{self.tr_.number(value, decimals=0)}%")
        if self.services.cooling.mode is ControllerMode.MANUAL:
            self.services.cooling.set_manual_pwm(float(value))

    def _on_profile_changed(self, index: int) -> None:
        key = self.profile_combo.itemData(index)
        if not key:
            return
        profile = self.services.profiles.get(key)
        if profile is None:
            return
        self.services.profiles.set_active(key)
        self.services.cooling.set_profile(profile)
        self.curve.set_points(
            [CurvePoint(p.temperature_c, p.duty_percent) for p in profile.curve.points]
        )
        self.curve.set_read_only(profile.builtin)
        self.remove_point_button.setEnabled(not profile.builtin)
        self.add_point_button.setEnabled(not profile.builtin)

    def _on_curve_changed(self, points: list[CurvePoint]) -> None:
        profile = self.services.profiles.active
        if profile is None or profile.builtin:
            return
        # Persisted by the profile service on the next save; kept in-memory here.

    def _sync_profile_buttons(self) -> None:
        active = self.services.profiles.active_key
        index = self.profile_combo.findData(active)
        if index >= 0:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(index)
            self.profile_combo.blockSignals(False)
        self._on_profile_changed(self.profile_combo.currentIndex())
        mode = self.services.cooling.mode
        for name, button in self._mode_buttons.items():
            button.setChecked(name == mode)
        self.enable_badge.set_status(
            self.tr_.t("fan.enabled") if self.services.cooling.enabled else self.tr_.t("common.none"),
            self.theme.color("success" if self.services.cooling.enabled else "text_faint"),
        )

    # -- data --------------------------------------------------------------

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        fan = snapshot.fan
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
        self.power_metric.set_metric(fan.power_w, tone="warm")
        self.pwm_metric.set_metric(fan.pwm_percent, tone="primary")
        delta = snapshot.thermal.headroom_c if snapshot.thermal else None
        efficiency = (delta / fan.power_w) if (delta is not None and fan.power_w) else None
        self.efficiency_metric.set_metric(efficiency, tone="info")
        self.response_badge.set_status(
            self.tr_.t(f"fan.state.{fan.state.value}"),
            self.theme.response_color(fan.response),
        )

    def retranslate(self) -> None:
        if not self._built:
            return
        for mode, button in self._mode_buttons.items():
            button.setText(self.tr_.t(_MODE_LABELS[mode]))
        self.manual_label.setText(self.tr_.t("fan.manual_pwm"))
        self.add_point_button.setText(self.tr_.t("fan.add_point"))
        self.remove_point_button.setText(self.tr_.t("fan.remove_point"))
