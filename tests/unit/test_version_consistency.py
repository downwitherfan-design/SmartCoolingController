"""The displayed version must match every place that declares one.

A release is only trustworthy if the number a user sees in ``--version``, the
Windows ``.exe`` properties and the installer is the same number recorded in the
package metadata.  v2.0.0 got this right by hand; nothing stopped a future edit
from bumping one file and forgetting another, so this test pins the invariant.

It reads the real files (no mocks) and fails with the offending path when a
declaration drifts.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: ``X.Y.Z`` with an optional pre-release/local suffix — PEP 440-ish and strict
#: enough to reject the "v2.0" / "2.0.0.0" shapes that break the installer.
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$")


def _app_version() -> str:
    text = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "APP_VERSION not found in app/__init__.py"
    return match.group(1)


APP_VERSION = _app_version()


def test_app_version_is_semver() -> None:
    assert _SEMVER.match(APP_VERSION), f"APP_VERSION {APP_VERSION!r} is not semver"


def test_pyproject_matches_app_version() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == APP_VERSION


def test_pyinstaller_spec_default_matches_app_version() -> None:
    text = (ROOT / "installer" / "smartcooling.spec").read_text(encoding="utf-8")
    match = re.search(r'SMARTCOOLING_APP_VERSION",\s*"([^"]+)"', text)
    assert match, "spec default version not found"
    assert match.group(1) == APP_VERSION


def test_inno_setup_default_matches_app_version() -> None:
    text = (ROOT / "installer" / "smartcooling.iss").read_text(encoding="utf-8")
    match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', text)
    assert match, "iss default version not found"
    assert match.group(1) == APP_VERSION


def test_generated_windows_version_resource_matches_app_version() -> None:
    """The VERSIONINFO resource the build writes must carry APP_VERSION.

    ``installer/version_info.txt`` is generated (and gitignored), so the test
    drives the real generator rather than reading a stale copy that is not even
    present on a fresh checkout.
    """
    import importlib

    sys.path.insert(0, str(ROOT / "scripts" / "build"))
    build = importlib.import_module("build")

    target = ROOT / "installer" / "version_info.txt"
    previous = target.read_bytes() if target.exists() else None
    try:
        generated = build._write_version_info(APP_VERSION, "testbuild")
        text = generated.read_text(encoding="utf-8")
        for field in ("FileVersion", "ProductVersion"):
            match = re.search(rf"StringStruct\('{field}',\s*'([^']+)'\)", text)
            assert match, f"{field} not generated"
            assert match.group(1) == APP_VERSION, f"{field} drifted from APP_VERSION"
    finally:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)


def test_readme_download_links_use_the_app_version() -> None:
    """Every ``releases/latest/download`` asset link names the same version.

    The README is hand-edited, so a version bump is the moment a stale filename
    sneaks in and the "direct link" column starts 404ing.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"releases/latest/download/([A-Za-z0-9._-]+)", text)
    assert links, "no download links found in README.md"
    versions = set()
    for name in links:
        match = re.search(r"SmartCoolingController-(\d+\.\d+\.\d+)", name)
        if match:
            versions.add(match.group(1))
    assert versions, "no versioned asset names found in README.md"
    assert versions == {APP_VERSION}, (
        f"README links reference {sorted(versions)} but APP_VERSION is {APP_VERSION}"
    )


def test_release_notes_checksum_example_uses_the_app_version() -> None:
    text = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    match = re.search(r"Get-FileHash\s+\.\\?([A-Za-z0-9._-]+\.exe)", text)
    if match is None:
        pytest.skip("release notes carry no checksum example")
    assert f"-{APP_VERSION}-" in match.group(1), (
        f"release-notes checksum example {match.group(1)!r} is not {APP_VERSION}"
    )
