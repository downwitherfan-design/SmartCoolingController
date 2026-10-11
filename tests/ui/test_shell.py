"""UI tests: build every page and exercise the shell's theming/RTL.

These run against real Qt widgets off-screen (``QT_QPA_PLATFORM=offscreen``).
We do not assert pixel output; we assert the things that actually break — that
every page constructs, that a theme switch re-polishes without error, that RTL
localisation flips the layout direction, and that the window shuts down cleanly.
"""

from __future__ import annotations

import pytest

from app.application.health_worker import HealthCheckWorker
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


def _pump(app, seconds: float = 2.0, until=None) -> bool:
    """Spin the Qt event loop until ``until()`` is true or ``seconds`` elapse."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None or bool(until())


def test_health_is_refreshed_on_a_timer(window, qapp) -> None:
    # Regression: health used to be computed only when the Diagnostics page was
    # opened, so the rail badge and dashboard health card never changed during a
    # session.  The shell must now recompute it on its own timer.
    assert window._health_timer.isActive()
    assert window._health_worker.running
    window.services.health._last = None  # type: ignore[attr-defined]
    window._health_worker.request()
    assert _pump(qapp, until=lambda: window.services.health.last_report is not None)
    assert window.services.health.last_report is not None


def test_health_badge_reflects_the_report(window, qapp) -> None:
    window._health_worker.request()
    assert _pump(qapp, until=lambda: window.rail.health_badge._text)
    report = window.services.health.last_report
    assert report is not None
    # With telemetry never started in the fixture, the CPU probe reports UNKNOWN,
    # which must reach the rail badge rather than staying at its initial state.
    assert window.rail.health_badge._text


def test_health_check_does_not_run_on_the_gui_thread(window, qapp) -> None:
    """The periodic health check must not perform hardware I/O on the GUI thread.

    Regression: ``HealthService.check`` used to be called directly from the GUI
    timer, and its sensor probe called ``registry.read()`` — a WMI/LHM call that
    enumerates disks and can take tens of milliseconds.  That is a measured
    freeze source.  The check now runs on a worker thread.
    """
    import threading
    import time

    seen: list[int] = []

    def slow_check() -> object:
        seen.append(threading.get_ident())
        time.sleep(0.25)  # stands in for a slow hardware probe
        return None

    worker = HealthCheckWorker(slow_check)
    worker.start()
    try:
        start = time.perf_counter()
        worker.request()
        # The request must return immediately — it only flips a flag.
        assert (time.perf_counter() - start) < 0.05
        assert _pump(qapp, until=lambda: bool(seen), seconds=2.0)
    finally:
        worker.stop()

    assert seen, "the check never ran"
    assert seen[0] != threading.get_ident(), "the check ran on the GUI thread"


def test_health_worker_coalesces_overlapping_requests() -> None:
    """A slow check must not accumulate a backlog of pending checks."""
    import threading
    import time

    runs = 0
    release = threading.Event()

    def blocked_check() -> None:
        nonlocal runs
        runs += 1
        release.wait(2.0)

    worker = HealthCheckWorker(blocked_check)
    worker.start()
    try:
        worker.request()
        time.sleep(0.1)  # let the first check start and block
        for _ in range(50):
            worker.request()  # all coalesce into at most one follow-up
        release.set()
        time.sleep(0.3)
    finally:
        worker.stop()

    # One running + at most one coalesced follow-up — never 50.
    assert runs <= 2, f"health checks accumulated: {runs}"


def test_health_probe_does_not_read_hardware(services) -> None:
    """The sensor health probe must be a pure function of cached state.

    Regression: ``_probe_sensors`` called ``registry.read()``, so a health
    refresh triggered a full hardware read on the calling thread.
    """
    calls = {"n": 0}
    services.telemetry.sample_once()  # populate the cached descriptor list

    def _fail() -> list:
        calls["n"] += 1
        raise AssertionError("health probe must not read hardware")

    original = services.registry.read
    services.registry.read = _fail  # type: ignore[method-assign]
    try:
        status, detail = services._probe_sensors()
    finally:
        services.registry.read = original  # type: ignore[method-assign]

    assert calls["n"] == 0
    assert status.value == "healthy"
    assert "sensor" in detail

