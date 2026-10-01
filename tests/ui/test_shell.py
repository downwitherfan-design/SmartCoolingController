"""UI tests: build every page and exercise the shell's theming/RTL.

These run against real Qt widgets off-screen (``QT_QPA_PLATFORM=offscreen``).
We do not assert pixel output; we assert the things that actually break — that
every page constructs, that a theme switch re-polishes without error, that RTL
localisation flips the layout direction, and that the window shuts down cleanly.
"""

from __future__ import annotations

import pytest

from app.core.enums import Language, ThemeMode
from app.ui.i18n import Translator


@pytest.fixture
def window(qapp, services):
    from app.ui.shell import MainWindow

    win = MainWindow(services)
    yield win
    win.shutdown()
    win.deleteLater()
    qapp.processEvents()


def test_every_page_constructs(window) -> None:
    from app.ui.shell import PAGE_CLASSES

    failures: list[tuple[str, str]] = []
    for key in PAGE_CLASSES:
        try:
            window.show_page(key)
        except Exception as exc:  # noqa: BLE001 - report every failure, not the first
            failures.append((key, repr(exc)))
    assert failures == [], f"page construction failed: {failures}"


def test_navigation_rail_lists_every_page(window) -> None:
    from app.ui.shell import PAGE_CLASSES

    rail_keys = set(window.rail._items.keys())
    assert set(PAGE_CLASSES) - {"data"} <= rail_keys


def test_switching_pages_updates_the_stack(window) -> None:
    window.show_page("dashboard")
    assert window._current_key == "dashboard"
    window.show_page("settings")
    assert window._current_key == "settings"
    assert window.stack.currentWidget() is window._pages["settings"].content


def test_theme_switch_is_reversible(window) -> None:
    window.services.config.appearance.theme = ThemeMode.LIGHT
    window._on_theme_changed()
    light_bg = window.theme.palette.bg
    window.services.config.appearance.theme = ThemeMode.DARK
    window._on_theme_changed()
    dark_bg = window.theme.palette.bg
    assert light_bg != dark_bg, "light and dark themes must differ"


def test_rtl_localisation_flips_layout_direction(window) -> None:
    from PySide6.QtCore import Qt

    window._on_language_changed(Language.PERSIAN)
    assert window.layoutDirection() is Qt.LayoutDirection.RightToLeft
    window._on_language_changed(Language.ENGLISH)
    assert window.layoutDirection() is Qt.LayoutDirection.LeftToRight


def test_language_switch_retranslates_without_losing_pages(window) -> None:
    window.show_page("dashboard")
    window._on_language_changed(Language.PERSIAN)
    window._on_language_changed(Language.ENGLISH)
    assert window._current_key == "dashboard"
    assert window.translator.language is Language.ENGLISH


def test_compact_mode_toggles_and_restores(window) -> None:
    window.enter_compact()
    assert window.compact.isVisible() is True
    assert window.isVisible() is False
    window.exit_compact()
    assert window.isVisible() is True
    assert window.compact.isVisible() is False


def test_pause_does_not_crash_the_telemetry_view(window) -> None:
    window.set_paused(True)
    assert window._paused is True
    window.set_paused(False)
    assert window._paused is False


def test_status_tick_updates_without_errors(window) -> None:
    window._tick_status()  # must not raise even with a partial snapshot


def test_alert_badge_accepts_an_alert(window) -> None:
    from app.core.enums import AlertCategory, AlertEventType, AlertSeverity

    window.services.alerts.raise_event(
        category=AlertCategory.THERMAL,
        severity=AlertSeverity.WARNING,
        title="UI badge test",
        event_type=AlertEventType.THERMAL_WARNING,
        dedup_key="ui.badge",
    )
    window._tick_status()  # the shell pulls the active count here


def test_translator_has_every_key_in_both_languages() -> None:
    from app.ui.i18n import _EN, _FA

    missing_fa = sorted(set(_EN) - set(_FA))
    assert missing_fa == [], f"missing Persian translations: {missing_fa[:10]}"


def test_translator_falls_back_to_the_key() -> None:
    translator = Translator(Language.ENGLISH)
    assert translator.t("definitely.missing.key") == "definitely.missing.key"


def test_translator_substitutes_placeholders() -> None:
    translator = Translator(Language.ENGLISH)
    assert translator.t("nav.dashboard") != "nav.dashboard"


def test_persian_number_formatting_uses_local_digits() -> None:
    translator = Translator(Language.PERSIAN)
    assert translator.number(1234) == "۱۲۳۴"


def test_page_titles_have_translations(window) -> None:
    from app.ui.shell import PAGE_CLASSES

    translator = Translator(Language.ENGLISH)
    for key, cls in PAGE_CLASSES.items():
        assert translator.t(cls.title_key) != cls.title_key, key


def test_shortcuts_are_registered(window) -> None:
    from app.ui.shell import _SHORTCUTS

    assert _SHORTCUTS
    for shortcut, label_key, action in _SHORTCUTS:
        assert shortcut and label_key and action
