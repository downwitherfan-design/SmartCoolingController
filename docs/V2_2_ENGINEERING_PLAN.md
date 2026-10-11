# V2.2 Engineering Plan — performance, responsiveness, GUI correctness

Branch: `v2.1/production-readiness` (PR #2 → `master`). No merge, no release, no
tag change. Continues `docs/V2_2_PERFORMANCE_PLAN.md`.

## Objective

Make the Windows desktop app responsive and visually correct without regressing
ESP32 communication, telemetry accuracy, fan-control safety, persistence,
Persian localization or packaging. Prove every claim by running the real path.

## Method

Measure → reproduce → diagnose → decide → implement → execute → inspect → test →
profile again → fix regressions → rerun → commit → verify remotely. A test is not
proof until it is run, and a fix is not proven until the test fails without it
(mutation check on an isolated copy).

## Phases and status

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Recover real state; verify the three reported commits and diffs | done |
| 1 | Publish verified local work to the remote branch; update PR #2 | done |
| 2 | Reproduce/measure the freeze; baseline with `perf_probe.py` | done |
| 3 | Remove blocking work from the GUI thread (discovery, health, shutdown) | in progress |
| 4 | Bound telemetry delivery without losing critical events | done (AD-3) |
| 5 | Charts / history / graphical components | in progress |
| 6 | Reproduce and repair the ESP32 Link + Dashboard defects | in progress |
| 7 | Anomaly + stale-sensor end-to-end verification | in progress |
| 8 | Memory / CPU / startup / package cost | todo |
| 9 | Extended reliability + soak workload | todo |
| 10 | Responsive-architecture invariants + event-loop delay targets | in progress |
| 11 | Quality gates (pytest, ruff, mypy, headless, layout audit) | running continuously |
| 12 | Commit, push, PR/CI evidence | running continuously |

## Environment

Linux container, Python 3.13 `.venv`, `QT_QPA_PLATFORM=offscreen`. Windows-only
and physical-ESP32 checks are reported as limitations, never claimed as executed.
