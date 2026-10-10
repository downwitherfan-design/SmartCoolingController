# V2.1 Sprint Progress

Durable log. Updated after each significant milestone. Times are approximate and
reflect actual work performed; nothing is backdated.

## Sprint start — baseline

- Inspected branch `v2.1/production-readiness` @ `b21ef58`, clean tree, 9 recent
  commits reviewed.
- Reinstalled `libegl1` / `libgl1`; ran full suite: **257 passed**.
- Confirmed remote write access with the new token; pushed
  `v2.1/production-readiness` and opened **PR #2** → `master`.
- Created sprint ledgers (this file, PLAN, DECISIONS).


## Phase 1 — independent fault-injection verification of the six prior fixes

- Harness `/tmp/faultinject.py` copies the tree, reverts one guard at a time and
  runs the single guarding test. All five selected guards are "live":
  - installer checksum (`test_packaging.py`) — TEST FAILED when reverted (good)
  - language coercion (`test_i18n.py`) — TEST FAILED (good)
  - safety-override bypass (`test_cooling_controller.py`) — TEST FAILED (good)
  - RPM ratio wiring (`test_anomaly_detector.py`) — TEST FAILED (good)
  - redaction disabled (`test_logging_redaction.py`) — TEST FAILED (good)
- ERROR-packet state override (`test_connection_hardening.py::
  test_error_packet_is_not_overridden_by_a_connected_promotion`): reverting the
  `promote = False` guard reproduces the failure, then restores. Verified.
- The i18n `conn.error` key test is trivially true (the key exists), so it is
  not a meaningful mutation target; recorded as such rather than claimed.

## Phase 2 — thermal control / failure-safety audit (one severe defect found)

- **Defect A (critical): the automatic control loop was inert.** The telemetry
  supplier (`ApplicationServices._telemetry_supplier`) never sent the fan
  recommendation, so the mock/firmware (which follows `recommendedPwm` while in
  AUTO) never moved. Reproduction against the real services + real in-process
  mock: AUTO computed 10.2 % but the mock fan demand stayed 0.0 %.
- **Defect B (critical, same root): every safety override was silently
  abandoned.** `CoolingController._apply` sent `setPwm` only when the *desktop*
  mode was AUTO, so SAFE mode and the critical-temperature override — which run
  in SAFE/auto-critical — never commanded the fan. Reproduction: SAFE reported
  100 % while the mock stayed at 0.0 %. Manual control was unaffected because
  `set_manual_pwm` sends `setPwm` directly.
- Fixes: `CoolingController.recommended_pwm()` + advertising it in the telemetry
  payload for AUTO; `_force_manual()` taking firmware authority with `setPwm` for
  SAFE/manual/critical; `_restore_firmware_auto()` handing authority back with
  `setMode AUTO` once, after the emergency clears. Added command de-duplication
  so a steady override does not flood the link, plus a `stop`-invalidation fix.
- Verified end to end: AUTO 10.2 % -> mock 10.3 %; SAFE 100 % -> mock 100 %.
- Tests added: 9 unit (`test_cooling_controller.py`), 2 integration
  (`test_services.py`, including a real-link mock test). Full suite green,
  ruff + mypy clean.
