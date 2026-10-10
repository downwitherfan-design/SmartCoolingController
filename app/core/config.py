"""Typed, persisted application configuration.

Configuration is stored as human-readable JSON in the resolved ``Config``
directory so that a portable install carries its settings with it and so that
support can inspect it safely.  Writes are atomic (temp file + replace) to
survive unexpected shutdowns (spec §211).

No secrets are stored here: ESP32 pairing keys live in the database, and no
passwords are ever written to disk (spec §231, §269).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import (
    AnimationLevel,
    ChartRange,
    DataMode,
    Language,
    PerformanceMode,
    ThemeMode,
)

CONFIG_FILENAME = "settings.json"
CONFIG_SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class GeneralSettings(_Model):
    first_run_completed: bool = False
    start_with_windows: bool = False
    start_minimized: bool = False
    auto_connect_esp32: bool = True
    start_telemetry: bool = True
    minimize_to_tray: bool = True
    close_to_tray: bool = True
    confirm_exit: bool = False
    single_instance: bool = True


class TelemetrySettings(_Model):
    sample_interval_ms: int = 500
    ui_update_interval_ms: int = 500
    sensor_refresh_interval_s: int = 300
    record_history: bool = True
    persist_interval_s: int = 5
    stale_after_s: float = 5.0

    @field_validator("sample_interval_ms", "ui_update_interval_ms")
    @classmethod
    def _validate_interval(cls, value: int) -> int:
        if value not in {250, 500, 1000, 2000, 5000}:
            raise ValueError("interval must be one of 250/500/1000/2000/5000 ms")
        return value


class CoolingSettings(_Model):
    active_profile: str = "balanced"
    auto_profile_switching: bool = False
    use_max_of_cpu_gpu: bool = True


class FanSettings(_Model):
    min_pwm: int = 0
    max_pwm: int = 100
    ramp_up_pct_per_s: float = 8.0
    ramp_down_pct_per_s: float = 4.0
    hysteresis_c: float = 3.0
    thermal_weight_cpu: float = 0.6
    thermal_weight_gpu: float = 0.4
    power_weight: float = 0.2
    load_weight: float = 0.1
    stall_rpm_threshold: int = 300


class Esp32Settings(_Model):
    last_device_id: str = ""
    last_host: str = ""
    manual_host: str = ""
    port: int = 8765
    discovery_port: int = 8766
    connect_timeout_s: float = 5.0
    heartbeat_interval_s: float = 1.0
    heartbeat_timeout_s: float = 3.0
    #: Discovery is bounded: a broadcast window plus an overall scan budget.
    discovery_timeout_s: float = 2.0
    discovery_enabled: bool = True
    #: Bounded exponential backoff with jitter.  The schedule is the base delay
    #: per attempt; jitter is applied as a fraction of each delay.
    reconnect_backoff_s: list[float] = Field(
        default_factory=lambda: [1.0, 2.0, 4.0, 8.0, 15.0, 30.0]
    )
    reconnect_jitter: float = 0.25
    #: A session must stay healthy at least this long before the backoff resets.
    healthy_reset_s: float = 5.0
    require_authentication: bool = True
    use_websocket: bool = True
    send_interval_ms: int = 500

    @field_validator("heartbeat_interval_s", "heartbeat_timeout_s", "connect_timeout_s")
    @classmethod
    def _positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout must be positive")
        return value

    @field_validator("reconnect_jitter")
    @classmethod
    def _bounded_jitter(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("jitter must be between 0 and 1")
        return value

    @field_validator("reconnect_backoff_s")
    @classmethod
    def _valid_backoff(cls, value: list[float]) -> list[float]:
        if not value or any(delay <= 0 for delay in value):
            raise ValueError("backoff schedule must contain positive delays")
        return value


class ChartSettings(_Model):
    default_range: ChartRange = ChartRange.M5
    show_grid: bool = True
    show_legend: bool = True
    auto_scale: bool = True
    max_live_points: int = 3600
    series_visibility: dict[str, bool] = Field(default_factory=dict)


class AlertSettings(_Model):
    cpu_warning_c: float = 80.0
    cpu_critical_c: float = 92.0
    gpu_warning_c: float = 78.0
    gpu_critical_c: float = 90.0
    storage_warning_c: float = 65.0
    windows_notifications: bool = False
    notification_cooldown_s: float = 120.0
    rapid_rise_c_per_min: float = 8.0

    @field_validator("cpu_warning_c", "gpu_warning_c", "storage_warning_c")
    @classmethod
    def _validate_warning(cls, value: float) -> float:
        if not 0 < value < 130:
            raise ValueError("threshold out of range")
        return value

    @field_validator("cpu_critical_c", "gpu_critical_c")
    @classmethod
    def _validate_critical(cls, value: float) -> float:
        if not 0 < value < 150:
            raise ValueError("threshold out of range")
        return value


class DataSettings(_Model):
    mode: DataMode = DataMode.STANDARD
    raw_retention_hours: int = 24
    aggregate_1m_retention_days: int = 30
    aggregate_5m_retention_days: int = 180
    log_retention_days: int = 14
    auto_backup_before_migration: bool = True
    auto_backup_days: int = 7


class AppearanceSettings(_Model):
    theme: ThemeMode = ThemeMode.DARK
    accent: str = "cyan"
    animation_level: AnimationLevel = AnimationLevel.NORMAL
    show_glow: bool = True
    compact_opacity: float = 0.92
    compact_always_on_top: bool = True
    compact_position_locked: bool = False
    compact_position: list[int] = Field(default_factory=list)
    dashboard_preset: str = "standard"
    hidden_cards: list[str] = Field(default_factory=list)
    card_order: list[str] = Field(default_factory=list)
    favorite_metrics: list[str] = Field(default_factory=list)
    view_mode: str = "simple"


class LocalizationSettings(_Model):
    language: Language = Language.ENGLISH


class PerformanceSettings(_Model):
    mode: PerformanceMode = PerformanceMode.NORMAL
    developer_mode: bool = False


class PrivacySettings(_Model):
    local_only: bool = True
    send_telemetry_to_esp32: bool = True


class DiagnosticsSettings(_Model):
    log_level: str = "INFO"
    measure_app_performance: bool = False


class WindowState(_Model):
    width: int = 1440
    height: int = 900
    x: int = -1
    y: int = -1
    maximized: bool = False
    selected_page: str = "dashboard"


class AppConfig(_Model):
    """Root configuration object."""

    schema_version: int = CONFIG_SCHEMA_VERSION
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    cooling: CoolingSettings = Field(default_factory=CoolingSettings)
    fan: FanSettings = Field(default_factory=FanSettings)
    esp32: Esp32Settings = Field(default_factory=Esp32Settings)
    charts: ChartSettings = Field(default_factory=ChartSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    localization: LocalizationSettings = Field(default_factory=LocalizationSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    diagnostics: DiagnosticsSettings = Field(default_factory=DiagnosticsSettings)
    window: WindowState = Field(default_factory=WindowState)


class ConfigStore:
    """Loads and saves :class:`AppConfig` with atomic writes."""

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        self._path = config_dir / CONFIG_FILENAME
        self._config = AppConfig()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def config(self) -> AppConfig:
        return self._config

    def load(self) -> AppConfig:
        """Read configuration, tolerating corruption by using defaults."""
        if not self._path.exists():
            self._config = AppConfig()
            return self._config
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("configuration root must be an object")
            self._config = AppConfig.model_validate(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            # Keep a copy of the broken file for support, then start clean.
            with contextlib.suppress(OSError):
                self._path.replace(self._path.with_suffix(".json.corrupt"))
            self._config = AppConfig()
        return self._config

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = self._config.model_dump(mode="json")
        fd, tmp_name = tempfile.mkstemp(
            prefix=".settings-", suffix=".tmp", dir=str(self._dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
        finally:
            if os.path.exists(tmp_name):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)

    def update(self, **sections: Any) -> AppConfig:
        """Patch one or more top-level sections and persist."""
        for name, values in sections.items():
            section = getattr(self._config, name, None)
            if section is None:
                raise KeyError(f"unknown configuration section: {name}")
            if isinstance(values, dict):
                for key, value in values.items():
                    setattr(section, key, value)
            else:
                setattr(self._config, name, values)
        self.save()
        return self._config


__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_SCHEMA_VERSION",
    "AlertSettings",
    "AppConfig",
    "AppearanceSettings",
    "ChartSettings",
    "ConfigStore",
    "CoolingSettings",
    "DataSettings",
    "DiagnosticsSettings",
    "Esp32Settings",
    "FanSettings",
    "GeneralSettings",
    "LocalizationSettings",
    "PerformanceSettings",
    "PrivacySettings",
    "TelemetrySettings",
    "WindowState",
]
