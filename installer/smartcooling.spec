# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for SmartCooling Controller (spec §260).

Build with::

    pyinstaller installer/smartcooling.spec --noconfirm --clean

The spec is deliberately explicit about what is *excluded*: Qt ships dozens of
modules we never touch, and dropping them is the difference between a ~90 MB
and a ~180 MB installer.  Everything we exclude is verified by the frozen
smoke-test (``scripts/build/verify_frozen.py``) which launches the real binary.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

# --- metadata injected by scripts/build/build.py ---------------------------
# The build script exports these before invoking PyInstaller; the defaults keep
# a manual ``pyinstaller installer/smartcooling.spec`` run working.
BUILD_ID = os.environ.get("SMARTCOOLING_BUILD_ID", "dev")
APP_VERSION = os.environ.get("SMARTCOOLING_APP_VERSION", "1.0.0")

# --- hidden imports --------------------------------------------------------
# These packages resolve parts of themselves at runtime (plugins, backends or
# codecs) so PyInstaller's static analysis can miss them.
hiddenimports = [
    "psutil",
    "websockets",
    "websockets.legacy",
    "websockets.asyncio",
    "websockets.asyncio.server",
    "websockets.asyncio.client",
    "pydantic",
    "pydantic_core",
    "cryptography",
    "cryptography.hazmat.backends.openssl",
    "pyqtgraph",
    "app._build",
]
hiddenimports += collect_submodules("websockets")

# numpy 2.x splits its compiled core into ``numpy._core`` and its public
# submodules import each other lazily, so PyInstaller 6.11's bundled numpy hook
# ships ``_multiarray_umath`` but not the pure-Python modules beside it.  The
# frozen app then dies with "No module named 'numpy._core._exceptions'".
# Collecting the whole ``numpy._core`` subtree closes that gap; pyqtgraph
# imports numpy unconditionally, so this is on the start-up path.  Test modules
# are dropped to avoid shipping a redundant copy of the numpy test-suite.
hiddenimports += [
    mod
    for mod in collect_submodules("numpy._core")
    if not mod.startswith("numpy._core.tests")
]

# --- Qt modules we never use ----------------------------------------------
excluded_qt = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

excludes = [
    *excluded_qt,
    # pyqtgraph's 3D sub-package needs PyOpenGL, which we do not ship, so make
    # sure it is never collected.  (pyqtgraph's own hook still logs a harmless
    # "Failed to collect submodules for 'pyqtgraph.opengl'" warning before
    # excludes apply; it is not an error.)
    "pyqtgraph.opengl",
    # Heavy third-party stacks we genuinely do not use.
    # NOTE: numpy must NOT be listed here.  pyqtgraph imports numpy
    # unconditionally for its plotting primitives, so excluding it produces a
    # bundle that imports fine but crashes the first time a chart is drawn.
    # The frozen smoke-test in scripts/build/verify_frozen.py would catch that,
    # but the correct fix is simply to keep numpy in the image.
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "pytest",
    "PIL",
    "setuptools",
    "pip",
    "wheel",
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SmartCoolingController",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is blocked by many AV engines; skip it.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(SPEC_DIR / "version_info.txt") if sys.platform == "win32" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SmartCoolingController",
)
