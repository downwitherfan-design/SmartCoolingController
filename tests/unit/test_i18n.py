"""i18n catalogue integrity (English ↔ Persian).

The app ships two hand-maintained catalogues.  A key present in one and missing
from the other silently falls back to the key string, which surfaces as a raw
identifier in the UI.  These tests are cheap and catch that class of defect
before it reaches a release.
"""

from __future__ import annotations

from app.core.enums import ConnectionState, HealthStatus
from app.ui import i18n
from app.ui.i18n import Translator
from app.ui.widgets.schematic import NODE_DETAIL_GROUPS

_EN = set(i18n._EN)
_FA = set(i18n._FA)


def test_catalogues_have_identical_keys() -> None:
    assert _EN == _FA, (
        f"missing in FA: {sorted(_EN - _FA)}; extra in FA: {sorted(_FA - _EN)}"
    )


def test_every_connection_state_is_translated() -> None:
    # The UI renders ``conn.{state.value}`` for every state the manager can
    # report; a missing key shows the raw identifier to the user.
    for state in ConnectionState:
        assert f"conn.{state.value}" in _EN, state
        assert f"conn.{state.value}" in _FA, state


def test_every_health_status_is_translated() -> None:
    for status in HealthStatus:
        assert f"health.{status.value}" in _EN, status
        assert f"health.{status.value}" in _FA, status


def test_every_schematic_field_and_node_is_translated() -> None:
    for node, fields in NODE_DETAIL_GROUPS.items():
        assert f"field.{node}" in _EN, node
        assert f"field.{node}" in _FA, node
        for field in fields:
            key = f"field.{field.lower()}"
            assert key in _EN, key
            assert key in _FA, key


def test_persian_never_returns_a_raw_key_for_a_known_key() -> None:
    translator = Translator("fa")
    for key in _FA:
        assert translator.t(key) != key, key


def test_english_never_returns_a_raw_key_for_a_known_key() -> None:
    translator = Translator("en")
    for key in _EN:
        assert translator.t(key) != key, key


def test_unknown_key_falls_back_to_itself() -> None:
    assert Translator("en").t("does.not.exist") == "does.not.exist"


def test_persian_number_uses_local_digits() -> None:
    text = Translator("fa").number(1234, grouping=True)
    assert all(char not in "0123456789" for char in text)
    assert "۱" in text


def test_translator_accepts_string_language_literals() -> None:
    # Callers pass the plain strings "fa"/"en" all over the codebase; a
    # StrEnum identity check would silently drop RTL and local digits.
    persian = Translator("fa")
    assert persian.is_rtl is True
    assert persian.t("conn.connected") == "متصل"
    assert persian.number(1234, grouping=True) == "۱٬۲۳۴"
    assert Translator("en").is_rtl is False


