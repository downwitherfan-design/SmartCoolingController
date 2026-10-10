# V2.1 — Engineering Decisions

Each entry records a non-obvious decision and the evidence behind it. Decisions
are revisable; the reasoning is what must survive.

---

## D1 — Do not publish a V2.1 release in this mission

The mission brief forbids publishing without owner review. The V2.0.0 audit
found a real release-integrity defect (DEFECT-1: the installer is absent from
`checksums.sha256`). Publishing a new version now would propagate the same
packaging flaw into a fresh release. The fix is prepared on the branch for
owner review; no tag is created.

## D2 — Fix DEFECT-1 in `deliver.py`, not in the workflow

The workflow composes `out/` from the installer directory and the delivery
directory. The checksums file is generated in `deliver.py`. Making
`deliver.py` aware of the installer (`build/installer/…-setup.exe`) keeps a
single source of truth and also makes local `build_windows.bat` produce correct
checksums, not just CI. Alternative (have CI append a line) was rejected: it
would leave the local delivery path still wrong and split the logic.

## D3 — "Not published" must be a real state, not "unavailable"

Several UI paths distinguish a *measured, known-empty* result from an *unknown*
one. Discovery returning zero devices is a fact; an ESP32 that is merely not
connected is not evidence about the network. The UI and diagnostics therefore
keep three states — unavailable (—), known-zero, and value — rather than
collapsing everything unknown to zero or "online". Any change that would show a
number without a measurement is a defect, not a polish.

## D4 — The simulator is a fault-injection instrument, not a success mock

The mock ESP32 already exposes knobs (`silent`, `drop_after_packets`,
`malformed_every`, `bad_version`, `delay_first_status_s`). New tests must drive
these real faults over a real localhost socket and observe the real
`ConnectionManager`. A test that monkeypatches the manager to assert its own
assumption is rejected (the mission's rule 3/5). Where a knob is missing
(oversized frame, half-open read), the mock is extended rather than the test
weakened.

## D5 — Physical hardware evidence is never synthesised

No ESP32, fan, tachometer or sensor is present in this environment. Phase 6
therefore produces a written physical acceptance procedure and marks the
hardware steps BLOCKED. Simulator results are labelled as simulator results in
every report.

## D6 — Highest-value user-facing feature selection (Phase 9, tentative)

Scheduled after the audit. The leading candidate is a **Connection Doctor**
(deterministic self-diagnosis: discovery → connect → handshake → heartbeat →
telemetry, with concrete recovery steps), because the reliability work in
Phases 2–3 produces exactly the measurements it needs and the app already has a
diagnostics page and a diagnostics service to host it. The decision and a
second candidate are confirmed in Phase 9 with evidence from the codebase.
