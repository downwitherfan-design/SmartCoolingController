# V2.2 Performance Results

Separates raw observations, derived statistics, hypotheses and verified
conclusions. Environment: Linux container, Python 3.13, `.venv`,
`QT_QPA_PLATFORM=offscreen`, simulated hardware backend (no real sensors, no
personal data). Windows-only behaviour is out of scope here and never claimed.

## Raw observations

`psutil` micro-profile (median of 30 calls, this host):

| call | median |
|------|--------|
| `cpu_percent(None)` | 0.026 ms |
| `cpu_freq()` | 0.082 ms |
| `virtual_memory()` | 0.037 ms |
| `sensors_temperatures()` | 1.533 ms |
| `disk_partitions(all=False)` | **33.821 ms** |
| `disk_usage('/')` | 0.005 ms |
| `sensors_battery()` | 0.008 ms |

`scripts/diagnostics/perf_probe.py` before and after the Phase 2/3 fixes
(median; n per the probe):

| Path | before | after |
|------|--------|-------|
| `PsutilBackend.read()` | 36.958 ms | 2.696 ms |
| `registry.read()` | 36.656 ms | 2.857 ms |
| `engine._build_snapshot()` | 37.364 ms | 3.376 ms |
| `HealthService.check()` | 0.018 ms | 0.019 ms |
| `LiveChart.append_point()` | 0.076 ms | 0.076 ms |
| `show_page()` (cold, per page) | 12.293 ms | 12.760 ms |
| `MainWindow.shutdown()` | 199.485 ms | 9.864 ms |
| `services.shutdown()` | 199.485 ms | 9.864 ms |

## Derived statistics

- Storage sampling was ~92% of the per-snapshot cost (33.8 of 37.0 ms). The
  engine samples every 0.5 s on its own thread; that thread is not the GUI
  thread, but the sample still gates snapshot publication, so trimming it keeps
  displayed telemetry fresh and reduces CPU.
- Shutdown was dominated by one blocking join: `connection.stop()` measured
  101.65 ms of the 199 ms total when idle, and the loop's worst-case wait is the
  30 s top of the reconnect backoff.

## Hypotheses

- H1: `disk_partitions()` cost scales with mount count (15 here). On a Windows
  desktop with far fewer mounts it is cheaper, but caching is still strictly
  better and removes the per-sample syscall walk.
- H2: the 30 s backoff wait would block application exit for up to 30 s on a
  machine that is reconnecting when the user quits.

## Verified conclusions

- C1 (verified): caching the mount list behind a 30 s TTL while still reading
  per-mount usage every sample reduces `PsutilBackend.read()` from ~37 ms to
  ~2.5 ms. Guarded by `tests/unit/test_psutil_backend.py`; bypassing the cache
  fails three tests.
- C2 (verified): routing the idle poll and the reconnect backoff through an
  interruptible wait that `stop()` signals bounds exit time. Guarded by
  `test_stop_interrupts_a_long_reconnect_backoff` (reverting to `asyncio.sleep`
  blocks the full 5 s and fails) and `test_stop_interrupts_the_idle_poll_wait`.
- C3 (verified): display-snapshot coalescing bounds the pending render queue to
  one event per topic under a 500-event worker burst while the GUI thread is
  busy, and only the newest is rendered; 50 alert events are all delivered.
  Guarded by `tests/ui/test_qt_bridge.py`; disabling coalescing fails three
  tests.

## Remaining limitations

- All figures are from the simulated backend in a Linux container. Real Windows
  numbers (LHM/WMI, real DPI, real installer) require a Windows run and are not
  claimed.
- `HealthService.check()` is already sub-millisecond here; its previous
  GUI-thread cost (median 36.6 ms → 0.025 ms) was fixed in an earlier phase.
