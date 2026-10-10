"""Settings page (spec §81, §85, §131, §132, §221, §245, §246).

The whole application is configurable from one searchable surface.  Each
section maps 1:1 to a section of :class:`~app.core.config.AppConfig`, so what
the UI writes is exactly what is persisted — no hidden state.

Design notes:

* Changes apply **immediately** and are persisted atomically.  A monitoring tool
  that loses settings on a crash is not trustworthy (spec §211).
* The search box filters rows across every section, which is the fastest way to
  reach a setting whose location you do not remember (spec §131).
* Theme, accent, animation, glow, language and performance are announced with
  signals so the shell can rebuild the world without restarting (spec §245-§247).
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.enums import (
    AnimationLevel,
    DataMode,
    Language,
    PerformanceMode,
    ThemeMode,
)
from app.models.telemetry import TelemetrySnapshot
from app.services.alerts import default_rules
from app.ui.design import ACCENTS, SPACE
from app.ui.pages.base import Page
from app.ui.widgets.atoms import EmptyState, SectionHeader
from app.ui.widgets.forms import FormCard, SettingsCombo, SettingsSlider, SettingsSpin, Switch

_SAMPLE_INTERVALS = [250, 500, 1000, 2000, 5000]


class SettingsPage(Page):
    key = "settings"
    title_key = "settings.title"
    subtitle_key = "settings.subtitle"
    icon_name = "settings"
    wants_snapshots = False

    #: Emitted when the resolved theme inputs change and the shell must rebuild.
    theme_changed = Signal()
    #: Emitted after the language changed so the shell can retranslate everything.
    language_changed = Signal(object)
    #: Emitted when the performance profile changed.
    performance_changed = Signal(object)

    def _build_content(self, content: QWidget) -> None:
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header_wrap = QWidget()
        header_layout = QVBoxLayout(header_wrap)
        header_layout.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["sm"])
        header_layout.setSpacing(SPACE["md"])
        header_layout.addWidget(SectionHeader(self.tr_.t("settings.title"), self.tr_.t("settings.subtitle")))
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr_.t("settings.search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search, 1)
        self.reset_button = QPushButton(self.tr_.t("settings.reset"))
        self.reset_button.clicked.connect(self._reset)
        search_row.addWidget(self.reset_button)
        header_layout.addLayout(search_row)
        outer.addWidget(header_wrap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("Root")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(SPACE["xl"], SPACE["sm"], SPACE["xl"], SPACE["xxl"])
        self.body.setSpacing(SPACE["lg"])
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self.empty = EmptyState(self.theme, self.tr_, icon_name="settings", title=self.tr_.t("common.empty"))
        self.empty.setVisible(False)
        self.body.addWidget(self.empty)

        self._cards: list[FormCard] = []
        self._labels: list[tuple[Any, str]] = []

        self._build_general()
        self._build_telemetry()
        self._build_cooling()
        self._build_fan()
        self._build_alerts()
        self._build_data()
        self._build_appearance()
        self._build_localization()
        self._build_performance()
        self._build_privacy()
        self._build_diagnostics()
        self.body.addStretch(1)

    # -- section builders --------------------------------------------------

    def _card(self, title_key: str, description_key: str = "") -> FormCard:
        card = FormCard(
            self.theme,
            self.tr_,
            self.tr_.t(title_key),
            description=self.tr_.t(description_key) if description_key else "",
        )
        self.body.addWidget(card)
        self._cards.append(card)
        self._labels.append((card.header, title_key))
        if description_key:
            self._labels.append((card.header, description_key))
        return card

    def _row(self, card: FormCard, title_key: str, control: QWidget, hint_key: str = "") -> None:
        title = self.tr_.t(title_key)
        hint = self.tr_.t(hint_key) if hint_key else ""
        row = card.add_row(
            title,
            control,
            description=hint,
            search_text=f"{title} {hint} {title_key} {hint_key}",
        )
        self._labels.append((row, title_key))
        if hint_key:
            self._labels.append((row, hint_key))

    def _switch(self, value: bool, handler) -> Switch:
        switch = Switch(self.theme, checked=value)
        switch.toggled.connect(handler)
        return switch

    def _build_general(self) -> None:
        config = self.services.config.general
        card = self._card("settings.general")
        self._row(
            card,
            "settings.start_with_windows",
            self._switch(config.start_with_windows, lambda v: self._store("general", start_with_windows=v)),
        )
        self._row(
            card,
            "settings.start_minimized",
            self._switch(config.start_minimized, lambda v: self._store("general", start_minimized=v)),
        )
        self._row(
            card,
            "settings.auto_connect",
            self._switch(config.auto_connect_esp32, lambda v: self._store("general", auto_connect_esp32=v)),
        )
        self._row(
            card,
            "settings.start_telemetry",
            self._switch(config.start_telemetry, lambda v: self._store("general", start_telemetry=v)),
        )
        self._row(
            card,
            "settings.minimize_to_tray",
            self._switch(config.minimize_to_tray, lambda v: self._store("general", minimize_to_tray=v)),
        )
        self._row(
            card,
            "settings.close_to_tray",
            self._switch(config.close_to_tray, lambda v: self._store("general", close_to_tray=v)),
        )
        self._row(
            card,
            "settings.confirm_exit",
            self._switch(config.confirm_exit, lambda v: self._store("general", confirm_exit=v)),
        )
        self._row(
            card,
            "settings.single_instance",
            self._switch(config.single_instance, lambda v: self._store("general", single_instance=v)),
        )

    def _build_telemetry(self) -> None:
        config = self.services.config.telemetry
        card = self._card("settings.telemetry")
        interval = SettingsCombo(
            [(f"{ms} {self.tr_.t('common.ms')}", ms) for ms in _SAMPLE_INTERVALS],
            current=config.sample_interval_ms,
        )
        interval.changed.connect(self._on_sample_interval)
        self._row(card, "settings.sample_interval", interval, "settings.sample_interval_hint")

        ui_interval = SettingsCombo(
            [(f"{ms} {self.tr_.t('common.ms')}", ms) for ms in _SAMPLE_INTERVALS],
            current=config.ui_update_interval_ms,
        )
        ui_interval.changed.connect(lambda v: self._store("telemetry", ui_update_interval_ms=v))
        self._row(card, "settings.ui_interval", ui_interval)

        self._row(
            card,
            "settings.record_history",
            self._switch(config.record_history, lambda v: self._store("telemetry", record_history=v)),
            "settings.record_history_hint",
        )
        persist = SettingsSpin(
            minimum=1, maximum=120, value=config.persist_interval_s, suffix=self.tr_.t("common.seconds")
        )
        persist.changed.connect(lambda v: self._store("telemetry", persist_interval_s=float(v)))
        self._row(card, "settings.persist_interval", persist)

        stale = SettingsSpin(
            minimum=1, maximum=60, step=0.5, decimals=1,
            value=config.stale_after_s, suffix=self.tr_.t("common.seconds"),
        )
        stale.changed.connect(lambda v: self._store("telemetry", stale_after_s=float(v)))
        self._row(card, "settings.stale_after", stale)

    def _build_cooling(self) -> None:
        config = self.services.config.cooling
        card = self._card("settings.cooling")
        profiles = self.services.profiles.all()
        profile_combo = SettingsCombo(
            [(profile.name, profile.key) for profile in profiles],
            current=config.active_profile,
        )
        profile_combo.changed.connect(self._on_profile)
        self._row(card, "settings.active_profile", profile_combo)
        self._row(
            card,
            "settings.auto_profile",
            self._switch(config.auto_profile_switching, lambda v: self._store("cooling", auto_profile_switching=v)),
            "settings.auto_profile_hint",
        )
        self._row(
            card,
            "settings.max_of_cpu_gpu",
            self._switch(config.use_max_of_cpu_gpu, lambda v: self._store("cooling", use_max_of_cpu_gpu=v)),
        )

    def _build_fan(self) -> None:
        config = self.services.config.fan
        card = self._card("settings.fan")
        self._fan_slider(card, "settings.fan_min_pwm", config.min_pwm, 0, 100, 1, 0, "%", "min_pwm")
        self._fan_slider(card, "settings.fan_max_pwm", config.max_pwm, 0, 100, 1, 0, "%", "max_pwm")
        self._fan_slider(card, "settings.ramp_up", config.ramp_up_pct_per_s, 0.5, 50, 0.5, 1, "%/s", "ramp_up_pct_per_s")
        self._fan_slider(card, "settings.ramp_down", config.ramp_down_pct_per_s, 0.5, 50, 0.5, 1, "%/s", "ramp_down_pct_per_s")
        self._fan_slider(card, "settings.hysteresis", config.hysteresis_c, 0, 15, 0.5, 1, "°C", "hysteresis_c")
        self._fan_slider(card, "settings.cpu_weight", config.thermal_weight_cpu, 0, 1, 0.05, 2, "", "thermal_weight_cpu")
        self._fan_slider(card, "settings.gpu_weight", config.thermal_weight_gpu, 0, 1, 0.05, 2, "", "thermal_weight_gpu")
        self._fan_slider(card, "settings.power_weight", config.power_weight, 0, 1, 0.05, 2, "", "power_weight")
        self._fan_slider(card, "settings.load_weight", config.load_weight, 0, 1, 0.05, 2, "", "load_weight")
        stall = SettingsSpin(minimum=0, maximum=3000, step=50, value=config.stall_rpm_threshold, suffix=self.tr_.t("unit.rpm"))
        stall.changed.connect(lambda v: self._store("fan", stall_rpm_threshold=int(v)))
        self._row(card, "settings.stall_rpm", stall)

    def _fan_slider(
        self,
        card: FormCard,
        title_key: str,
        value: float,
        minimum: float,
        maximum: float,
        step: float,
        decimals: int,
        suffix: str,
        field: str,
    ) -> None:
        slider = SettingsSlider(
            minimum=minimum, maximum=maximum, step=step, value=value,
            decimals=decimals, suffix=suffix,
        )
        slider.changed.connect(lambda v, name=field: self._store("fan", **{name: float(v)}))
        self._row(card, title_key, slider)

    def _build_alerts(self) -> None:
        config = self.services.config.alerts
        card = self._card("settings.alerts")
        for title_key, field, value in (
            ("settings.cpu_warning", "cpu_warning_c", config.cpu_warning_c),
            ("settings.cpu_critical", "cpu_critical_c", config.cpu_critical_c),
            ("settings.gpu_warning", "gpu_warning_c", config.gpu_warning_c),
            ("settings.gpu_critical", "gpu_critical_c", config.gpu_critical_c),
            ("settings.storage_warning", "storage_warning_c", config.storage_warning_c),
        ):
            spin = SettingsSpin(minimum=0, maximum=130, step=1, value=value, suffix="°C")
            spin.changed.connect(lambda v, name=field: self._on_threshold(name, float(v)))
            self._row(card, title_key, spin)

        cooldown = SettingsSpin(
            minimum=0, maximum=3600, step=10, value=config.notification_cooldown_s,
            suffix=self.tr_.t("common.seconds"),
        )
        cooldown.changed.connect(lambda v: self._on_cooldown(float(v)))
        self._row(card, "settings.notification_cooldown", cooldown)

        rise = SettingsSpin(minimum=1, maximum=60, step=0.5, decimals=1, value=config.rapid_rise_c_per_min, suffix="°C/min")
        rise.changed.connect(lambda v: self._store("alerts", rapid_rise_c_per_min=float(v)))
        self._row(card, "settings.rapid_rise", rise)

        self._row(
            card,
            "settings.notifications",
            self._switch(config.windows_notifications, self._on_notifications),
        )

    def _build_data(self) -> None:
        config = self.services.config.data
        card = self._card("settings.data")
        mode = SettingsCombo(
            [
                (self.tr_.t("settings.data_mode.standard"), DataMode.STANDARD),
                (self.tr_.t("settings.data_mode.portable"), DataMode.PORTABLE),
            ],
            current=config.mode,
        )
        mode.changed.connect(lambda v: self._store("data", mode=v))
        self._row(card, "settings.data_mode", mode, "settings.data_mode_hint")

        raw = SettingsSpin(minimum=1, maximum=720, step=1, value=config.raw_retention_hours, suffix=self.tr_.t("common.hours"))
        raw.changed.connect(lambda v: self._store("data", raw_retention_hours=int(v)))
        self._row(card, "settings.raw_retention", raw)

        agg1 = SettingsSpin(minimum=1, maximum=3650, step=1, value=config.aggregate_1m_retention_days, suffix=self.tr_.t("common.days"))
        agg1.changed.connect(lambda v: self._store("data", aggregate_1m_retention_days=int(v)))
        self._row(card, "settings.agg1m_retention", agg1)

        agg5 = SettingsSpin(minimum=1, maximum=3650, step=1, value=config.aggregate_5m_retention_days, suffix=self.tr_.t("common.days"))
        agg5.changed.connect(lambda v: self._store("data", aggregate_5m_retention_days=int(v)))
        self._row(card, "settings.agg5m_retention", agg5)

        logs = SettingsSpin(minimum=1, maximum=3650, step=1, value=config.log_retention_days, suffix=self.tr_.t("common.days"))
        logs.changed.connect(lambda v: self._store("data", log_retention_days=int(v)))
        self._row(card, "settings.log_retention", logs)

        self._row(
            card,
            "settings.auto_backup",
            self._switch(config.auto_backup_before_migration, lambda v: self._store("data", auto_backup_before_migration=v)),
        )
        backup_days = SettingsSpin(minimum=1, maximum=90, step=1, value=config.auto_backup_days, suffix=self.tr_.t("common.days"))
        backup_days.changed.connect(lambda v: self._store("data", auto_backup_days=int(v)))
        self._row(card, "settings.auto_backup_days", backup_days)

    def _build_appearance(self) -> None:
        config = self.services.config.appearance
        card = self._card("settings.appearance")
        theme_combo = SettingsCombo(
            [
                (self.tr_.t("settings.theme.dark"), ThemeMode.DARK),
                (self.tr_.t("settings.theme.light"), ThemeMode.LIGHT),
                (self.tr_.t("settings.theme.system"), ThemeMode.SYSTEM),
            ],
            current=config.theme,
        )
        theme_combo.changed.connect(self._on_theme)
        self._row(card, "settings.theme", theme_combo)

        accent_combo = SettingsCombo(
            [(name.title(), name) for name in ACCENTS],
            current=config.accent,
        )
        accent_combo.changed.connect(self._on_accent)
        self._row(card, "settings.accent", accent_combo, "settings.accent_hint")

        animation_combo = SettingsCombo(
            [
                (self.tr_.t("settings.animations.off"), AnimationLevel.OFF),
                (self.tr_.t("settings.animations.low"), AnimationLevel.LOW),
                (self.tr_.t("settings.animations.normal"), AnimationLevel.NORMAL),
                (self.tr_.t("settings.animations.high"), AnimationLevel.HIGH),
            ],
            current=config.animation_level,
        )
        animation_combo.changed.connect(self._on_animation)
        self._row(card, "settings.animations", animation_combo)

        self._row(
            card,
            "settings.glow",
            self._switch(config.show_glow, self._on_glow),
        )

        preset = SettingsCombo(
            [
                (self.tr_.t("settings.preset.standard"), "standard"),
                (self.tr_.t("settings.preset.minimal"), "minimal"),
                (self.tr_.t("settings.preset.power"), "power"),
            ],
            current=config.dashboard_preset,
        )
        preset.changed.connect(lambda v: self._store("appearance", dashboard_preset=v))
        self._row(card, "settings.dashboard_preset", preset)

        view = SettingsCombo(
            [
                (self.tr_.t("settings.view.simple"), "simple"),
                (self.tr_.t("settings.view.advanced"), "advanced"),
            ],
            current=config.view_mode,
        )
        view.changed.connect(lambda v: self._store("appearance", view_mode=v))
        self._row(card, "settings.view_mode", view)

        opacity = SettingsSlider(minimum=0.4, maximum=1.0, step=0.02, value=config.compact_opacity, decimals=2)
        opacity.changed.connect(lambda v: self._store("appearance", compact_opacity=float(v)))
        self._row(card, "settings.compact_opacity", opacity)

        self._row(
            card,
            "settings.compact_on_top",
            self._switch(config.compact_always_on_top, lambda v: self._store("appearance", compact_always_on_top=v)),
        )
        self._row(
            card,
            "settings.compact_lock",
            self._switch(config.compact_position_locked, lambda v: self._store("appearance", compact_position_locked=v)),
        )

    def _build_localization(self) -> None:
        config = self.services.config.localization
        card = self._card("settings.localization")
        language_combo = SettingsCombo(
            [("English", Language.ENGLISH), ("فارسی", Language.PERSIAN)],
            current=config.language,
        )
        language_combo.changed.connect(self._on_language)
        self._row(card, "settings.language", language_combo)

    def _build_performance(self) -> None:
        config = self.services.config.performance
        card = self._card("settings.performance")
        mode = SettingsCombo(
            [
                (self.tr_.t("settings.animations.normal"), PerformanceMode.NORMAL),
                (self.tr_.t("settings.lightweight"), PerformanceMode.LIGHTWEIGHT),
            ],
            current=config.mode,
        )
        mode.changed.connect(self._on_performance)
        self._row(card, "settings.performance_mode", mode, "settings.performance_hint")
        self._row(
            card,
            "settings.developer_mode",
            self._switch(config.developer_mode, lambda v: self._store("performance", developer_mode=v)),
        )

    def _build_privacy(self) -> None:
        config = self.services.config.privacy
        card = self._card("settings.privacy")
        self._row(
            card,
            "settings.local_only",
            self._switch(config.local_only, lambda v: self._store("privacy", local_only=v)),
            "settings.local_only_hint",
        )
        self._row(
            card,
            "settings.send_telemetry_esp32",
            self._switch(config.send_telemetry_to_esp32, lambda v: self._store("privacy", send_telemetry_to_esp32=v)),
            "settings.send_telemetry_esp32_hint",
        )

    def _build_diagnostics(self) -> None:
        config = self.services.config.diagnostics
        card = self._card("settings.diagnostics")
        level = SettingsCombo(
            [
                (self.tr_.t("settings.level.debug"), "DEBUG"),
                (self.tr_.t("settings.level.info"), "INFO"),
                (self.tr_.t("settings.level.warning"), "WARNING"),
                (self.tr_.t("settings.level.error"), "ERROR"),
            ],
            current=config.log_level.upper(),
        )
        level.changed.connect(lambda v: self._store("diagnostics", log_level=v))
        self._row(card, "settings.log_level", level)
        self._row(
            card,
            "settings.measure_performance",
            self._switch(config.measure_app_performance, lambda v: self._store("diagnostics", measure_app_performance=v)),
        )

    # -- handlers ----------------------------------------------------------

    def _store(self, section: str, **values: Any) -> None:
        self.services.update_config(**{section: values})
        self.message.emit(self.tr_.t("settings.saved"), "success")

    def _on_sample_interval(self, milliseconds: int) -> None:
        self._store("telemetry", sample_interval_ms=milliseconds)
        self.services.telemetry.set_interval(milliseconds / 1000.0)

    def _on_profile(self, key: str) -> None:
        self._store("cooling", active_profile=key)
        profile = self.services.profiles.set_active(key)
        self.services.cooling.set_profile(profile)

    def _on_threshold(self, field: str, value: float) -> None:
        self._store("alerts", **{field: value})
        self._rebuild_rules()

    def _on_cooldown(self, seconds: float) -> None:
        self._store("alerts", notification_cooldown_s=seconds)
        self.services.alerts.set_cooldown(seconds)

    def _on_notifications(self, enabled: bool) -> None:
        self._store("alerts", windows_notifications=enabled)
        self.services.alerts.set_notifications_enabled(enabled)

    def _rebuild_rules(self) -> None:
        config = self.services.config.alerts
        self.services.alerts.set_rules(
            default_rules(
                cpu_warning=config.cpu_warning_c,
                cpu_critical=config.cpu_critical_c,
                gpu_warning=config.gpu_warning_c,
                gpu_critical=config.gpu_critical_c,
                storage_warning=config.storage_warning_c,
            )
        )

    def _on_theme(self, mode: ThemeMode) -> None:
        self._store("appearance", theme=mode)
        self.theme_changed.emit()

    def _on_accent(self, accent: str) -> None:
        self._store("appearance", accent=accent)
        self.theme_changed.emit()

    def _on_animation(self, level: AnimationLevel) -> None:
        self._store("appearance", animation_level=level)
        self.theme_changed.emit()

    def _on_glow(self, enabled: bool) -> None:
        self._store("appearance", show_glow=enabled)
        self.theme_changed.emit()

    def _on_language(self, language: Language) -> None:
        self.services.update_config(localization={"language": language})
        self.tr_.set_language(language)
        self.language_changed.emit(language)

    def _on_performance(self, mode: PerformanceMode) -> None:
        self._store("performance", mode=mode)
        self.performance_changed.emit(mode)

    # -- search / reset ----------------------------------------------------

    def _apply_filter(self) -> None:
        query = self.search.text().strip().lower()
        visible_rows = 0
        for card in self._cards:
            card_visible = 0
            for row in card.rows():
                haystack = str(row.property("search") or "")
                matches = not query or query in haystack
                row.setVisible(matches)
                card_visible += 1 if matches else 0
            card.setVisible(card_visible > 0)
            visible_rows += card_visible
        self.empty.setVisible(visible_rows == 0 and bool(query))

    def _reset(self) -> None:
        self.services.config_store.update(
            telemetry={
                "sample_interval_ms": 500,
                "ui_update_interval_ms": 500,
                "record_history": True,
                "persist_interval_s": 5,
                "stale_after_s": 5.0,
            },
            fan={
                "min_pwm": 0,
                "max_pwm": 100,
                "ramp_up_pct_per_s": 8.0,
                "ramp_down_pct_per_s": 4.0,
                "hysteresis_c": 3.0,
                "thermal_weight_cpu": 0.6,
                "thermal_weight_gpu": 0.4,
                "power_weight": 0.2,
                "load_weight": 0.1,
                "stall_rpm_threshold": 300,
            },
            alerts={
                "cpu_warning_c": 80.0,
                "cpu_critical_c": 92.0,
                "gpu_warning_c": 78.0,
                "gpu_critical_c": 90.0,
                "storage_warning_c": 65.0,
                "notification_cooldown_s": 120.0,
                "rapid_rise_c_per_min": 8.0,
            },
            appearance={
                "theme": ThemeMode.DARK,
                "accent": "cyan",
                "animation_level": AnimationLevel.NORMAL,
                "show_glow": True,
            },
        )
        self.services.telemetry.set_interval(0.5)
        self._rebuild_rules()
        self.services.alerts.set_cooldown(120.0)
        self.message.emit(self.tr_.t("settings.reset"), "info")
        self.theme_changed.emit()
        # Rebuild the form so every control reflects the restored defaults.
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._labels.clear()
        self._build_general()
        self._build_telemetry()
        self._build_cooling()
        self._build_fan()
        self._build_alerts()
        self._build_data()
        self._build_appearance()
        self._build_localization()
        self._build_performance()
        self._build_privacy()
        self._build_diagnostics()

    def refresh(self, snapshot: TelemetrySnapshot) -> None:
        return

    def retranslate(self) -> None:
        if not self._built:
            return
        self.search.setPlaceholderText(self.tr_.t("settings.search"))
        self.reset_button.setText(self.tr_.t("settings.reset"))
        for widget, key in self._labels:
            text = self.tr_.t(key)
            if isinstance(widget, FormCard):
                widget.header.set_text(text, widget.header.subtitle.text())
            elif hasattr(widget, "set_title"):
                widget.set_title(text)
        # Rebuild combo labels (they are baked in at construction time).
        self._relabel_combos()

    def _relabel_combos(self) -> None:
        mapping = {
            "settings.theme.dark": ThemeMode.DARK,
            "settings.theme.light": ThemeMode.LIGHT,
            "settings.theme.system": ThemeMode.SYSTEM,
        }
        combos = cast("list[QComboBox]", self.findChildren(QComboBox))
        for combo in combos:
            for index in range(combo.count()):
                data = combo.itemData(index)
                if data in mapping:
                    combo.setItemText(index, self.tr_.t(next(k for k, v in mapping.items() if v is data)))


__all__ = ["SettingsPage"]
