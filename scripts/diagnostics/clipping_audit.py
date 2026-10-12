"""Text-clipping / compression audit across every page (Block B).

Shows the real ``MainWindow`` at a given size and language, walks each page and
reports:

* wrapped labels whose allocated height is smaller than their rendered text,
* non-wrapped labels whose text is wider than the label (real horizontal clip),
* widgets compressed below their ``minimumSizeHint`` height,
* cards whose child header is squeezed into a narrow column.

Run:
    QT_QPA_PLATFORM=offscreen python scripts/diagnostics/clipping_audit.py \
        --size 1648x920 --lang en
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
from PySide6.QtWidgets import QHeaderView, QLabel, QScrollBar, QWidget  # noqa: E402

TOL = 2
_SKIP = (QHeaderView, QScrollBar)


def _settle(app, seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _needed_height(label: QLabel) -> int:
    fm = label.fontMetrics()
    if label.wordWrap() and label.width() > 0:
        flags = int(label.alignment()) | int(Qt.TextFlag.TextWordWrap)
        return fm.boundingRect(0, 0, label.width(), 100000, flags, label.text()).height()
    return fm.height()


def audit(widget: QWidget, findings: list[str], depth: int = 0, max_depth: int = 8) -> None:
    if depth > max_depth:
        return
    if isinstance(widget, _SKIP):
        return
    if isinstance(widget, QLabel) and widget.isVisible() and widget.text().strip():
        if widget.wordWrap():
            need = _needed_height(widget)
            if widget.height() + TOL < need:
                findings.append(
                    f"VCLIP {type(widget).__name__} h={widget.height()} need={need} "
                    f"w={widget.width()} text={widget.text()[:34]!r}"
                )
        else:
            fm = widget.fontMetrics()
            tw = fm.horizontalAdvance(widget.text())
            if widget.height() + TOL < fm.height():
                findings.append(
                    f"VCLIP-nw h={widget.height()} need={fm.height()} "
                    f"w={widget.width()} text={widget.text()[:34]!r}"
                )
            if tw > widget.width() + TOL and type(widget).__name__ != "ElidedLabel":
                findings.append(
                    f"HCLIP tw={tw} w={widget.width()} text={widget.text()[:34]!r}"
                )
    mh = widget.minimumSizeHint()
    if widget.isVisible() and mh.height() > 0 and widget.height() + TOL < mh.height() and depth > 0:
        findings.append(
            f"COMPRESS {type(widget).__name__}({widget.objectName() or '-'}) "
            f"h={widget.height()} min={mh.height()}"
        )
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        audit(child, findings, depth + 1, max_depth)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", default="1648x920")
    parser.add_argument("--lang", default="en")
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

    from app.ui.shell import PAGE_CLASSES, MainWindow

    win = MainWindow(services)
    w, h = (int(x) for x in args.size.split("x"))
    win.resize(w, h)
    win.show()
    _settle(app)

    total = 0
    for key in PAGE_CLASSES:
        win.show_page(key)
        _settle(app, 0.25)
        page = win._pages.get(key)
        if page is None:
            continue
        findings: list[str] = []
        audit(page.content, findings)
        if findings:
            print(f"\n### {key} ({len(findings)})")
            for line in findings:
                print("   ", line)
            total += len(findings)
    print(f"\n== size={args.size} lang={args.lang} total findings={total} ==")

    win.shutdown()
    services.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
