#!/usr/bin/env python3
"""Generate release notes from git history (spec §264).

Groups commits since the previous tag into the categories a release note needs:
breaking changes, features, fixes, performance, and everything else.  The
output is Markdown, ready to paste into a GitHub release.

Usage::

    python scripts/package/release_notes.py                 # since the last tag
    python scripts/package/release_notes.py --since v0.9.0
    python scripts/package/release_notes.py --output docs/RELEASE_NOTES.md
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ROOT, app_metadata

#: Conventional-commit type → section heading.  Order defines the output order.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("breaking", "Breaking changes"),
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build & packaging"),
    ("ci", "CI"),
    ("chore", "Maintenance"),
)

_COMMIT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"


@dataclass(slots=True)
class Commit:
    sha: str
    subject: str
    body: str = ""

    @property
    def category(self) -> str:
        match = _COMMIT_RE.match(self.subject)
        if not match:
            return "chore"
        if match.group("breaking") or "BREAKING CHANGE" in self.body:
            return "breaking"
        kind = match.group("type")
        known = {name for name, _ in SECTIONS}
        return kind if kind in known else "chore"

    @property
    def summary(self) -> str:
        match = _COMMIT_RE.match(self.subject)
        if not match:
            return self.subject
        scope = match.group("scope")
        text = match.group("subject")
        return f"**{scope}**: {text}" if scope else text


def _git_log(since: str | None) -> list[Commit]:
    fmt = f"%h{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"
    command = ["git", "log", f"--pretty=format:{fmt}"]
    if since:
        command.append(f"{since}..HEAD")
    import subprocess

    result = subprocess.run(
        command, cwd=str(ROOT), capture_output=True, text=True, check=False, timeout=30
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    commits: list[Commit] = []
    for record in result.stdout.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(_FIELD_SEP, 2)
        if len(parts) < 2:
            continue
        commits.append(
            Commit(sha=parts[0], subject=parts[1], body=parts[2] if len(parts) > 2 else "")
        )
    return commits


def last_tag() -> str | None:
    import subprocess

    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def render(commits: list[Commit], *, version: str, protocol: int, since: str | None) -> str:
    buckets: dict[str, list[Commit]] = {name: [] for name, _ in SECTIONS}
    for commit in commits:
        buckets.setdefault(commit.category, []).append(commit)

    lines = [
        f"# SmartCooling Controller {version}",
        "",
        f"Protocol version: **v{protocol}**",
    ]
    if since:
        lines.append(f"Changes since `{since}`.")
    lines.append("")

    if not commits:
        lines += ["_No commits found for this range._", ""]
        return "\n".join(lines)

    for name, heading in SECTIONS:
        items = buckets.get(name) or []
        if not items:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for commit in items:
            lines.append(f"- {commit.summary} (`{commit.sha}`)")
        lines.append("")

    lines += ["## Full changelog", ""]
    if since:
        lines.append(f"https://github.com/OWNER/REPO/compare/{since}...HEAD")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate release notes from git history.")
    parser.add_argument("--since", default=None, help="git ref to start from (default: last tag)")
    parser.add_argument("--output", type=Path, default=None, help="write to a file")
    args = parser.parse_args(argv)

    version, protocol = app_metadata()
    since = args.since or last_tag()
    commits = _git_log(since)
    markdown = render(commits, version=version, protocol=protocol, since=since)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        print(f"wrote {len(commits)} commits to {args.output}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
