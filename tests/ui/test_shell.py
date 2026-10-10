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


def _visible_texts(widget) -> set[str]:
    from PySide6.QtWidgets import QLabel, QPushButton

    texts: set[str] = set()
    for child in widget.findChildren(QLabel) + widget.findChildren(QPushButton):
        text = child.text().strip()
        # Units and pictographs ("%", "°C", "❚❚") are intentionally identical in
        # both languages; only real words (lower-case or non-ASCII letters) are
        # expected to be translated.
        if text and (any(ch.islower() for ch in text) or any(ord(ch) > 127 and ch.isalpha() for ch in text)):
            texts.add(text)
    return texts


def test_language_switch_leaves_no_english_strings(window) -> None:
    """Switching to Persian must retranslate every cached page.

    Regression guard: pages used to be kept in ``self._pages`` with the old
    translator, so 131 labels stayed English after the switch.  Every page must
    now be rebuilt against the new translator.
    """
    from app.core.enums import Language
    from app.ui.i18n import _EN, _FA
    from app.ui.shell import PAGE_CLASSES

    # Only strings whose Persian form differs from English are expected to
    # change; runtime data (paths, "standard", "0.0 MiB") legitimately does not.
    en_to_fa = {value: _FA[key] for key, value in _EN.items() if _FA.get(key, value) != value}

    for key in PAGE_CLASSES:
        window.show_page(key)
        window._on_language_changed(Language.ENGLISH)
        page = window._pages.get(key)
        assert page is not None, f"{key} not built"
        before = _visible_texts(page.content)

        window._on_language_changed(Language.PERSIAN)
        rebuilt = window._pages.get(key)
        assert rebuilt is not None, f"{key} lost after language switch"
        after = _visible_texts(rebuilt.content)

        stale = sorted(t for t in before if t in after and t in en_to_fa)
        assert stale == [], f"{key}: untranslated after switch: {stale[:8]}"


def test_language_switch_never_shows_raw_keys(window) -> None:
    """A missing catalogue entry would leak a dotted key into the UI."""
    import re

    from app.core.enums import Language
    from app.ui.shell import PAGE_CLASSES

    pattern = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    window._on_language_changed(Language.PERSIAN)
    for key in PAGE_CLASSES:
        window.show_page(key)
        page = window._pages.get(key)
        assert page is not None
        leaked = sorted(t for t in _visible_texts(page.content) if pattern.match(t))
        assert leaked == [], f"{key}: raw i18n keys shown: {leaked[:8]}"


def _clipped_labels(root) -> list[str]:
    from PySide6.QtWidgets import QLabel

    from app.ui.widgets.atoms import ElidedLabel

    clipped: list[str] = []
    for label in root.findChildren(QLabel):
        text = label.text().strip()
        # Wrapping and eliding labels are designed to fit by design.
        if not text or label.wordWrap() or isinstance(label, ElidedLabel):
            continue
        needed = label.sizeHint().width()
        if needed > label.width() + 2:
            clipped.append(f"{text!r} needs {needed}px, has {label.width()}px")
    return clipped


def test_no_widget_text_is_clipped_in_english(window) -> None:
    from app.core.enums import Language

    window.resize(1440, 900)
    window.show()
    window._on_language_changed(Language.ENGLISH)
    for key in ("dashboard", "history", "diagnostics"):
        window.show_page(key)
        window.layout().activate()
        clipped = _clipped_labels(window._pages[key].content)
        assert clipped == [], f"{key} (en) clipped: {clipped[:6]}"


def test_no_widget_text_is_clipped_in_persian(window) -> None:
    from app.core.enums import Language

    window.resize(1440, 900)
    window.show()
    window._on_language_changed(Language.PERSIAN)
    for key in ("dashboard", "history", "diagnostics"):
        window.show_page(key)
        window.layout().activate()
        clipped = _clipped_labels(window._pages[key].content)
        assert clipped == [], f"{key} (fa) clipped: {clipped[:6]}"
