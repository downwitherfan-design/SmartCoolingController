"""Regression tests for the delivery packager.

These cover the two packaging defects that shipped once and must never ship
again:

* a Windows ``.bat`` archived with bare-LF line endings — ``cmd.exe`` closes
  such a file the instant it is launched, so the build script "opened and
  immediately closed" for every user; and
* the source archive dropping ``scripts/build/`` because the exclusion filter
  matched the word "build" at any depth instead of only the output directory
  at the archive root.

The tests exercise the real archive helpers against a real temporary tree.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "package"))

import deliver  # noqa: E402  (path set up above)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_bat_is_archived_with_crlf(tmp_path: Path) -> None:
    tree = tmp_path / "src"
    _write(tree / "build_windows.bat", b"@echo off\npause\n")
    archive = tmp_path / "out.zip"

    deliver._zip_tree(tree, archive)

    with zipfile.ZipFile(archive) as zf:
        payload = zf.read("build_windows.bat")
    assert b"\r\n" in payload
    assert payload.count(b"\n") == payload.count(b"\r\n")


def test_cmd_is_archived_with_crlf(tmp_path: Path) -> None:
    tree = tmp_path / "src"
    _write(tree / "tool.cmd", b"echo hi\n")
    archive = tmp_path / "out.zip"

    deliver._zip_tree(tree, archive)

    with zipfile.ZipFile(archive) as zf:
        assert zf.read("tool.cmd") == b"echo hi\r\n"


def test_crlf_bat_is_not_double_converted(tmp_path: Path) -> None:
    tree = tmp_path / "src"
    _write(tree / "run.bat", b"@echo off\r\npause\r\n")
    archive = tmp_path / "out.zip"

    deliver._zip_tree(tree, archive)

    with zipfile.ZipFile(archive) as zf:
        assert zf.read("run.bat") == b"@echo off\r\npause\r\n"


def test_non_batch_files_are_untouched(tmp_path: Path) -> None:
    tree = tmp_path / "src"
    _write(tree / "script.sh", b"echo hi\n")
    _write(tree / "module.py", b"print('x')\n")
    archive = tmp_path / "out.zip"

    deliver._zip_tree(tree, archive)

    with zipfile.ZipFile(archive) as zf:
        assert zf.read("script.sh") == b"echo hi\n"
        assert zf.read("module.py") == b"print('x')\n"


def test_build_output_is_excluded_only_at_root() -> None:
    # A directory literally named "build" at the archive root is output and
    # must be dropped ...
    assert deliver._excluded(Path("build/dist/app.exe"))
    assert deliver._excluded(Path("build"))
    # ... but scripts/build/ is source and must survive.
    assert not deliver._excluded(Path("scripts/build/build.py"))
    assert not deliver._excluded(Path("scripts/build/verify_frozen.py"))


@pytest.mark.parametrize(
    "relative",
    [
        Path(".venv/pyvenv.cfg"),
        Path("__pycache__/x.pyc"),
        Path("app/__pycache__/y.pyc"),
        Path(".git/config"),
    ],
)
def test_generated_paths_are_excluded(relative: Path) -> None:
    assert deliver._excluded(relative)
