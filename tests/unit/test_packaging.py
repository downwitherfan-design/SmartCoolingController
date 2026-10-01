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


# ---------------------------------------------------------------------------
# build_windows.bat — the one-click Windows launcher.
#
# cmd.exe parses an entire parenthesised block up front.  A single unescaped,
# unbalanced "(" anywhere inside such a block aborts the whole script with
# "Mismatch in parentheses." — the file opens and closes instantly, which is
# exactly the failure users reported.  The checks below lock down the two
# properties that keep that from happening: CRLF endings and clean parens.
# ---------------------------------------------------------------------------

_LAUNCHER = ROOT / "build_windows.bat"


def _unescaped(text: str) -> str:
    """Strip cmd caret escapes so only *active* parentheses remain."""
    return text.replace("^(", "").replace("^)", "")


def test_launcher_is_shipped_with_crlf() -> None:
    payload = _LAUNCHER.read_bytes()
    assert b"\r\n" in payload
    assert payload.count(b"\n") == payload.count(b"\r\n")


def test_launcher_has_no_unbalanced_line() -> None:
    text = _LAUNCHER.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        active = _unescaped(line)
        # A line may legitimately *open* a block (trailing "(") or *close* one
        # (a lone ")"); every other line must carry balanced parentheses.
        stripped = active.rstrip()
        if stripped.endswith("(") and stripped.count("(") == 1 and stripped.count(")") == 0:
            continue
        if stripped == ")":
            continue
        assert active.count("(") == active.count(")"), (
            f"build_windows.bat:{number} has unbalanced parentheses: {line!r}"
        )


def test_launcher_parens_are_globally_balanced() -> None:
    active = _unescaped(_LAUNCHER.read_text(encoding="utf-8"))
    assert active.count("(") == active.count(")")


def test_launcher_has_no_fragile_nested_blocks() -> None:
    # The launcher deliberately uses flat, top-level statements plus "goto"
    # instead of nested "if (...)" / "for ... do (...)" blocks, so no line can
    # ever be swallowed by cmd.exe's block parser.
    for number, line in enumerate(_LAUNCHER.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip().lower()
        assert not stripped.startswith("for "), f"nested for-block at line {number}"
        assert not stripped.endswith("("), f"unexpected block opener at line {number}"
