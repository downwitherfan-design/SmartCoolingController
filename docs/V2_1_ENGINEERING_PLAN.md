# V2.1 — Production-Readiness Engineering Plan

Branch: `v2.1/production-readiness`. Baseline: `master` @ `eea73fe`
(= released `v2.0.0` tag `4f5c6d3` + one docs-only commit).

This is the durable plan and status ledger for the V2.1 mission. See
`V2_1_PROGRESS.md` for the chronological log (commands, results, commits) and
`V2_1_DECISIONS.md` for the rationale behind non-obvious choices.

Status vocabulary: `TODO`, `IN PROGRESS`, `BLOCKED`, `PASS`, `FAIL`.

---

## Verified V2.0.0 baseline (Phase 1)

| Claim | Verdict | Evidence |
| --- | --- | --- |
| V2 changes are in the default branch | PASS | `git log v1.0.0..master` shows the merge `4f5c6d3` and the V2 commits |
| `v2.0.0` points at the intended commit | PASS | `git rev-list -n1 v2.0.0` = `4f5c6d3` (the V2 merge) |
| Displayed version == package metadata | PASS | `APP_VERSION`, `pyproject`, spec, `.iss`, `version_info`, README, notes all `2.0.0` |
| Release assets correspond to the tagged source | PASS | `installer-manifest.json.git_sha` = `4f5c6d3` = tag commit |
| `latest` URL resolves to v2.0.0 | PASS | `gh api .../releases/latest` → `v2.0.0` |
| Source archive checksum matches manifest | PASS | downloaded + `sha256sum` == published |
| Installer checksum is verifiable per the guide | **FAIL** | `checksums.sha256` omits `...-setup.exe` (DEFECT-1) |
| Installer / portable actually ran on Windows | BLOCKED | Linux container cannot execute Windows binaries |

> Time is measured in wall-clock UTC. "Hours worked" is not claimed; see the
> progress log for exact command timestamps.

---

## Phases

### Phase 1 — Independently audit the V2.0.0 release — PASS (with DEFECT-1)
- [PASS] 1.1 Git/version integrity (see table).
- [PASS] 1.2 Asset names, checksums, source-archive contents.
- [FAIL] 1.2 Installer verification instructions are broken (DEFECT-1).
- [BLOCKED] 1.2 Executing the Windows installer / portable on Windows.

### Phase 2 — Deep ESP32 connection reliability audit — IN PROGRESS
Test the 16 scenarios against the **real** manager + transports + mock.
Focus areas: heartbeat-timeout detection after first telemetry, half-open
recovery, oversized frames, unsupported versions, stale telemetry cutover,
disconnect-during-establishment, shutdown races, duplicate UI connects.

### Phase 3 — Make the simulator a serious test instrument — IN PROGRESS
Extend the deterministic harness (timeouts, drops, malformed, oversized,
half-open, restart, silent). Add a bounded multi-hundred-transition stress test.

### Phase 4 — Audit the animated connection visualization — TODO
Timer lifecycle, truthful state mapping, unavailable-vs-fabricated values,
reduced-motion / CPU while idle. Deterministic UI tests on the real widget.

### Phase 5 — Windows install / upgrade / portable / recovery — TODO
Inspect installer + delivery scripts. Extend Windows CI for feasible checks.
Separate completed from proposed checks. No destructive tests on real data.

### Phase 6 — Hardware safety, telemetry truth, cooling behavior — TODO
Conservative failure modes; document firmware- vs software-guaranteed safety;
write the physical acceptance checklist. No fabricated hardware results.

### Phase 7 — Persistence, settings, i18n, lifecycle — TODO
DB create/reopen/interrupted-shutdown, migration, corrupted config, large
history, locks, idempotent shutdown, EN/FA startup, runtime switch, RTL.

### Phase 8 — Security, privacy, dependencies, CI — TODO
Redaction coverage, support-bundle privacy, input validation, least-privilege
workflows, Node-runtime action compatibility, dependency/license audit.

### Phase 9 — Selected user-facing improvements (max two) — TODO
Candidates ranked by repository evidence; implement fully, localize EN+FA, test.

### Phase 10 — Performance and long-duration stability — TODO
Profile real paths; bounded queues; idle resource use. Run the longest feasible
soak and report the **actual** duration.

### Phase 11 — Comprehensive release quality gates — TODO
`ruff`, `mypy`, `pytest`, `run.py --headless`, packaging/installer/delivery
commands; Python-version matrix as far as runners allow.

### Phase 12 — Final integration and reviewable delivery — TODO
Coherent commits on `v2.1/production-readiness`; open a PR (do **not** merge,
do **not** publish). Final report with the ten required items.

---

## Acceptance criteria

1. Every release claim re-checked against real artefacts or marked BLOCKED.
2. Each real defect has a regression test that fails before and passes after.
3. Simulator distinguishes success-path mocks from genuine fault injection.
4. No fabricated telemetry anywhere; unknown stays unknown in UI and logs.
5. Physical ESP32 work explicitly marked pending with a written procedure.
6. All feasible quality gates run and reported with actual results.
7. A reviewable PR exists; no merge, no new release.

## Blockers

- **Physical ESP32 / fan / sensors** unavailable → Phase 6 physical tests BLOCKED.
- **Windows execution** unavailable in this Linux container → installer/portable
  run tests BLOCKED (asset integrity still verifiable).
- `gh` requires `GH_TOKEN`/`GITHUB_TOKEN` in this environment (documented).
