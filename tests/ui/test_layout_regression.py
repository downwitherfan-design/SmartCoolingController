"""Layout / text-clipping regression matrix (Block D of the GUI repair sprint).

These tests drive the *real* ``MainWindow`` and its pages at a matrix of window
sizes, in English and Persian, and assert that no visible text is clipped or
compressed below what it needs.  They reproduce the defects found in the
production screenshots: a ``SectionHeader`` whose title collapsed into a narrow
column, and non-scrolling pages whose cards were crushed so their labels became
thin horizontal fragments.

The window sizes are Qt *logical* sizes (the offscreen platform reports DPR 1);
they reproduce the screenshot geometry rather than a specific Windows DPI.
"""

from __future__ import annotations

import pytest

from tests.ui.layout_helpers import find_clipping, find_compression, settle

# The sizes requested by the sprint's regression matrix.
SIZES = [
    (1648, 920),  # screenshot reproduction
    (800, 600),  # compact
    (1024, 768),  # small desktop
    (1280, 720),  # short widescreen
    (1366, 768),  # laptop
    (1600, 900),  # standard desktop
    (1920, 1080),  # large desktop
]

LANGUAGES = ["en", "fa"]


@pytest.fixture
def window(qapp, services):
    """A shown ``MainWindow`` on the simulated back-end, torn down after use."""
    from app.ui.shell import MainWindow

    win = MainWindow(services)
    settle(qapp)
    yield win
    win.shutdown()


def _all_page_keys():
    from app.ui.shell import PAGE_CLASSES

    return list(PAGE_CLASSES)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("width,height", SIZES)
def test_no_text_clipping_across_pages(window, qapp, language, width, height):
    from app.core.enums import Language

    window._on_language_changed(Language(language))
    window.resize(width, height)
    window.show()
    settle(qapp, 0.35)

    problems: list[str] = []
    for key in _all_page_keys():
        window.show_page(key)
        settle(qapp, 0.2)
        page = window._pages.get(key)
        if page is None:
            continue
        for finding in find_clipping(page.content):
            problems.append(f"[{key}] {finding}")

    assert not problems, (
        f"clipped text at {width}x{height} lang={language}:\n" + "\n".join(problems)
    )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("width,height", SIZES)
def test_no_widget_compression_across_pages(window, qapp, language, width, height):
    from app.core.enums import Language

    window._on_language_changed(Language(language))
    window.resize(width, height)
    window.show()
    settle(qapp, 0.35)

    problems: list[str] = []
    for key in _all_page_keys():
        window.show_page(key)
        settle(qapp, 0.2)
        page = window._pages.get(key)
        if page is None:
            continue
        for finding in find_compression(page.content):
            problems.append(f"[{key}] {finding}")

    assert not problems, (
        f"compressed widgets at {width}x{height} lang={language}:\n" + "\n".join(problems)
    )


def test_section_header_title_owns_spare_width(window, qapp):
    """A header title must use the card's width, not collapse into a column."""
    from app.ui.widgets.atoms import SectionHeader

    window.resize(1648, 920)
    window.show()
    settle(qapp, 0.3)
    window.show_page("esp32")
    settle(qapp, 0.25)

    page = window._pages["esp32"]
    headers = page.content.findChildren(SectionHeader)
    assert headers, "expected at least one SectionHeader on the ESP32 page"
    for header in headers:
        assert header.title.width() >= header.width() * 0.9, (
            f"title width {header.title.width()} is a fraction of header width {header.width()}"
        )


def test_dashboard_headers_use_card_width(window, qapp):
    from app.ui.widgets.atoms import SectionHeader

    window.resize(1648, 920)
    window.show()
    settle(qapp, 0.3)
    window.show_page("dashboard")
    settle(qapp, 0.25)

    page = window._pages["dashboard"]
    for header in page.content.findChildren(SectionHeader):
        if header.title.text() in ("Thermal Trend", "Fan Target"):
            assert header.title.width() >= header.width() * 0.9


def test_language_change_while_running_keeps_layout(window, qapp):
    from app.core.enums import Language

    window.resize(1280, 720)
    window.show()
    settle(qapp, 0.3)

    window.show_page("esp32")
    settle(qapp, 0.2)
    window._on_language_changed(Language.PERSIAN)
    settle(qapp, 0.35)
    page = window._pages["esp32"]
    assert not find_clipping(page.content)

    window._on_language_changed(Language.ENGLISH)
    settle(qapp, 0.35)
    page = window._pages["esp32"]
    assert not find_clipping(page.content)


def test_gauge_caption_does_not_collide_with_arc(window, qapp):
    """The caption band must sit above the arc's outer stroke."""
    window.resize(1640, 920)
    window.show()
    settle(qapp, 0.3)
    window.show_page("dashboard")
    settle(qapp, 0.25)

    page = window._pages["dashboard"]
    gauge = page._gauges["cpu"]
    rect, _side = gauge._geometry()
    band = gauge.BAND
    # Caption occupies [rect.top()-BAND, rect.top()]; the arc's stroke starts at
    # rect.top() + thickness/2 > rect.top(), so the bands never overlap.
    assert rect.top() - band < rect.top()
    thickness = max(9.0, min(rect.width(), rect.height()) * 0.085)
    assert rect.top() + thickness / 2 > rect.top(), "arc must start below the caption band"
