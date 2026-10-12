# V2.2 Performance, Responsiveness & GUI Correctness Plan

Branch: `v2.1/production-readiness` (continues the existing work; PR #2 → `master`).
No merge, no release, no tag change during this mission.

## Goal

Make the Windows desktop app responsive and robust without breaking ESP32
communication, telemetry accuracy, fan-control safety, persistence,
localization or packaging.

## Method (non-negotiable)

Measure → diagnose → design → implement → execute → inspect → regression-test →
profile again → fix → rerun → commit. Every significant change is proven by
running the real affected path; a written test is not proof until it is run and
observed to fail without the fix.

## Phases

- **A. Baseline** — reproduce the freeze with the simulated backend; instrument
  startup, hardware discovery/read, snapshot build, event delivery, health
  probes, page open, chart append, history query/render, shutdown; rank by
  measured user impact.
- **B. Startup** — make hardware discovery non-blocking if it blocks the first
  paint; explicit init states; bounded timeouts; cancellation.
- **C. GUI thread** — remove synchronous hardware reads from the health timer;
  audit every UI slot/timer for blocking I/O (SQLite, exports, network, scans).
- **D. Telemetry delivery** — bound the GUI's pending snapshot work with a
  latest-value coalescing dispatcher while preserving lossless critical events.
- **E. Charts/animations** — batch appends, bisect the cursor lookup, adaptive
  rendering; make performance modes real (no no-op controls).
- **F. GUI defects** — reproduce the screenshot clipping/overlap on the ESP32
  page and Dashboard; fix the layout constraint, not the symptom; strengthen the
  weak layout tests to assert real painted geometry.
- **G. Performance contract** — defensible, measured targets.
- **H. Stress** — repeatable scenarios + a soak workload; report real durations.
- **I. Safety/data contracts** — verify the V2/V2.1 invariants survive.
- **J. Quality gates** — pytest, ruff, mypy, headless smoke, build, mutation
  checks; update PR #2.
- **K. Durable execution** — keep the ledger current; checkpoint and continue.

## Environment

Linux container, Python 3.13 `.venv`, `QT_QPA_PLATFORM=offscreen`. Windows-only
and physical-ESP32 checks are reported as limitations, never claimed.
