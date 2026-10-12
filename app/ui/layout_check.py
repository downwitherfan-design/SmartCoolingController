"""Painted-geometry layout checks.

Layout tests that only call ``layout().activate()`` and inspect ``sizeHint``
miss the defects users actually see: a stylesheet that overrides
``setFixedSize`` and pushes a button over the text below it, a tile that clips
a compound value, two cards drawn on top of each other.

:func:`find_layout_problems` inspects the *real* geometry Qt computed for a
built widget tree and reports those defects as human-readable strings.  It is
used by the head-less GUI audit (``scripts/diagnostics/gui_audit.py``) and by
the UI test-suite, so both share one definition of "correctly laid out".

:func:`find_painted_text_problems` covers what QLabel geometry cannot see:
gauges, the schematic and the fan paint their own text, so it renders a widget
through a recording painter and reports every run that overflows its box.  The
audit runs both.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QWidget

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


#: Shared sink the recording painter appends to during one audit render.  A
#: widget's ``paintEvent`` builds its *own* ``QPainter(self)``, so the findings
#: cannot live on a single instance — every recording painter created while an
#: audit is active must report into the same list.
_ACTIVE_FINDINGS: list[str] | None = None


class _TextRecordingPainter(QPainter):
    """A QPainter that records ``drawText`` runs that overflow their rectangle.

    QLabel checks cannot see text drawn inside a ``paintEvent`` — gauges, the
    schematic and the fan all paint their own labels — which is exactly where the
    reported clipping lived.  Rendering a widget while this class is installed
    captures every run with the *real* font metrics and box, so overflow becomes
    measurable and can gate CI.
    """

    def drawText(self, *args) -> None:  # type: ignore[override]
        sink = _ACTIVE_FINDINGS
        try:
            if sink is not None and args and isinstance(args[0], QRectF):
                rect = args[0]
                flags = int(args[1]) if len(args) > 1 else 0
                text = str(args[2]) if len(args) > 2 else ""
                metrics = self.fontMetrics()
                wrapped = bool(flags & int(Qt.TextFlag.TextWordWrap))
                if wrapped:
                    needed = metrics.boundingRect(
                        0, 0, int(rect.width()), 100000, flags, text
                    ).height()
                    if needed > rect.height() + _SLACK:
                        sink.append(
                            f"painted-text overflow {text[:32]!r}: wrapped needs "
                            f"{needed}px, box {int(rect.height())}px"
                        )
                else:
                    ink = metrics.tightBoundingRect(text)
                    if ink.width() > rect.width() + _SLACK or ink.height() > rect.height() + _SLACK:
                        sink.append(
                            f"painted-text overflow {text[:32]!r}: ink "
                            f"{ink.width()}x{ink.height()}px, box "
                            f"{int(rect.width())}x{int(rect.height())}px"
                        )
        except (TypeError, ValueError):  # never let the audit crash the render
            pass
        super().drawText(*args)


#: Modules whose custom painters are patched during an audit.  Imported lazily so
#: this module stays importable without a QApplication.
_PAINT_MODULES = (
    "app.ui.widgets.gauges",
    "app.ui.widgets.fan",
    "app.ui.widgets.schematic",
    "app.ui.widgets.curve",
    "app.ui.widgets.atoms",
    "app.ui.widgets.forms",
    "app.ui.widgets.base",
)


def _iter_widget_modules() -> list[ModuleType]:
    import app.ui.widgets as package

    modules = [importlib.import_module(name) for name in _PAINT_MODULES]
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        if module not in modules:
            modules.append(module)
    return modules


def find_painted_text_problems(widget: QWidget) -> list[str]:
    """Render ``widget`` and report custom-painted text that escapes its box."""
    global _ACTIVE_FINDINGS
    if QApplication.instance() is None:
        return []
    width = max(1, widget.width())
    height = max(1, widget.height())
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    findings: list[str] = []
    patches: list[tuple[ModuleType, object]] = []
    for module in _iter_widget_modules():
        if not hasattr(module, "QPainter"):
            continue
        patches.append((module, module.QPainter))  # type: ignore[attr-defined]
        module.QPainter = _TextRecordingPainter  # type: ignore[attr-defined]
    previous = _ACTIVE_FINDINGS
    _ACTIVE_FINDINGS = findings
    try:
        painter = _TextRecordingPainter(image)
        widget.render(painter, QPoint(0, 0))
        painter.end()
    finally:
        _ACTIVE_FINDINGS = previous
        for module, original in patches:
            module.QPainter = original  # type: ignore[attr-defined]
    return findings
