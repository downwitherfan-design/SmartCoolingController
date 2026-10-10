"""The shell must turn a notification request into a visible tray balloon.

Regression: ``alerts.notify`` was published but nothing subscribed, so the
"Windows notifications" setting was a silent no-op.  The shell now consumes it
and shows one ``QSystemTrayIcon`` balloon per alert.
"""

from __future__ import annotations

from PySide6.QtWidgets import QSystemTrayIcon

from app.core.enums import AlertCategory, AlertEventType, AlertSeverity
from app.models.alerts import AlertEvent
from app.ui.shell import MainWindow


class _TrayStub:
    def __init__(self, visible: bool = True) -> None:
        self.visible = visible
        self.calls: list[tuple] = []

    def isVisible(self) -> bool:
        return self.visible

    def showMessage(self, *args) -> None:
        self.calls.append(args)


class _SelfStub:
    def __init__(self, tray: _TrayStub) -> None:
        self.tray = tray


def _alert(severity: AlertSeverity) -> AlertEvent:
    return AlertEvent(
        category=AlertCategory.THERMAL,
        severity=severity,
        title="CPU temperature is high",
        message="Consider raising the fan profile.",
        event_type=AlertEventType.THERMAL_WARNING,
        dedup_key="cpu.warning",
    )


def test_a_warning_shows_a_tray_balloon() -> None:
    tray = _TrayStub()
    MainWindow._show_notification(_SelfStub(tray), _alert(AlertSeverity.WARNING))
    assert len(tray.calls) == 1
    title, body, icon, _timeout = tray.calls[0]
    assert title == "CPU temperature is high"
    assert body == "Consider raising the fan profile."
    assert icon == QSystemTrayIcon.MessageIcon.Warning


def test_a_critical_alert_uses_the_critical_icon() -> None:
    tray = _TrayStub()
    MainWindow._show_notification(_SelfStub(tray), _alert(AlertSeverity.CRITICAL))
    assert tray.calls[0][2] == QSystemTrayIcon.MessageIcon.Critical


def test_no_balloon_when_the_tray_is_hidden() -> None:
    tray = _TrayStub(visible=False)
    MainWindow._show_notification(_SelfStub(tray), _alert(AlertSeverity.WARNING))
    assert tray.calls == []


def test_a_message_falls_back_to_the_title_when_empty() -> None:
    tray = _TrayStub()
    alert = _alert(AlertSeverity.WARNING)
    alert.message = ""
    MainWindow._show_notification(_SelfStub(tray), alert)
    assert tray.calls[0][1] == "CPU temperature is high"
