# V2.1 Sprint Plan

Autonomous engineering sprint on `SmartCoolingController`.  Correctness above
everything: every change is executed and verified, not merely written.

## Baseline (captured at sprint start)

| Item | Value |
| --- | --- |
| Branch | `v2.1/production-readiness` |
| HEAD | `b21ef58` |
| Working tree | clean |
| Python | 3.13.15 (`.venv`) |
| Tests | 257 passed |
| ruff | pass |
| mypy | pass (93 source files) |
| Remote | `origin` = downwitherfan-design/SmartCoolingController |
| PR | #2 (open) targets `master` |

Environment note: the container needed `libegl1` / `libgl1` reinstalled
(`apt-get update && apt-get install -y --no-install-recommends libegl1 libgl1`)
before PySide6 could import.

## Milestones

0. Preserve workspace; write sprint ledgers; reproduce baseline. *(done)*
1. Independently verify the six prior fixes, including a fault-injection check
   that each regression test fails when its fix is removed.
2. Thermal-control and failure-safety boundary / fault-injection tests.
3. Connection Doctor: real staged diagnosis, service + UI, EN/FA, tests.
4. Privacy-safe support bundle; inspect the real archive bytes.
5. Extended network / lifecycle stress against the deterministic simulator.
6. Windows delivery and release-integrity revalidation; Node-runtime warning.
7. Performance / UI / recovery audit with measurements.
8. Repeated test-fix-verify cycles and full quality gates.
9. Git checkpoints, PR updates, transfer artifacts.

## Ground rules

- Reproduce before fixing; execute after fixing.
- No test is "passing" unless it was run.
- Simulated hardware is never described as a physical ESP32 test.
- Do not weaken tests or skip to get green.
- The token is used only as a transient git credential; its value is never
  committed to the repository.
