"""Chart settings wiring (spec §33, §34).

The chart preferences (default range, auto-scale, grid, legend, per-series
visibility) are persisted in ``ChartSettings``.  These tests assert the settings
actually reach the live plot and that a user toggle is written back — the
regression class where a setting exists in the schema but is never applied.

The window shows the dashboard during construction, so settings that seed a
page must be set *before* ``MainWindow`` is created.
"""

from __future__ import annotations

import contextlib

from PySide6.QtWidgets import QApplication


@contextlib.contextmanager
def _window(qapp, services):
    from app.ui.shell import MainWindow

    win = MainWindow(services)
    try:
        yield win
    finally:
        win.shutdown()
        win.deleteLater()
        QApplication.processEvents()


def test_series_visibility_is_seeded_from_settings(qapp, services) -> None:
    services.config.charts.series_visibility = {"gpu": False}
    with _window(qapp, services) as window:
        panel = window._pages["dashboard"].chart_panel
        assert panel.chart._series["gpu"].visible is False
        assert panel.chart._series["cpu"].visible is True


def test_toggling_a_series_is_persisted(qapp, services) -> None:
    with _window(qapp, services) as window:
        panel = window._pages["dashboard"].chart_panel
        panel._series_buttons["cpu"].setChecked(False)
        assert services.config.charts.series_visibility.get("cpu") is False
        assert panel.chart._series["cpu"].visible is False


def test_chart_display_settings_reach_the_plot(qapp, services) -> None:
    services.config.charts.auto_scale = False
    services.config.charts.show_grid = False
    services.config.charts.show_legend = False
    with _window(qapp, services) as window:
        live = window._pages["dashboard"].chart_panel.chart
        assert live._auto_scale is False
        assert live._show_grid is False
        assert live._legend_visible is False


def test_history_opens_on_the_persisted_default_range(qapp, services) -> None:
    from app.core.enums import ChartRange

    services.config.charts.default_range = ChartRange.H24
    with _window(qapp, services) as window:
        window.show_page("history")
        assert window._pages["history"]._range == "h24"


def test_history_keeps_user_choice_after_first_show(qapp, services) -> None:
    with _window(qapp, services) as window:
        window.show_page("history")
        page = window._pages["history"]
        page.reload("d7")
        window.show_page("diagnostics")
        window.show_page("history")
        assert page._range == "d7"
