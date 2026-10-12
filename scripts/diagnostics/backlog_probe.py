"""Measure Qt display-snapshot backlog under delayed GUI processing.

Reproduces the failure mode AD-3 addresses: the telemetry engine publishes
display snapshots from a worker thread; if the GUI thread is busy, each queued
``received`` signal is rendered in turn, showing obsolete frames.  The probe
runs the *same* burst through a lossless bridge and a coalescing bridge and
reports, for each:

- snapshots produced vs. snapshots actually rendered,
- maximum pending display work observed during the stall,
- age (staleness) of each rendered snapshot at the moment it was drawn,
- recovery time from the end of the stall to the newest frame being drawn.

It is a diagnostic, not a test: run it by hand and read the numbers.  Nothing
here is imported by the application or the test suite.

Run:
    QT_QPA_PLATFORM=offscreen python scripts/diagnostics/backlog_probe.py \
        --burst 2000 --stall-ms 400
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOPIC = "telemetry.snapshot"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def _run_case(*, burst: int, stall_ms: float, coalesce: bool, app) -> dict[str, float]:
    from app.application.qt_bridge import QtEventBridge
    from app.core.events import EventBus

    bus = EventBus()
    bridge = QtEventBridge(bus)
    rendered: list[tuple[int, float]] = []  # (sequence, staleness_ms)
    rendered_after_stall = 0
    stall_end_ref: list[float] = []
    max_pending = 0

    def slot(event) -> None:
        nonlocal rendered_after_stall
        sequence, published_at = event.payload
        rendered.append((sequence, (time.monotonic() - published_at) * 1000.0))
        if stall_end_ref and time.monotonic() >= stall_end_ref[0]:
            rendered_after_stall += 1

    bridge.received.connect(slot)
    bridge.attach([TOPIC], coalesce=[TOPIC] if coalesce else [])

    start = threading.Event()

    def publisher() -> None:
        start.wait()
        for sequence in range(burst):
            bus.publish(TOPIC, (sequence, time.monotonic()))

    worker = threading.Thread(target=publisher, name="probe-publisher")
    worker.start()
    start.set()

    # Stall the GUI thread for a fixed window while the worker floods the bus.
    stall_until = time.monotonic() + stall_ms / 1000.0
    while time.monotonic() < stall_until:
        max_pending = max(max_pending, bridge.pending_display_events)
        time.sleep(0.002)

    stall_end = time.monotonic()
    stall_end_ref.append(stall_end)
    worker.join(timeout=10)

    # Recover: pump the loop until the newest snapshot has been rendered.
    newest = burst - 1
    recovered_at: float | None = None
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        app.processEvents()
        max_pending = max(max_pending, bridge.pending_display_events)
        if rendered and rendered[-1][0] == newest:
            recovered_at = time.monotonic()
            break
        time.sleep(0.001)

    bridge.detach()
    staleness = [age for _, age in rendered]
    return {
        "produced": float(burst),
        "rendered": float(len(rendered)),
        "replayed_after_stall": float(rendered_after_stall),
        "pending_tracked": 1.0 if coalesce else 0.0,
        "max_pending": float(max_pending),
        "median_staleness_ms": _percentile(staleness, 0.5),
        "p95_staleness_ms": _percentile(staleness, 0.95),
        "worst_staleness_ms": max(staleness, default=0.0),
        "recovery_ms": (recovered_at - stall_end) * 1000.0 if recovered_at else -1.0,
    }


def _report(name: str, stats: dict[str, float]) -> None:
    print(f"\n[{name}]")
    print(f"  snapshots produced          : {int(stats['produced'])}")
    print(f"  snapshots rendered          : {int(stats['rendered'])}")
    if stats["pending_tracked"]:
        pending = str(int(stats["max_pending"]))
    else:
        pending = "n/a (queued inside Qt, not tracked by the bridge)"
    print(f"  max pending display work    : {pending}")
    print(f"  frames replayed after stall : {int(stats['replayed_after_stall'])}")
    print(f"  staleness median / p95 / max: "
          f"{stats['median_staleness_ms']:.1f} / {stats['p95_staleness_ms']:.1f} / "
          f"{stats['worst_staleness_ms']:.1f} ms")
    print(f"  recovery to newest frame    : {stats['recovery_ms']:.1f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--burst", type=int, default=2000)
    parser.add_argument("--stall-ms", type=float, default=400.0)
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    print("Qt display-snapshot backlog probe")
    print(f"burst={args.burst} stall={args.stall_ms:.0f}ms")

    lossless = _run_case(burst=args.burst, stall_ms=args.stall_ms, coalesce=False, app=app)
    coalesced = _run_case(burst=args.burst, stall_ms=args.stall_ms, coalesce=True, app=app)

    _report("lossless (no coalescing)", lossless)
    _report("coalescing (AD-3)", coalesced)

    print("\nConclusion:")
    print(f"  lossless rendered {int(lossless['rendered'])} stale frames "
          f"(worst {lossless['worst_staleness_ms']:.0f} ms old);")
    print(f"  coalescing rendered {int(coalesced['rendered'])} frame "
          f"(worst {coalesced['worst_staleness_ms']:.0f} ms old).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
