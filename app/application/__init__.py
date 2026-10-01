"""Application layer: the Qt/event-bus marshalling bridge.

Importing this package is cheap; :class:`QtEventBridge` is imported lazily by
the shell so that ``app.application`` never drags Qt into head-less tooling.
"""

from __future__ import annotations

__all__ = ["QtEventBridge"]


def __getattr__(name: str):
    if name == "QtEventBridge":
        from app.application.qt_bridge import QtEventBridge

        return QtEventBridge
    raise AttributeError(name)
