# V2.2 Engineering Progress — living ledger

Records actual commands, real outcomes, measured timings and next steps. Every
number here was observed in this environment; Windows-only checks are marked as
limitations.

Environment: Linux container, Python 3.13, `.venv`, `QT_QPA_PLATFORM=offscreen`.

## Phase 0 — recover the real state (done)

Commands and results:

- `git status --short --branch` → clean tree on `v2.1/production-readiness`.
- `git log --oneline --decorate -20` → head `5049494`; the three reported commits
  `60509a2`, `5908910`, `5049494` are present.
- `git branch -avv` → the branch had **no remote counterpart** before Phase 1.
- `git diff --stat` / `git diff --check` → no uncommitted changes, no whitespace
  errors.

## Phase 1 — publish (done)

- Token stored outside version control at `~/.openhands/gh_agent_token` (mode
  600) with a git credential helper at `~/.openhands/git-credential-gh.sh`. The
  raw value is never printed, committed or embedded in a tracked file.
- `git push -u origin v2.1/production-readiness` → `422c8c0..5049494`.
- Remote head verified `50494944a523777537328ce25a62473441c1f7bc`; the three
  commits return HTTP 200 from the commits API.
- PR #2 retitled and rewritten to cover the combined V2.1 + V2.2 scope.

## Phase 2 — baseline measurements (done)

Command:

```
QT_QPA_PLATFORM=offscreen python scripts/diagnostics/perf_probe.py \
    --iterations 500 --health-samples 30 --reads 50
```

Baseline (median, before any fix):

| Path | median |
|------|--------|
| `backend.read()` [psutil] | 36.958 ms |
| `registry.read()` (all backends) | 36.656 ms |
| `engine._build_snapshot()` | 37.364 ms |
| `HealthService.check()` | 0.018 ms |
| `LiveChart.append_point()` | 0.076 ms |
| `MainWindow.shutdown()` / `services.shutdown()` | 199.485 ms |

Root causes found:

1. `psutil.disk_partitions()` measured **~33.8 ms** per call on this host
   (15 mounts) and ran on every 0.5 s snapshot.
2. `ConnectionManager.stop()` joins the connection loop, which waited in a plain
   `asyncio.sleep`; the top of the reconnect backoff is 30 s, so exit could block
   for up to half a minute. Measured 101 ms when idle (the 0.2 s poll tick).

## Phase 3/2 — fixes and after measurements

Commit `9ccb3dc` — psutil partition cache + interruptible connection wait:

| Path | before | after |
|------|--------|-------|
| `PsutilBackend.read()` | 37.0 ms | 2.5 ms |
| `registry.read()` | 36.7 ms | 2.9 ms |
| `engine._build_snapshot()` | 37.4 ms | 3.4 ms |
| `connection.stop()` | 101 ms | 0.5 ms |
| `services.shutdown()` | 199.5 ms | 3.4 ms |

Tests added: `tests/unit/test_psutil_backend.py` (cache TTL, fresh usage,
transient-failure retention), `tests/integration/test_connection.py`
(`test_stop_interrupts_a_long_reconnect_backoff`,
`test_stop_interrupts_the_idle_poll_wait`). Mutation checks: bypassing the cache
fails 3 tests; restoring `asyncio.sleep` blocks for the full 5 s and fails.

Commit `9910c8a` — display-snapshot coalescing in `QtEventBridge` (AD-3):
a 500-event worker burst collapses to one pending display event and renders only
the newest; 50 alert events are all delivered. Mutation: disabling coalescing
fails 3 tests.

## Phase 6 — GUI correctness in custom-painted widgets

The owner's screenshots showed clipping that the geometry audit reported as
"no layout defects found", because `find_layout_problems` only inspects QLabel
geometry. Rendering the pages through a `QPainter` subclass that records
`drawText` found the real defects immediately:

| Widget | Run | Box | Needed |
|--------|-----|-----|--------|
| `TemperatureGauge` | value `42` | 112x26 | 62x36 (vertically clipped) |
| `TemperatureGauge` | `Normal · Simulated` | 112x18 | 162x12 |
| `Schematic` | `4-Wire Fan` | 102x24 | 115x15 |
| `Schematic` | `فن چهارسیمه` (fa) | 102x24 | 133x19 |
| `Schematic` | `RPM Unavailable` | 102x46 | 125x12 |
| `FanVisualizer` | `دور بر دقیقه` (fa) | 47x14 | 68x12 |

