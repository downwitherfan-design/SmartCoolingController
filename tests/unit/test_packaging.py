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


def test_windows_build_caches_are_excluded_at_root() -> None:
    # The one-click build keeps ~150 MB of wheels in these two directories;
    # they must never bloat the source archive.
    assert deliver._excluded(Path(".pip-cache/http-v2/x.body"))
    assert deliver._excluded(Path("wheelhouse/PySide6-6.11.2-cp39-abi3-win_amd64.whl"))
    # Only at the root: a nested directory with the same name is real source.
    assert not deliver._excluded(Path("scripts/wheelhouse/keep.py"))


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


# ---------------------------------------------------------------------------
# Packaging must survive a machine that has no git and no Inno Setup.
#
# A source archive is unpacked on a Windows box that has neither.  The release
# pipeline used to crash twice there: ``release_notes`` called ``git`` through
# ``subprocess`` and died with FileNotFoundError, and the missing Inno Setup
# compiler aborted the whole packaging step.  Neither may take the build down.
# ---------------------------------------------------------------------------


def _import_release_notes():
    sys.path.insert(0, str(ROOT / "scripts" / "package"))
    import release_notes

    return release_notes


def test_release_notes_survive_a_missing_git(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as _sp

    release_notes = _import_release_notes()

    def _missing_git(*_args, **_kwargs):
        # Exactly what Windows raises when "git" is not on PATH.
        raise FileNotFoundError(2, "The system cannot find the file specified", "git")

    monkeypatch.setattr(_sp, "run", _missing_git)

    assert release_notes.last_tag() is None
    assert release_notes._git_log(None) == []
    markdown = release_notes.render([], version="1.0.0", protocol=1, since=None)
    assert "No commits found" in markdown


def test_release_notes_cli_writes_a_file_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess as _sp

    release_notes = _import_release_notes()

    def _missing_git(*_args, **_kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified", "git")

    monkeypatch.setattr(_sp, "run", _missing_git)
    output = tmp_path / "RELEASE_NOTES.md"

    assert release_notes.main(["--output", str(output)]) == 0
    assert output.is_file()
    assert "SmartCooling Controller" in output.read_text(encoding="utf-8")


def test_launcher_builds_once_through_the_release_pipeline() -> None:
    # build_windows.bat used to run build.py and then release.py, which builds
    # again — the freeze ran twice.  It now calls the release pipeline once and
    # passes --skip-installer so a machine without Inno Setup still finishes.
    text = _LAUNCHER.read_text(encoding="utf-8")
    assert "scripts\\package\\release.py --release --skip-installer" in text
    assert "scripts\\build\\build.py" not in text
    assert "installer_missing" not in text


def test_release_pipeline_accepts_skip_installer() -> None:
    text = (ROOT / "scripts" / "package" / "release.py").read_text(encoding="utf-8")
    assert "--skip-installer" in text
    # A missing Inno Setup must be caught, not propagated.
    assert "except SystemExit" in text
    # Release notes are optional: a failure there must not kill the build.
    assert "continuing without them" in text


def test_installer_search_covers_a_per_user_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # find_iscc() probes several absolute Windows paths; a real machine (CI!)
    # may already have one of them.  Redirect every probe into an empty tree so
    # the test observes only the search order, not the host.
    sys.path.insert(0, str(ROOT / "scripts"))
    import common

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(empty / "AppData" / "Local"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(empty / "Program Files (x86)"))
    monkeypatch.setattr(common, "_INNO_CANDIDATES", (str(empty / "pf" / "ISCC.exe"),))
    monkeypatch.setattr(common.shutil, "which", lambda *_a, **_k: None)
    # No ISCC anywhere on this machine: the search must return None, not raise.
    assert common.find_iscc() is None


def test_installer_search_finds_a_per_user_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The per-user install (``%LOCALAPPDATA%\Programs\Inno Setup 6``) is the one
    # a standard, non-elevated user gets, so the search must reach it.
    sys.path.insert(0, str(ROOT / "scripts"))
    import common

    local = tmp_path / "Local"
    iscc = local / "Programs" / "Inno Setup 6" / "ISCC.exe"
    iscc.parent.mkdir(parents=True)
    iscc.write_bytes(b"stub")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(common, "_INNO_CANDIDATES", ())
    monkeypatch.setattr(common.shutil, "which", lambda *_a, **_k: None)
    assert common.find_iscc() == iscc


def test_launcher_detects_a_stale_checkout() -> None:
    # The recurring failure was running an old copy of the scripts.  The
    # launcher must probe the scripts it is about to run and refuse loudly
    # instead of surfacing a "FileNotFoundError: git" traceback.
    text = _LAUNCHER.read_text(encoding="utf-8")
    assert "findstr" in text
    assert "goto stale_tree" in text
    assert ":stale_tree" in text


def test_release_notes_never_calls_subprocess_directly() -> None:
    # git is reached through common.git, the single guarded entry point; a
    # stray subprocess.run(["git", ...]) is exactly what crashed Windows.
    text = (ROOT / "scripts" / "package" / "release_notes.py").read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "from common import" in text and "git" in text


# ---------------------------------------------------------------------------
# The Windows installer workflow.
#
# CI is how a real `setup.exe` and a download link get produced.  It must build
# on a genuine Windows runner, compile the installer with Inno Setup, verify the
# frozen executable and publish the artefacts on a tag.  These assertions keep
# the workflow honest if it is ever edited.
# ---------------------------------------------------------------------------

_WORKFLOW = ROOT / ".github" / "workflows" / "windows-installer.yml"


def test_windows_installer_workflow_exists_and_is_valid_yaml() -> None:
    import yaml

    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert data["jobs"]["windows"]["runs-on"] == "windows-latest"


def test_windows_installer_workflow_builds_the_installer() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/installer/build_installer.py" in text
    assert "innosetup" in text  # Inno Setup must actually be installed
    assert "scripts/package/deliver.py" in text


def test_windows_installer_workflow_publishes_on_tags() -> None:
    import yaml

    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare YAML key ``on`` as the boolean True.
    triggers = data.get("on", data.get(True))
    assert triggers is not None
    assert "v*" in triggers["push"]["tags"]
    assert data["permissions"]["contents"] == "write"


def test_windows_installer_workflow_verifies_the_frozen_binary() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "--headless" in text
    assert "--version" in text
