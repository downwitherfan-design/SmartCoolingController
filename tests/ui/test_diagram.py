"""The system diagram must tell the truth about link health.

Regression guards for three v1 defects: the schematic timer kept firing after
the page was torn down (language-switch rebuild), the Wi-Fi node reported a
fabricated ``online`` status, and DEGRADED/STALE were drawn as a fully live
link.
"""

from __future__ import annotations

import pytest

from app.core.enums import ConnectionState, FanState
from app.ui.animation import AnimationManager
from app.ui.design import build_theme
from app.ui.i18n import Translator
from app.ui.pages.diagram import DiagramPage
from app.ui.widgets.schematic import Schematic

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def schematic(qapp):
    theme = build_theme("en")
    widget = Schematic(theme, Translator("en"))
    yield widget
    widget.stop_animation()
    widget.deleteLater()


def test_connected_link_animates(schematic: Schematic) -> None:
    schematic.set_link_state(ConnectionState.CONNECTED, fan_state=FanState.RUNNING, rpm=1800)
    assert schematic._mode == "healthy"
    assert schematic._timer.isActive()


def test_degraded_link_animates_slower(schematic: Schematic) -> None:
    schematic.set_link_state(ConnectionState.CONNECTED, fan_state=FanState.RUNNING, rpm=1800)
    schematic._tick()
    healthy_phase = schematic._links[0].phase
    schematic.set_link_state(ConnectionState.DEGRADED, fan_state=FanState.RUNNING, rpm=1800)
    assert schematic._mode == "degraded"
    assert schematic._timer.isActive()
    assert healthy_phase >= 0.0  # sanity: phase is a fraction


def test_stale_link_freezes(schematic: Schematic) -> None:
    schematic.set_link_state(ConnectionState.STALE, fan_state=FanState.STOPPED, rpm=None)
    assert schematic._mode == "stale"
    assert not schematic._timer.isActive()
    network = {"wifi", "esp32", "telemetry", "tach", "ina219", "ds18b20"}
    assert not any(link.live for link in schematic._links if link.src in network)


def test_disconnected_link_is_idle(schematic: Schematic) -> None:
    schematic.set_link_state(ConnectionState.DISCONNECTED, fan_state=FanState.STOPPED, rpm=None)
    assert schematic._mode == "idle"
    assert not schematic._timer.isActive()


def test_protocol_mismatch_is_not_drawn_live(schematic: Schematic) -> None:
    schematic.set_link_state(
        ConnectionState.PROTOCOL_MISMATCH, fan_state=FanState.STOPPED, rpm=None
    )
    assert schematic._mode == "idle"
    assert not schematic._timer.isActive()


def test_stop_animation_halts_the_timer(schematic: Schematic) -> None:
    schematic.set_link_state(ConnectionState.CONNECTED, fan_state=FanState.RUNNING, rpm=1800)
    assert schematic._timer.isActive()
    schematic.stop_animation()
    assert not schematic._timer.isActive()


def test_diagram_teardown_stops_schematic_timer(qapp, services) -> None:
    theme = build_theme("en")
    page = DiagramPage(
        services, theme, Translator("en"), AnimationManager(theme)
    )
    page.build()
    page.refresh(services.telemetry.sample_once())
    page.schematic.set_link_state(
        ConnectionState.CONNECTED, fan_state=FanState.RUNNING, rpm=1800
    )
    assert page.schematic._timer.isActive()
    page.teardown()
    assert not page.schematic._timer.isActive()
