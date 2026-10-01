"""Shared pytest fixtures for the SmartCooling Controller test-suite.

The suite is split into three layers that mirror the application itself:

* ``tests/unit`` — pure logic (curves, analytics, protocol, config, paths).
* ``tests/integration`` — services wired to a real SQLite database and the
  simulated hardware back-end, driven through the real event bus.
* ``tests/ui`` — real Qt widgets rendered off-screen.

Everything runs against the *real* code paths.  No service is mocked; where a
dependency is expensive or platform-specific (hardware, the network) we use the
application's own simulated back-end and in-process mock ESP32, which are
production components rather than test doubles.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Qt must run head-less in CI and in this sandbox.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

# Make ``app`` importable when pytest is invoked from a different directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def qapp():
    """A single ``QApplication`` for the whole session."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """An isolated, writable data directory for one test."""
    root = tmp_path / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def config(tmp_path: Path):
    """A default :class:`AppConfig` bound to an isolated config directory."""
    from app.core.config import AppConfig, ConfigStore

    store = ConfigStore(tmp_path / "Config")
    store._config = AppConfig()
    return store.config


@pytest.fixture
def services(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """Fully wired services on the simulated back-end.

    The fixture never touches real hardware and always shuts the services down,
    so a failing test cannot leave a writer thread or an open database behind.
    """
    from app.services import ApplicationServices

    monkeypatch.setenv("SMARTCOOLING_DATA_MODE", "standard")
    monkeypatch.setenv("SMARTCOOLING_DATA_DIR", str(data_root))
    instance = ApplicationServices()
    # Keep tests deterministic and quiet.
    instance.config.general.start_telemetry = False
    instance.config.general.auto_connect_esp32 = False
    instance.config.general.first_run_completed = True
    instance.config.general.close_to_tray = False
    instance.config.general.confirm_exit = False
    instance.enable_simulation()
    instance.start(simulate=True)
    try:
        yield instance
    finally:
        instance.shutdown()


@pytest.fixture
def theme():
    """The default dark theme."""
    from app.core.enums import ThemeMode
    from app.ui.design import build_theme

    return build_theme(ThemeMode.DARK)


@pytest.fixture
def translator():
    """An English translator."""
    from app.core.enums import Language
    from app.ui.i18n import Translator

    return Translator(Language.ENGLISH)
