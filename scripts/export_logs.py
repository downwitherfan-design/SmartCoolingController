#!/usr/bin/env python3
"""Bundle application logs for support, with secrets scrubbed (spec §122, §152).

Reads the rotating text log and the structured diagnostics table, redacts
anything that looks like a credential or an absolute user path, and writes a
single text file plus a small environment header.  The result is safe to attach
to a support ticket.

Usage::

    python scripts/export_logs.py
    python scripts/export_logs.py --data-dir D:\\SmartCoolingData --lines 5000
    python scripts/export_logs.py --output C:\\temp\\smartcooling-logs.txt
"""

from __future__ import annotations

import argparse
import platform
import re
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import to_iso, utc_now
from app.core.enums import DataMode
from app.core.logging_setup import redact_text
from app.core.paths import AppPaths

#: Collapse the user's profile directory so a log never leaks a real name.
_PATH_RE = re.compile(r"([A-Za-z]:\\Users\\[^\\\s]+|/home/[^/\s]+)")


def scrub(text: str) -> str:
    """Redact credentials and absolute home paths from one line."""
    return _PATH_RE.sub("<user>", redact_text(text))


def tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=lines)]


def structured_diagnostics(data_dir: Path, limit: int) -> list[str]:
    database = data_dir / "Database" / "SmartCooling.db"
    if not database.exists():
        return []
    from app.persistence.database import Database
    from app.persistence.repositories import DiagnosticRepository

    handle = Database(database)
    try:
        rows = DiagnosticRepository(handle).recent(limit)
    finally:
        handle.close()
    rendered = []
    for row in reversed(rows):  # oldest first reads better in a ticket
        stamp = row.get("ts", "?")
        rendered.append(
            f"{stamp} {row.get('level', '?')!s:<8} {row.get('component', '?')} "
            f"{row.get('message', '')}"
        )
    return [scrub(line) for line in rendered]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export scrubbed logs for support.")
    parser.add_argument("--data-dir", metavar="PATH", help="override the data directory")
    parser.add_argument("--lines", type=int, default=2000, help="text log lines to include")
    parser.add_argument("--diagnostics", type=int, default=500, help="structured rows to include")
    parser.add_argument("--mode", choices=[m.value for m in DataMode], default=None)
    parser.add_argument("--output", type=Path, default=None, help="output file")
    args = parser.parse_args(argv)

    if args.data_dir:
        import os

        os.environ["SMARTCOOLING_DATA_DIR"] = str(Path(args.data_dir).expanduser())

    paths = AppPaths.resolve(mode=DataMode(args.mode) if args.mode else None)
    paths.ensure()

    header = [
        "SmartCooling Controller — support log bundle",
        f"generated_at: {to_iso(utc_now())}",
        f"python: {platform.python_version()}",
        f"platform: {platform.system()} {platform.release()} ({platform.machine()})",
        f"data_dir: {_PATH_RE.sub('<user>', str(paths.root))}",
        "=" * 72,
        "",
    ]

    body: list[str] = ["--- application log ---", ""]
    body += [scrub(line) for line in tail(paths.logs / "smartcooling.log", args.lines)]
    body += ["", "--- structured diagnostics ---", ""]
    body += structured_diagnostics(paths.root, args.diagnostics)

    text = "\n".join(header + body) + "\n"
    output = args.output or (paths.exports / f"logs-{utc_now().strftime('%Y%m%d-%H%M%S')}.txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")

    print(f"wrote {len(text.splitlines())} lines to:")
    print(f"  {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
