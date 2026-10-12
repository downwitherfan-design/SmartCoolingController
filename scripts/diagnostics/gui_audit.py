"""Head-less GUI correctness audit.

Renders every page at several window sizes in both languages and reports
objective layout defects:

* text clipped by a fixed-size label (needs more width/height than it has);
* widgets that overflow their parent's content rect (a symptom of a missing
  layout constraint rather than a per-widget tweak);
* sibling cards that overlap on the same page;
* degenerate geometry (zero or negative size for a visible widget).

Run off-screen:

    QT_QPA_PLATFORM=offscreen python scripts/diagnostics/gui_audit.py

It exits non-zero when any defect is found, so it doubles as a CI gate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.enums import Language  # noqa: E402
from app.ui.layout_check import find_layout_problems, find_painted_text_problems  # noqa: E402
from app.ui.shell import PAGE_CLASSES, MainWindow  # noqa: E402

#: The owner's screenshot was 1648x920; the rest span the common desktop range.
SIZES = [(1648, 920), (1280, 800), (1440, 900), (1600, 1000), (1024, 700)]
LANGUAGES = [Language.ENGLISH, Language.PERSIAN]


def audit_page(page) -> list[str]:
    return find_layout_problems(page.content) + find_painted_text_problems(page.content)


def _services(data_dir: Path):
    os.environ["SMARTCOOLING_DATA_MODE"] = "standard"
    os.environ["SMARTCOOLING_DATA_DIR"] = str(data_dir)
    from app.services import ApplicationServices

    services = ApplicationServices()
    services.config.general.start_telemetry = False
    services.config.general.auto_connect_esp32 = False
    services.config.general.first_run_completed = True
    services.config.general.close_to_tray = False
    services.config.general.confirm_exit = False
    services.enable_simulation()
    services.start(simulate=True)
    return services


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    import tempfile

    tmp = tempfile.TemporaryDirectory(prefix="scc_gui_audit_")
    services = _services(Path(tmp.name) / "data")
    failures = 0
    window = None
    try:
        window = MainWindow(services)
        # Populate every page with a real simulated snapshot so data-dependent
        # rows (fan readouts, key/value pairs, charts) are laid out for real.
        snapshot = services.telemetry.sample_once()
        for page in window._pages.values():
            page.on_snapshot(snapshot)
            page._flush_refresh()
        for width, height in SIZES:
            window.resize(width, height)
            window.show()
            for language in LANGUAGES:
                window._on_language_changed(language)
                for key in PAGE_CLASSES:
                    window.show_page(key)
                    window.layout().activate()
                    window._pages[key].content.updateGeometry()
                    app.processEvents()
                    problems = audit_page(window._pages[key])
                    if problems:
                        failures += len(problems)
                        print(f"[{width}x{height} {language.value} {key}]")
                        for problem in problems:
                            print(f"    - {problem}")
    finally:
        if window is not None:
            window.shutdown()
        services.shutdown()
        tmp.cleanup()

    if failures:
        print(f"\n{failures} layout defect(s) found")
        return 1
    print("no layout defects found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
