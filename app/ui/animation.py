"""Animation system (spec §25, §26, §89, §130).

Motion is a *preference*, not a decoration.  Everything here reads the theme's
:class:`~app.core.enums.AnimationLevel`:

===========  ==========================================================
Level        Behaviour
===========  ==========================================================
``OFF``      No transitions, no fades, no pulses.  Also honoured when the OS
             reports reduced motion.
``LOW``      Short fades only (half duration).
``NORMAL``   Fades + subtle slide transitions + staggered card reveals.
``HIGH``     Longer, more expressive motion and glow pulses.
===========  ==========================================================

Two implementation notes:

* Transitions use :class:`QGraphicsOpacityEffect` on the outgoing/incoming page
  and are cancelled cleanly if a new navigation starts mid-flight, so rapid
  clicking never leaves a half-faded page behind.
* Stagger reveals are driven by a single ``QTimer`` chain, not one timer per
  widget, so a page with 20 cards costs one timer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from app.core.enums import AnimationLevel
from app.ui.design import Theme


class AnimationManager:
    """Creates theme-aware animations and owns their lifetime."""

    def __init__(self, theme: Theme) -> None:
        self.theme = theme
        self._running: list[QPropertyAnimation | QVariantAnimation] = []

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme

    @property
    def enabled(self) -> bool:
        return self.theme.motion

    def _track(self, animation: QPropertyAnimation | QVariantAnimation):
        self._running.append(animation)
        animation.finished.connect(lambda: self._forget(animation))
        return animation

    def _forget(self, animation) -> None:
        if animation in self._running:
            self._running.remove(animation)

    def stop_all(self) -> None:
        for animation in list(self._running):
            animation.stop()
        self._running.clear()

    # -- primitives --------------------------------------------------------

    def fade(
        self,
        widget: QWidget,
        *,
        start: float = 0.0,
        end: float = 1.0,
        duration_ms: int = 220,
        on_finished: Callable[[], None] | None = None,
    ) -> QPropertyAnimation | None:
        duration = self.theme.duration(duration_ms)
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setOpacity(start)
        if duration <= 0:
            effect.setOpacity(end)
            if on_finished:
                on_finished()
            return None
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on_finished:
            animation.finished.connect(on_finished)
        self._track(animation)
        animation.start()
        return animation

    def slide(
        self,
        widget: QWidget,
        *,
        offset: QPoint,
        duration_ms: int = 260,
    ) -> QPropertyAnimation | None:
        duration = self.theme.duration(duration_ms)
        if duration <= 0:
            return None
        start = widget.pos()
        animation = QPropertyAnimation(widget, b"pos", widget)
        animation.setDuration(duration)
        animation.setStartValue(start + offset)
        animation.setEndValue(start)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._track(animation)
        animation.start()
        return animation

    def pulse(self, widget: QWidget, *, token: str = "danger", cycles: int = 3) -> None:
        """A gentle attention pulse used for critical alerts."""
        if not self.enabled:
            return
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(self.theme.duration(900))
        animation.setKeyValueAt(0.0, 1.0)
        animation.setKeyValueAt(0.5, 0.45)
        animation.setKeyValueAt(1.0, 1.0)
        animation.setLoopCount(cycles)
        animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._track(animation)
        animation.start()

    def count_up(
        self,
        *,
        start: float,
        end: float,
        duration_ms: int = 600,
        on_value: Callable[[float], None],
        on_finished: Callable[[], None] | None = None,
    ) -> QVariantAnimation | None:
        duration = self.theme.duration(duration_ms)
        if duration <= 0:
            on_value(end)
            if on_finished:
                on_finished()
            return None
        animation = QVariantAnimation()
        animation.setDuration(duration)
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: on_value(float(value)))
        if on_finished:
            animation.finished.connect(on_finished)
        self._track(animation)
        animation.start()
        return animation


class StaggerReveal:
    """Reveals a list of widgets one after another with a short delay.

    A single timer walks the list, so cost is O(1) regardless of widget count.
    """

    def __init__(self, animations: AnimationManager) -> None:
        self._animations = animations
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reveal_next)
        self._queue: list[QWidget] = []
        self._delay_ms = 35

    def start(self, widgets: Iterable[QWidget], *, delay_ms: int | None = None) -> None:
        self._timer.stop()
        self._queue = [w for w in widgets if w is not None]
        if delay_ms is not None:
            self._delay_ms = delay_ms
        if not self._animations.enabled:
            for widget in self._queue:
                widget.setVisible(True)
                effect = widget.graphicsEffect()
                if isinstance(effect, QGraphicsOpacityEffect):
                    effect.setOpacity(1.0)
            self._queue.clear()
            return
        for widget in self._queue:
            effect = widget.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(widget)
                widget.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            widget.setVisible(True)
        self._reveal_next()

    def _reveal_next(self) -> None:
        if not self._queue:
            return
        widget = self._queue.pop(0)
        self._animations.fade(widget, start=0.0, end=1.0, duration_ms=260)
        if self._queue:
            delay = max(10, int(self._delay_ms * (0.5 if self._animations.theme.animation is AnimationLevel.LOW else 1.0)))
            self._timer.start(delay)


__all__ = ["AnimationManager", "StaggerReveal"]
