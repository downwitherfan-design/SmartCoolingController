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

## Phase 2 (cont.) — desktop notifications were a silent no-op

- **Defect C (functional): the Windows-notifications setting did nothing.**
  ``AlertService._notify`` published to ``alerts.notify`` but no component ever
  subscribed, so a warning/critical alert produced no desktop notification at
  all while the Settings switch implied it would.
- Fix: exported ``TOPIC_NOTIFY`` and consumed it in the shell; a raised alert
  now shows a ``QSystemTrayIcon`` balloon (Warning/Critical icon), skipped when
  the tray is hidden. The service still owns the *whether* (severity gate +
  setting); the shell owns the *how*.
- Tests: 4 unit (`test_notifications.py`) and 3 integration
  (`test_services.py`) covering publish/suppress/severity gating.
- Note: default ``alerts.windows_notifications`` is ``False`` (opt-in,
  privacy-respecting); tests enable it explicitly.

## Phase 4 — config audit: dead settings removed (T4)

- Audited every field in `app/core/config.py` against real readers. Two Settings
  controls (`settings.dashboard_preset`, `settings.view_mode`) stored a value
  that no code consumed; `telemetry.sensor_refresh_interval_s`,
  `appearance.hidden_cards` and `appearance.card_order` had no reader at all.
- Decision: remove, do not guess. `sensor_refresh_interval_s` especially must
  *not* be wired as a read-throttle — the telemetry engine samples every tick
  for live telemetry and LibreHardwareMonitor already refreshes inside `read()`;
  honouring a 300 s interval would freeze CPU/GPU temperatures.
- Removed the four config fields, the two Settings rows and the orphaned EN/FA
  i18n keys (`settings.preset.*`, `settings.view.*`). Regenerated catalogs stay
  symmetric (test_i18n green).
- Tests: 3 new in `tests/unit/test_config.py` (fields stay removed; a legacy
  settings.json carrying the removed keys still loads value-for-value). Verified
  non-vacuous by re-adding `dashboard_preset` (test fails).
- Full gate: ruff + mypy clean; suite 330 passed.

## Phase 11 — release-integrity: version consistency guarded

- The V2.0.0 audit listed "displayed version == package metadata" as a hand
  check; nothing enforced it. Added `tests/unit/test_version_consistency.py`
  pinning `APP_VERSION` against `pyproject.toml`, the PyInstaller spec, the Inno
  Setup default, the generated VERSIONINFO (driven through the real generator),
  the README download links and the release-notes checksum example.
- First CI attempt failed on Windows: the test read the *generated* (gitignored)
  `installer/version_info.txt`, absent on a clean checkout. Fixed to call
  `scripts/build/build.py::_write_version_info` and restore any prior artefact.
- Verified non-vacuous: changing only `pyproject.toml`'s version fails it.

## Phase 5 — Windows delivery revalidation (asset integrity)

- The CI run for the GUI commit (`2419ae4`) built the full Windows pipeline
  green and uploaded a 129 MB `smartcooling-windows` artefact; downloaded and
  inspected it: `SmartCoolingController-2.0.0-setup.exe` (MZ/PE), portable zip,
  source zip, `installer-manifest.json`, `checksums.sha256`.
- `checksums.sha256` now lists the installer with a digest matching the actual
  file — DEFECT-1 (shipped in v2.0.0) is closed and CI-guarded.
- `releases/latest/download/<asset>` resolves 302 → the v2.0.0 release for all
  three assets plus `checksums.sha256` (link validity confirmed).
- Executing the installer on Windows remains BLOCKED in this Linux container;
  the asset is verifiable (hash/size/magic) but not runnable here.
