"""Geometry / text-clipping helpers shared by the layout regression tests.

These are real measurements of a shown, laid-out widget tree (never mocks): a
label is "clipped" only when the height Qt allocated it is genuinely smaller
than the height its rendered text needs at its current width.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QScrollBar, QWidget

#: Sub-pixel slack; Qt's font metrics and the allocation can differ by a pixel.
TOL = 2
#: Qt-managed sub-controls whose geometry is not driven by our layouts.
_SKIP = (QHeaderView, QScrollBar)


def settle(app, seconds: float = 0.25) -> None:
    """Run the event loop long enough for layout/resize events to be applied."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def needed_height(label: QLabel) -> int:
    """The pixel height ``label`` needs to render its text without clipping."""
    fm = label.fontMetrics()
    if label.wordWrap() and label.width() > 0:
        flags = int(label.alignment()) | int(Qt.TextFlag.TextWordWrap)
        return fm.boundingRect(0, 0, label.width(), 100000, flags, label.text()).height()
    return fm.height()


def find_clipping(root: QWidget, max_depth: int = 12) -> list[str]:
    """Return a message for every visible label whose text is clipped."""
    findings: list[str] = []
    _walk_clipping(root, findings, 0, max_depth)
    return findings


def _walk_clipping(widget: QWidget, findings: list[str], depth: int, max_depth: int) -> None:
    if depth > max_depth or isinstance(widget, _SKIP):
        return
    if isinstance(widget, QLabel) and widget.isVisible() and widget.text().strip():
        fm = widget.fontMetrics()
        if widget.wordWrap():
            need = needed_height(widget)
            if widget.height() + TOL < need:
                findings.append(
                    f"VCLIP {type(widget).__name__} h={widget.height()} need={need} "
                    f"w={widget.width()} text={widget.text()[:40]!r}"
                )
        else:
            if widget.height() + TOL < fm.height():
                findings.append(
                    f"VCLIP-nw h={widget.height()} need={fm.height()} "
                    f"w={widget.width()} text={widget.text()[:40]!r}"
                )
            if (
                type(widget).__name__ != "ElidedLabel"
                and fm.horizontalAdvance(widget.text()) > widget.width() + TOL
            ):
                findings.append(
                    f"HCLIP tw={fm.horizontalAdvance(widget.text())} w={widget.width()} "
                    f"text={widget.text()[:40]!r}"
                )
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        _walk_clipping(child, findings, depth + 1, max_depth)


def find_compression(root: QWidget, max_depth: int = 12) -> list[str]:
    """Return a message for every visible widget squeezed below its minimum height."""
    findings: list[str] = []
    _walk_compression(root, findings, 0, max_depth)
    return findings


def _walk_compression(widget: QWidget, findings: list[str], depth: int, max_depth: int) -> None:
    if depth > max_depth or isinstance(widget, _SKIP):
        return
    if depth > 0 and widget.isVisible():
        mh = widget.minimumSizeHint()
        if mh.height() > 0 and widget.height() + TOL < mh.height():
            findings.append(
                f"COMPRESS {type(widget).__name__}({widget.objectName() or '-'}) "
                f"h={widget.height()} min={mh.height()}"
            )
    for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        _walk_compression(child, findings, depth + 1, max_depth)