Fix: `base.py::fit_text` / `draw_fitted_text` (shrink to `min_scale`, then
elide) now backs every boxed run in `gauges.py`, `fan.py`, `schematic.py` and
`atoms.py`; magic pixel rects were replaced with real geometry. The fan readout
now splits each row into a label half and a value half so a long label cannot
paint under a right-aligned value.

Detection: `layout_check.py::find_painted_text_problems` renders a widget
through the recording painter; `gui_audit.py` runs it alongside the geometry
check at five sizes (including the 1648x920 screenshot size) × two languages ×
every page — **0 defects**. `tests/ui/test_painted_text.py` pins each custom
widget and includes a deliberate-overflow self-check that fails if the detector
stops biting. AD-4 records the decision.

## Quality gates (as of `9337476`)

- `ruff check app run.py tests scripts` → pass
- `python -m mypy app` → success, 95 files
- `pytest` → see `docs/V2_2_PERFORMANCE_RESULTS.md` for the current full count.

## Open items / next steps

- Phase 3: audit remaining GUI callbacks (history range change, diagnostics,
  support-bundle zip, network discovery) for blocking I/O.
- Phase 5: profile chart render under a full buffer and hidden-chart repaint.
- Phase 8: memory/CPU/startup and package cost.
- Phase 9: long soak run.

## History data integrity + export reliability (as of `9337476`)

This sprint audited the history/export data path against an independent oracle
and fixed two correctness defects.

Defect 1 — time-above inflated by sampling gaps. `aggregate()` multiplied the
count of above-threshold samples by the *chart's display bucket* (60 s on 24 h,
900 s on 7 d), not the sampling cadence. A gap between samples therefore
invented time without bound: 20 above-threshold samples ten minutes apart
reported 651.6 s "above" over a 619 s span — 105% of the whole range, ~60x the
real elapsed time. The product is now `count x interval_s` (the nominal
`telemetry.sample_interval_ms`), capped by the span between the first and last
in-range sample, so no gap can manufacture time; with no nominal cadence the
observed mean spacing is the fallback.

Defect 2 — export/history paging dropped rows. `iter_range()` used a strict
`ts > cursor`, so the row exactly at `since` was skipped, and a `ts`-only cursor
could skip or repeat rows that shared a timestamp at a page boundary. It now
keys on the row value `(ts, id)` with a strict `(ts, id) > (cursor)` seeded at
`(since, -1)` — lower bound inclusive, ties never lost. Row-value keyset also
lets SQLite walk `idx_snapshots_ts` directly instead of building a temp B-tree
sort per page.

Evidence:

- `tests/integration/test_history_audit.py` (new) checks the bucketed series and
  the summary against an independent loop oracle on regular, irregular and gapped
  data; inclusive boundaries; empty and single-sample ranges; naive vs aware
  timestamps; gap inflation; pagination duplicates; and a meta-test proving the
  newest-data guard rejects a capped-prefix implementation.
- `tests/integration/test_services.py` exports 120 000 rows (crossing the old
  100 000 cap) and asserts every row is present, ordered and unique; a
  fault-injection test proves a mid-write `OSError` leaves no partial file and is
  recorded as an error.
- Streaming memory is bounded by the page size, not the row count: 200 000 rows
  peak at 4.45 MB via `iter_range` vs 88.25 MB materialised (19.9x less), and
  1 000 000 rows at `page=5000` stay at 4.45 MB.

History view performance (AD-7): the worst case — a cold week-view open on a
database at its 2M-row retention cap — is ~0.5 s; the hour view is 4 ms. A
covering index (~14%), a larger page cache (no effect) and a single grouped scan
(1.07x, slower on the hour view) were each measured and rejected, so the simple
pair of queries is kept. The single-scan experiment was reverted.

Quality gates: `ruff` clean, `mypy` clean (95 files), `pytest` 400 passed.
