"""Page framework (spec §24, §27, §107).

Every page inherits :class:`Page`.  The contract is deliberately small so a
page can be written in one file:

* ``key``/``title_key``/``subtitle_key``/``icon`` describe the page to the shell.
* :meth:`build` creates the widget tree once.
* :meth:`refresh` is called on telemetry snapshots — **throttled by the shell**,
  never per-sample, so a slow page cannot stall the pipeline.
* :meth:`subscribe` registers an event-bus handler that is automatically
  disconnected when the page is destroyed, so no page leaks a listener.

Pages never touch the network or the database directly; they go through the
service container.  That keeps them testable with a fake container.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from app.models.telemetry import TelemetrySnapshot
from app.ui.animation import AnimationManager
from app.ui.design import SPACE, Theme
from app.ui.i18n import Translator
from app.ui.widgets.base import ThemedWidget

if TYPE_CHECKING:  # pragma: no cover
    from app.services import ApplicationServices


class Page(ThemedWidget):
    """Base class for all pages."""

    #: Emitted when a page wants the shell to navigate somewhere.
    navigate = Signal(str)
    #: Emitted when a page wants a transient message in the status area.
    message = Signal(str, str)

    key: str = "page"
    title_key: str = ""
    subtitle_key: str = ""
    icon_name: str = "dashboard"
    #: Whether the page needs periodic telemetry refreshes.
    wants_snapshots: bool = True

    def __init__(
        self,
        services: ApplicationServices,
        theme: Theme,
        translator: Translator,
        animations: AnimationManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, translator, parent)
        self.services = services
        self.animations = animations
        self._subscriptions: list[Callable[[], None]] = []
        self._built = False
        self._dirty = True
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._flush_refresh)
        self._pending_snapshot: TelemetrySnapshot | None = None
        self._content = QWidget(self)
        self._content.setObjectName("Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._outer = outer

    # -- lifecycle ---------------------------------------------------------

    def build(self) -> QWidget:
        """Create the widget tree and return the root to embed."""
        if not self._built:
            self._build_content(self._content)
            self._built = True
        return self._content

    def _build_content(self, content: QWidget) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def content(self) -> QWidget:
        return self._content

    # -- data --------------------------------------------------------------

    def subscribe(self, topic: Any, handler: Callable[..., None]) -> None:
        """Subscribe to the bus; the handler is removed with the page."""
        unsubscribe = self.services.bus.subscribe(topic, handler)
        self._subscriptions.append(unsubscribe)

    def on_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        """Called by the shell; coalesces bursts into one refresh."""
        if not self.wants_snapshots:
            return
        self._pending_snapshot = snapshot
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _flush_refresh(self) -> None:
        if self._pending_snapshot is not None:
            self.refresh(self._pending_snapshot)
            self._pending_snapshot = None

    def refresh(self, snapshot: TelemetrySnapshot) -> None:  # pragma: no cover - overridden
        """Update the page from a snapshot. Throttled by the shell."""

    def on_shown(self) -> None:
        """Called when the page becomes visible; force one refresh."""
        if self._pending_snapshot is not None:
            self._flush_refresh()

    # -- theming / i18n ----------------------------------------------------

    def retranslate(self) -> None:  # pragma: no cover - overridden
        """Update all display strings after a language change."""

    def set_theme(self, theme: Theme) -> None:
        super().set_theme(theme)
        self.animations.set_theme(theme)

    def teardown(self) -> None:
        for unsubscribe in self._subscriptions:
            with contextlib.suppress(Exception):
                unsubscribe()
        self._subscriptions.clear()

    def deleteLater(self) -> None:
        self.teardown()
        super().deleteLater()


class ScrollPage(Page):
    """A page whose content scrolls vertically with generous margins."""

    def _build_content(self, content: QWidget) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Root")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(SPACE["xl"], SPACE["lg"], SPACE["xl"], SPACE["xl"])
        self.body.setSpacing(SPACE["lg"])
        scroll.setWidget(inner)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.build_body()

    def build_body(self) -> None:  # pragma: no cover - overridden
        """Populate ``self.body``."""


__all__ = ["Page", "ScrollPage"]
