"""Temporary geometry probe (Block B of the GUI repair sprint).

Constructs the real ``MainWindow`` against the simulated services, shows it at a
requested size, lets Qt settle the layout, then walks the widget tree of a page
and prints the geometry facts needed to find why a child is narrower/shorter
than its parent card.

Run:
    QT_QPA_PLATFORM=offscreen python scripts/diagnostics/geometry_probe.py \
        --page esp32 --size 1648x920 --lang en
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402


def _short(text: str, limit: int = 30) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _settle(app, seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def dump(widget: QWidget, depth: int = 0, max_depth: int = 6) -> None:
    if depth > max_depth:
        return
    g = widget.geometry()
    sh = widget.sizeHint()
    mh = widget.minimumSizeHint()
    pol = widget.sizePolicy()
    extra = ""
    if isinstance(widget, QLabel):
        fm = widget.fontMetrics()
        need_h = fm.height()
        if widget.wordWrap() and widget.width() > 0:
            need_h = fm.boundingRect(
                0, 0, widget.width(), 100000, int(widget.alignment()), widget.text()
            ).height()
        extra = (
            f" text={_short(widget.text())!r} wrap={widget.wordWrap()}"
            f" fm.h={need_h} vis={widget.isVisible()}"
        )
    print(
        f"{'  ' * depth}{type(widget).__name__}({widget.objectName() or '-'}) "
        f"g=({g.x()},{g.y()},{g.width()}x{g.height()}) "
        f"hint={sh.width()}x{sh.height()} min={mh.width()}x{mh.height()} "
        f"pol=({pol.horizontalPolicy().name},{pol.verticalPolicy().name}){extra}"
    )
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        dump(child, depth + 1, max_depth)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", default="esp32")
    parser.add_argument("--size", default="1648x920")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--depth", type=int, default=6)
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from app.core.enums import Language
    from app.services import ApplicationServices

    os.environ.setdefault("SMARTCOOLING_DATA_MODE", "standard")
    services = ApplicationServices()
    services.config.general.start_telemetry = False
    services.config.general.auto_connect_esp32 = False
    services.config.general.first_run_completed = True
    services.config.localization.language = Language(args.lang.lower())
    services.enable_simulation()
    services.start(simulate=True)

    from app.ui.shell import MainWindow

    win = MainWindow(services)
    w, h = (int(x) for x in args.size.split("x"))
    win.resize(w, h)
    win.show()
    _settle(app)
    win.show_page(args.page)
    _settle(app)

    screen = app.primaryScreen()
    print(
        f"== screen={screen.geometry().width()}x{screen.geometry().height()} "
        f"dpr={win.devicePixelRatioF()} win={win.width()}x{win.height()} =="
    )
    page = win._pages.get(args.page)
    if page is None:
        print("page not built")
    else:
        dump(page.content, max_depth=args.depth)

    win.shutdown()
    services.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
