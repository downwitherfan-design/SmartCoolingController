# V2.2 Architecture Decisions

Short, dated records of decisions that shape the performance work. Each entry
states the decision, the evidence, and the invariant it protects.

## AD-3 — Coalesce display snapshots in the Qt bridge; never coalesce control/alert state

Status: implemented and tested (`app/application/qt_bridge.py`,
`tests/ui/test_qt_bridge.py`).

`QtEventBridge` re-emits every attached bus event as a queued Qt signal. When the
GUI thread is busy — a slow page paint, a range change, a resize — a run of
`telemetry.snapshot` events piles up in Qt's event queue and each is rendered in
turn afterwards, showing obsolete frames.

Decision: `attach(..., coalesce={TOPIC_SNAPSHOT})`. For a coalescing topic the
bridge keeps only the newest event in a one-slot-per-topic map and schedules a
single drain on the GUI thread. Delivery stays on the GUI thread, and at most one
drain callback is ever queued. Non-coalescing topics keep the existing lossless
synchronous path.

What is **not** coalesced (lossless): `TOPIC_ALERT`, `TOPIC_ALERT_CLEARED`,
`TOPIC_NOTIFY`, `TOPIC_HEALTH`, `TOPIC_CONNECTION`, `TOPIC_SESSIONS`,
`TOPIC_MODE`, `TOPIC_DISCOVERY`. The service layer consumes the same snapshots
directly from the bus (cooling control, alerts, persistence) before the bridge
ever sees them, so display coalescing cannot drop a control or persistence
decision.

Invariant protected: GUI display work is bounded during congestion while the
newest visual state is still rendered; critical events are never lost.

Evidence (`scripts/diagnostics/backlog_probe.py`, 3000 snapshots published from a
worker while the GUI thread was stalled 500 ms):

| Path | frames rendered | frames replayed after the stall | max pending | recovery |
|------|-----------------|----------------------------------|-------------|----------|
| lossless | 3000 | 3000 | n/a (queued inside Qt) | 2.8 ms |
| coalescing (AD-3) | 1 | 1 | 1 | 0.1 ms |

Without coalescing the user watches 3000 obsolete frames replay after the stall;
with it, exactly the newest frame is drawn and the backlog is bounded to one.

Shutdown hardening (found while auditing the same path): `EventBus.publish`
snapshots its handler list, so a `detach()` during application shutdown could run
*after* the bridge's `_relay` was captured but before it was called — delivering
an event to a window that was being torn down. `detach()` now clears an
`_attached` flag before unsubscribing, and both `_relay` and `_drain` check it, so
a detached (or never-attached) bridge delivers nothing.
`test_detach_during_publication_delivers_nothing` pins the race; re-attach
resumes delivery.

## AD-4 — One fitted-text primitive for every custom painter; the GUI gate renders text

Custom-painted widgets (`gauges.py`, `fan.py`, `schematic.py`, `atoms.py`) sized
their text with magic pixel rectangles and hard-coded point sizes. The layout
checker only inspected QLabel geometry, so the defects it could not see were
exactly the ones the owner saw: the gauge's big number was vertically clipped
(36 px of ink in a 26 px box), the trend sublabel `Normal · Simulated` needed
162 px in a 112 px box, and the schematic node titles/subtitles clipped in both
English and Persian (`فن چهارسیمه` needs 133 px in a 102 px box).

Decision: a single `app/ui/widgets/base.py::fit_text` / `draw_fitted_text`
defines "fitted text" — shrink the point size to `min_scale` (0.62) of the base,
then elide horizontally; wrapping runs are fitted by height. Every custom
painter calls it instead of `painter.drawText` for boxed text, and every box is
sized from the widget's real geometry rather than a magic constant.

The fitter and the detector must agree on what "fits" means, so both measure the
**ink** (`QFontMetricsF.tightBoundingRect`), not the advance box. Persian and
Arabic glyphs overhang their advance width: `هیچ` reports a 24px advance but
29px of ink, so a fitter trusting `boundingRect` left it overflowing a 26px box
and the detector flagged it. `tests/ui/test_painted_text.py` pins this case.

Enforcement: `layout_check.py::find_painted_text_problems` renders a widget
through a `QPainter` subclass that records each `drawText` whose ink (or wrapped
height) exceeds its rectangle. The head-less `gui_audit.py` runs both the
geometry and the painted-text checks at five sizes × two languages × every page,
and `tests/ui/test_painted_text.py` pins each widget plus a self-check that the
detector actually bites.

Invariant protected: no text a custom painter draws can escape its box, in any
language, because the box and the metrics are compared by the same code that
draws it — not by a human reading a screenshot.

## AD-1 — Preserve the lossless/critical event path when coalescing display snapshots

Status: superseded by AD-3 (implemented).

The telemetry sampler publishes `telemetry.snapshot` at the sample interval.
The Qt bridge re-emits each event as one queued Qt signal. If the GUI thread is
busy, ordinary *display* snapshots can accumulate without bound.

