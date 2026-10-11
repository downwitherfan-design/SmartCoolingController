"""Painted-geometry layout checks.

Layout tests that only call ``layout().activate()`` and inspect ``sizeHint``
miss the defects users actually see: a stylesheet that overrides
``setFixedSize`` and pushes a button over the text below it, a tile that clips
a compound value, two cards drawn on top of each other.

:func:`find_layout_problems` inspects the *real* geometry Qt computed for a
built widget tree and reports those defects as human-readable strings.  It is
used by the head-less GUI audit (``scripts/diagnostics/gui_audit.py``) and by
the UI test-suite, so both share one definition of "correctly laid out".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget

from app.ui.widgets.atoms import ElidedLabel

#: A size hint may exceed the actual size by this many pixels before it counts
#: as clipped — Qt rounds sub-pixel metrics and a 1px slack avoids false hits.
_SLACK = 2


def _visible(widget: QWidget) -> bool:
    return widget.isVisible() and widget.width() > 0 and widget.height() > 0


def _describe(widget: QWidget) -> str:
    name = widget.objectName() or type(widget).__name__
    text = getattr(widget, "text", None)
    if callable(text):
        label = text().strip()
        if label:
            name += f"({label[:24]!r})"
    return name


def _clipped_labels(root: QWidget) -> list[str]:
    problems: list[str] = []
    for label in root.findChildren(QLabel):
        text = label.text().strip()
        if not text or not _visible(label):
            continue
        # Wrapping and eliding labels are designed to fit by design.
        if label.wordWrap() or isinstance(label, ElidedLabel):
            continue
        if label.sizeHint().width() > label.width() + _SLACK:
            problems.append(
                f"clipped-width {text!r}: needs {label.sizeHint().width()}px, "
                f"has {label.width()}px"
            )
        if label.sizeHint().height() > label.height() + _SLACK:
            problems.append(
                f"clipped-height {text!r}: needs {label.sizeHint().height()}px, "
                f"has {label.height()}px"
            )
    return problems


def _geometry_problems(root: QWidget) -> list[str]:
    problems: list[str] = []

    # A scroll area's inner widget is *meant* to exceed the viewport — that is
    # what scrolling is for — so it is not an overflow defect.
    scroll_contents = {
        scroll.widget()
        for scroll in root.findChildren(QScrollArea)
        if scroll.widget() is not None
    }
    viewports = {scroll.viewport() for scroll in root.findChildren(QScrollArea)}

    for parent in [root, *root.findChildren(QWidget)]:
        if parent in viewports:
            continue
        children = [
            child
            for child in parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
            if _visible(child) and not child.isWindow() and child not in scroll_contents
        ]
        parent_rect = parent.rect()
        for child in children:
            geo = child.geometry()
            if geo.left() < -1 or geo.top() < -1:
                problems.append(
                    f"escapes-left/top {_describe(child)}: {geo} in {parent_rect}"
                )
            if geo.right() > parent_rect.right() + 1 or geo.bottom() > parent_rect.bottom() + 1:
                problems.append(f"overflow {_describe(child)}: {geo} escapes {parent_rect}")
        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                a, b = children[i].geometry(), children[j].geometry()
                if a.intersects(b):
                    problems.append(
                        f"overlap {_describe(children[i])}{a} / "
                        f"{_describe(children[j])}{b} (in {_describe(parent)})"
                    )
    return problems


def find_layout_problems(root: QWidget) -> list[str]:
    """Return every painted-geometry defect found under ``root``."""
    return _clipped_labels(root) + _geometry_problems(root)
