# V2.2 Architecture Decisions

Short, dated records of decisions that shape the performance work. Each entry
states the decision, the evidence, and the invariant it protects.

## AD-1 — Preserve the lossless/critical event path when coalescing display snapshots

Status: decided (implementation pending measurement).

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
