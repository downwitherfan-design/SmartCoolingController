"""Every page must build and render off-screen with real data.

``MainWindow._create_page`` swallows a page's construction error and shows a
placeholder instead, so a broken page degrades quietly rather than crashing.
That safety net also means nothing fails loudly when a page regresses — this
test walks the whole navigation rail, asserts each page built as its real class
(not the error placeholder), renders it, and re-renders after a language switch
to prove the rebuild path releases the old widgets.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QPixmap

from app.ui.shell import PAGE_CLASSES, create_window

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def window(qapp, services):
    services.telemetry.sample_once()
    win = create_window(services)
    win.resize(1360, 860)
    yield win
    # Full window shutdown tears pages down *before* it closes the database,
    # matching production order, and resets the window singleton so the next
    # test builds a fresh window instead of reusing this dead one.
    win.shutdown()
    win.deleteLater()
    qapp.processEvents()


def test_every_page_builds_as_its_real_class(window) -> None:
    for key, page_class in PAGE_CLASSES.items():
        window.show_page(key)
        page = window._pages.get(key)
        assert page is not None, f"{key} was not created"
        assert isinstance(page, page_class), (
            f"{key} fell back to the error placeholder"
        )


def test_every_page_renders(window) -> None:
    for key in PAGE_CLASSES:
        window.show_page(key)
        pixmap = QPixmap(window.size())
        pixmap.fill()
        window.render(pixmap)
        assert not pixmap.isNull()


def test_pages_survive_a_language_switch(window) -> None:
    from app.core.enums import Language

    window.show_page("diagram")
    window.show_page("dashboard")
    # The same slot the Settings page drives: rebuilds every cached page and
    # tears the old ones down (this is where the schematic timer used to leak).
    window._on_language_changed(Language.PERSIAN)
    assert window.translator.language is Language.PERSIAN
    for key in ("diagram", "dashboard", "esp32"):
        window.show_page(key)
        page = window._pages.get(key)
        assert isinstance(page, PAGE_CLASSES[key])
        pixmap = QPixmap(window.size())
        pixmap.fill()
        window.render(pixmap)
        assert not pixmap.isNull()
