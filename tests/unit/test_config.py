"""Unit tests for configuration loading, saving and migration safety.

Configuration is user-writable state that lives outside the app, so it must
never crash the program: corrupt or partial files degrade to defaults, and a
patch round-trips through disk intact (spec §35, §36, §38, §238).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import AppConfig, ConfigStore
from app.core.enums import DataMode, Language, ThemeMode


def test_defaults_are_sane() -> None:
    config = AppConfig()
    assert config.localization.language is Language.ENGLISH
    assert config.appearance.theme is ThemeMode.DARK
    assert config.data.mode in {DataMode.PORTABLE, DataMode.STANDARD}
    assert config.general.first_run_completed is False


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "Config")
    config = store.load()
    assert config.localization.language is Language.ENGLISH
    assert not store.path.exists()


def test_patch_round_trips_through_disk(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "Config")
    store.load()
    store.update(appearance={"theme": ThemeMode.LIGHT, "accent": "amber"})
    store.update(localization={"language": Language.PERSIAN})

    reloaded = ConfigStore(tmp_path / "Config")
    config = reloaded.load()
    assert config.appearance.theme is ThemeMode.LIGHT
    assert config.appearance.accent == "amber"
    assert config.localization.language is Language.PERSIAN


def test_corrupt_file_is_quarantined_and_defaults_used(tmp_path: Path) -> None:
    config_dir = tmp_path / "Config"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text("{ this is not valid json", encoding="utf-8")

    store = ConfigStore(config_dir)
    config = store.load()
    assert config.localization.language is Language.ENGLISH
    assert (config_dir / "settings.json.corrupt").exists()


def test_non_object_root_is_treated_as_corrupt(tmp_path: Path) -> None:
    config_dir = tmp_path / "Config"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    store = ConfigStore(config_dir)
    config = store.load()
    assert isinstance(config, AppConfig)
    assert (config_dir / "settings.json.corrupt").exists()


def test_unknown_keys_are_ignored_not_fatal(tmp_path: Path) -> None:
    config_dir = tmp_path / "Config"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text(
        json.dumps({"localization": {"language": "fa"}, "totallyUnknown": 1}),
        encoding="utf-8",
    )
    config = ConfigStore(config_dir).load()
    assert config.localization.language is Language.PERSIAN


def test_unknown_section_patch_raises(tmp_path: Path) -> None:
    import pytest

    store = ConfigStore(tmp_path / "Config")
    store.load()
    with pytest.raises(KeyError):
        store.update(nonexistent={"x": 1})


def test_invalid_value_is_rejected_by_validation(tmp_path: Path) -> None:
    import pytest
    from pydantic import ValidationError

    store = ConfigStore(tmp_path / "Config")
    store.load()
    with pytest.raises(ValidationError):
        store.update(localization={"language": "klingon"})


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    config_dir = tmp_path / "Config"
    store = ConfigStore(config_dir)
    store.load()
    store.save()
    leftovers = [p.name for p in config_dir.iterdir() if p.name.startswith(".settings-")]
    assert leftovers == []
    assert (config_dir / "settings.json").exists()


def test_compact_position_defaults_to_empty() -> None:
    assert AppConfig().appearance.compact_position == []


def test_window_state_defaults_are_valid() -> None:
    window = AppConfig().window
    assert window.width > 0 and window.height > 0
    assert window.selected_page
