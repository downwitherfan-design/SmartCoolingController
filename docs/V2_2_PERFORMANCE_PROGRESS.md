# V2.2 Performance Progress Ledger

Durable record. Updated after each milestone with **actual measured results**,
not intentions. Distinguishes engineering duration from automated-test duration.

## Baseline (mission start)

- Branch `v2.1/production-readiness` @ `422c8c0`, clean tree.
- Token: `~/.openhands/gh_agent_token` (mode 600, never committed).
- Headless smoke (`run.py --headless --headless-seconds 2`) exits 0.
- Suite: 330 tests collected.

## Findings so far (static, to be measured)

- `MainWindow._refresh_health()` runs on a 5 s `QTimer` on the GUI thread and
  calls `HealthService.check()`, whose `_probe_sensors()` calls
  `registry.read()` — a real hardware read (WMI/LHM/NVML) on the GUI thread.
- `ApplicationServices.start()` calls `registry.discover()` synchronously; the
  entry point calls `services.start()` before `window.show()`.
- `LiveChart.append_point()` calls `setData()` per sample and trims by
  `del xs[:n]` (O(n) front shift); `_on_mouse_moved` does a linear `min(range())`
  scan per series.
- `HistoryPage.reload()` runs the SQLite query + decimation synchronously in
  `on_shown()`.
- `QtEventBridge._relay()` emits one queued Qt signal per event with no
  coalescing; snapshots can back up behind a busy GUI thread.

## Phase A — measured baseline (`scripts/diagnostics/perf_probe.py`)

Linux container, simulated backend, `QT_QPA_PLATFORM=offscreen`, 3–40 samples.

| Path | median | p95 | max |
|---|---|---|---|
| `ApplicationServices()` | 1.5 ms | 11.1 ms | 11.1 ms |
| `registry.discover()` (synchronous in `start()`) | 73.2 ms | — | 73.2 ms |
| `psutil` backend `read()` | 36.0 ms | 37.8 ms | 41.1 ms |
| `registry.read()` (all backends) | 35.9 ms | 37.3 ms | 37.7 ms |
| `engine._build_snapshot()` | 37.1 ms | 43.7 ms | 44.2 ms |
| **`HealthService.check()` (was on the GUI timer)** | **36.6 ms** | **50.9 ms** | **50.9 ms** |
| ↳ `probe: sensors` (called `registry.read()`) | 37.2 ms | 42.5 ms | 42.5 ms |
| `MainWindow()` | 11.1 ms | 88.4 ms | 88.4 ms |
| `show_page()` (cold) | 12.9 ms | 53.3 ms | 53.3 ms |
| `LiveChart.append_point()` | 0.068 ms | 0.091 ms | 0.416 ms |
| `HistoryPage.reload('h1')` (empty DB) | 0.017 ms | 0.137 ms | 0.137 ms |
| `MainWindow.shutdown()` | 42.1 ms | — | 42.1 ms |

Micro-profile of the psutil backend: `disk_partitions()` = **33.8 ms** of the
36 ms read; every other collector is < 0.1 ms. The disk enumeration is the
dominant, variable cost.

## Phase C1 — periodic health check moved off the GUI thread (DONE)

Root cause: `MainWindow._refresh_health` ran `HealthService.check()` on the GUI
thread every 5 s, and `_probe_sensors` called `registry.read()`, whose psutil
backend spends ~34 ms in `disk_partitions()`. That is a measured freeze source.

Fix (smallest sound change, single owner for hardware reads):
- New `app/application/health_worker.py`: one long-lived daemon thread with a
  coalescing flag — a request during an in-flight check is folded into the next
  run, so a slow probe can never accumulate a backlog. Never blocks the caller.
- `_probe_sensors` no longer reads hardware: it derives from the telemetry
  engine's cached sensor descriptors (`engine.sensors`). The sampler owns the
  read cadence.
- `MainWindow` drives the worker from the timer; the report returns through the
  existing bus → Qt bridge path; `shutdown()` stops timer + worker.

Measured result: `HealthService.check()` **36.6 ms → 0.025 ms** median
(p95 50.9 → 0.57 ms); `probe: sensors` 37.2 ms → 0.000 ms.

Tests (all mutation-verified — see below):
`test_health_is_refreshed_on_a_timer`, `test_health_badge_reflects_the_report`,
`test_health_check_does_not_run_on_the_gui_thread`,
`test_health_worker_coalesces_overlapping_requests`,
`test_health_probe_does_not_read_hardware`.

Mutation evidence (isolated copy, `/tmp/mut`, never committed):
- restore the hardware-reading probe → `test_health_probe_does_not_read_hardware`
  FAILS.
- run `request()` inline → `test_health_check_does_not_run_on_the_gui_thread`
  FAILS.
- replace coalescing with a queue → `test_health_worker_coalesces_overlapping_requests`
  FAILS with `assert 51 <= 2`.