Decision: introduce a latest-value coalescing dispatcher for the **display**
path only. Snapshots are visual state, not a log — the newest one supersedes the
older ones, so keeping only the latest pending snapshot is correct for display.
Everything that is **not** display (alerts, cooling decisions, connection-state
transitions, command acknowledgements, persistence) keeps the existing lossless
synchronous delivery.

Invariant protected: no display optimization may drop data required by control
logic or persistence (Phase I). The coalescer must never sit between the engine
and the persistence writer, the cooling controller, or the alert service.

## AD-2 — One live anomaly path; `AnomalyDetector` is a tested reference, not a second live engine

Status: decided (live defect fixed alongside this record).

There are two anomaly implementations:

- `app/analytics/anomaly.py::AnomalyDetector` — a standalone, stateful detector
  (`rapid_rise`, `sensor_disappearance`, `fan`, `electrical`). It is exercised
  only by `tests/unit/test_anomaly_detector.py`; no production code constructs
  it.
- `ThermalAnalyser._detect_anomalies` — the detector that actually runs, driven
  by `TelemetryEngine` and published on `telemetry.anomaly`.

Decision: the analyser stays the single live detector. `AnomalyDetector` is kept
as a documented, independently-tested reference of the intended heuristics — it
is not wired into the engine. Wiring a second detector would duplicate state and
emit anomalies on a topic the UI shell does not subscribe to (`TOPIC_ANOMALY` is
not in the bridge's attach set), so it could not reach the user without new UI
work — out of scope for a performance mission.

Invariant protected: exactly one component owns anomaly detection in the running
application, so the two implementations can never disagree in production.

Defect found while auditing this: the live analyser never emitted
`SENSOR_DISAPPEARED` (a sensor going from a valid reading to `UNAVAILABLE`),
although `AnomalyThresholds.stale_after_s` and `AnomalyKind.SENSOR_DISAPPEARED`
exist for exactly that case. The live detector now tracks metric availability
transitions and emits `SENSOR_DISAPPEARED`, consuming the `stale_after_s`
threshold that was previously declared but never read.

## AD-5 — Aggregate the history range inside SQLite; never cap it with a row limit

Status: implemented and tested (`app/persistence/repositories.py`,
`app/ui/pages/history.py`, `tests/integration/test_history_range.py`).

The history page loaded a range with `SnapshotRepository.range(since, until,
limit=MAX_ROWS)` — `ORDER BY ts ASC LIMIT 20000` — then decimated in Python. On
a database sampled once per second this is a correctness defect, not just a cost:
the ascending limit returns the *oldest* 20 000 rows, so a 24h view silently
showed only its first ~5.5 h and hid the most recent ~18.5 h, and every summary
statistic (max, average, time-above) described that stale prefix. Measured on a
250 000-row database, the old 24h query's newest row was 18.4 h behind now and
the old 7d query's was 64 h behind.

Decision: two dedicated, index-driven queries replace the capped load.

- `series_bucketed(since, until, bucket_s)` — `GROUP BY CAST(ts / bucket_s)` with
  `AVG(...)` per metric. It scans the `ts` index once and returns `range /
  bucket_s` points, so the chart always covers the whole range. Averaging in SQL
  matches the previous in-Python decimation, so the drawn curve is unchanged.
- `aggregate(since, until, threshold_c, bucket_s)` — `COUNT/MIN/MAX/AVG` over the
  whole range plus the count of samples at or above the threshold. The time-above
  figure is `count(above) x interval`, where `interval` is the observed mean
  spacing between samples (capped at the display bucket so a sampling gap cannot
  over-count). The engine samples on a fixed cadence, so this is exact for the
  normal case and avoids a window function that materialises every row.

The in-memory ring remains the fallback when the database has nothing for the
range (before the writer flushes, or in portable mode); it is bucketed and
summarised into the same shape by `_bucket_rows` / `_summarise`.

Evidence (`scripts/diagnostics/history_probe.py`, 250 000 rows at 1 s):

| range | old newest-missing | new newest-missing | old total | new total |
|-------|--------------------|--------------------|-----------|-----------|
| 1h    | 0.1 min            | 0.1 min            | 12.7 ms   | 4.9 ms    |
| 24h   | 1106.7 min         | 0.3 min            | 98.6 ms   | 82.6 ms   |
| 7d    | 3833.4 min         | 7.3 min            | 67.8 ms   | 233.3 ms  |

The 7d path is slower because it now scans the whole range — the work the old
code skipped by loading only a 20 000-row prefix and drawing the wrong window.
That scan is the price of a correct chart; `_decimate` (the old Python bucketing)
is gone, its logic folded into the SQL query and the ring fallback.

Invariant protected: a range the user selects is the range the chart draws and
the statistics describe — never a row-capped prefix that hides the newest data.
