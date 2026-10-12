"""Opt-in performance probe for the V2.2 responsiveness work.

Measures the real execution paths that can freeze the GUI — service
construction, hardware discovery, per-backend ``read()``, snapshot build, the
periodic health check and each probe, event-bus delivery, page construction,
chart appends, the history query/render and shutdown — against the simulated
backend (deterministic, no real hardware, no personal data).

It is a diagnostic, not a test: run it by hand and read the numbers.

Run:
    QT_QPA_PLATFORM=offscreen python scripts/diagnostics/perf_probe.py \
        --iterations 200 --health-samples 20

Nothing here is imported by the application or the test suite.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Sample:
    """Collects high-resolution timings (nanoseconds) for one operation."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def add(self, seconds: float) -> None:
        self.values.append(seconds * 1e9)

    def summary(self) -> dict[str, float]:
        if not self.values:
            return {}
        ordered = sorted(self.values)
        n = len(ordered)
        return {
            "n": float(n),
            "median_ms": ordered[n // 2] / 1e6,
            "p95_ms": ordered[min(n - 1, int(n * 0.95))] / 1e6,
            "p99_ms": ordered[min(n - 1, int(n * 0.99))] / 1e6,
            "max_ms": ordered[-1] / 1e6,
        }


def _timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return (time.perf_counter() - start), result


def _settle(app, seconds: float = 0.4) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def _report(name: str, sample: Sample) -> None:
    s = sample.summary()
    if not s:
        print(f"{name:<44} (no samples)")
        return
    print(
        f"{name:<44} n={int(s['n']):>4}  "
        f"median={s['median_ms']:8.3f}ms  p95={s['p95_ms']:8.3f}ms  "
        f"p99={s['p99_ms']:8.3f}ms  max={s['max_ms']:8.3f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200, help="chart append samples")
    parser.add_argument("--health-samples", type=int, default=15)
    parser.add_argument("--reads", type=int, default=30, help="per-backend read samples")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Keep the probe away from any real profile: throwaway data dir.
    os.environ.setdefault("SMARTCOOLING_DATA_MODE", "portable")
    os.environ.setdefault("SMARTCOOLING_DATA_DIR", str(Path("/tmp/scc_perf_probe")))

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from app.services import ApplicationServices

    print("== construction ==")
    construct = Sample()
    for _ in range(3):
        elapsed, _ = _timed(ApplicationServices)
        construct.add(elapsed)
    _report("ApplicationServices()", construct)

    services = ApplicationServices()
    services.config.general.start_telemetry = False
    services.config.general.auto_connect_esp32 = False
    services.config.general.first_run_completed = True

    print("\n== hardware discovery (synchronous in start()) ==")
    discover = Sample()
    elapsed, _ = _timed(services.registry.discover)
    discover.add(elapsed)
    _report("registry.discover()", discover)

    print("\n== per-backend read() ==")
    for backend in services.registry.backends:
        read_sample = Sample()
        for _ in range(args.reads):
            elapsed, _ = _timed(backend.read)
            read_sample.add(elapsed)
        _report(f"backend.read() [{backend.kind.value}]", read_sample)

    print("\n== simulated backend ==")
    services.enable_simulation()
    sim_read = Sample()
    sim = services._simulation
    for _ in range(args.reads):
        elapsed, _ = _timed(sim.read)
        sim_read.add(elapsed)
    _report("simulation.read()", sim_read)

    registry_read = Sample()
    for _ in range(args.reads):
        elapsed, _ = _timed(services.registry.read)
        registry_read.add(elapsed)
    _report("registry.read() (all backends)", registry_read)

    print("\n== telemetry snapshot build ==")
    services.start(simulate=True)
    engine = services.telemetry
    snapshot = Sample()
    for _ in range(args.reads):
        elapsed, _ = _timed(engine._build_snapshot)
        snapshot.add(elapsed)
    _report("engine._build_snapshot()", snapshot)

    print("\n== health check + probes (runs on the GUI timer) ==")
    health = Sample()
    for _ in range(args.health_samples):
        elapsed, _ = _timed(services.health.check)
        health.add(elapsed)
    _report("HealthService.check() (all probes)", health)

    for name, probe in services.health._probes.items():
        probe_sample = Sample()
        for _ in range(args.health_samples):
            elapsed, _ = _timed(probe)
            probe_sample.add(elapsed)
        _report(f"  probe: {name}", probe_sample)

    print("\n== GUI construction & pages ==")
    from app.ui.shell import PAGE_CLASSES, MainWindow

    elapsed, win = _timed(MainWindow, services)
    construct.add(elapsed)
    win.resize(1648, 920)
    win.show()
    _settle(app)
    page_open = Sample()
    for key in PAGE_CLASSES:
        elapsed, _ = _timed(win.show_page, key)
        _settle(app, 0.05)
        page_open.add(elapsed)
    _report("MainWindow()", construct)
    _report("show_page() (cold, per page)", page_open)

    print("\n== chart appends ==")
    dash = win._pages.get("dashboard")
    if dash is not None and hasattr(dash, "chart_panel"):
        chart = dash.chart_panel.chart
        base = time.time()
        append = Sample()
        for i in range(args.iterations):
            elapsed, _ = _timed(chart.append_point, "cpu", base + i * 0.5, 50.0 + (i % 20))
            append.add(elapsed)
        _report("LiveChart.append_point()", append)
        _report("  (capacity)", Sample())
        print(f"  capacity={chart._capacity} retained={len(chart._data['cpu'][0])}")

    print("\n== history query + render ==")
    hist = win._pages.get("history")
    if hist is None:
        win.show_page("history")
        _settle(app, 0.1)
        hist = win._pages.get("history")
    if hist is not None:
        reload_sample = Sample()
        for _ in range(5):
            elapsed, _ = _timed(hist.reload, "h1")
            reload_sample.add(elapsed)
        _report("HistoryPage.reload('h1')", reload_sample)

    print("\n== shutdown ==")
    shutdown = Sample()
    elapsed, _ = _timed(win.shutdown)
    shutdown.add(elapsed)
    _report("MainWindow.shutdown()", shutdown)
    elapsed, _ = _timed(services.shutdown)
    shutdown.add(elapsed)
    _report("services.shutdown()", shutdown)

    gc.collect()
    print("\n== done ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
