# V2 Engineering Plan — SmartCooling Controller

Durable task ledger. Statuses: **TODO / IN PROGRESS / BLOCKED / PASS / FAIL**.
Branch: `v2/connection-reliability`. Baseline: v1.0.0 (`cb5cd92`).

This file is the source of truth for *what was done and what was proven*. It is
updated as work lands; see `docs/V2_DECISIONS.md` for the rationale behind each
non-obvious choice.

---

## Phase 0 — Repository forensics (baseline)

### Baseline quality gates (before any change)

| Gate | Result |
| --- | --- |
| `pytest` | 158 passed |
| `ruff check app run.py tests scripts` | clean |
| `mypy app` | success, 93 files |

### Architecture map (verified by reading the source, not the docs)

- Entry point: `run.py::main` → `_configure_qt` → `QApplication` → single
  instance → crash guard → wizard/safe-mode → `services.start()` → `create_window`
  → `app.exec()`.
- ESP32 data path:
  `ConnectionManager` (dedicated asyncio thread) → `Transport`
  (`WebSocketTransport` / `TcpTransport`) → `decode_packet`/`validate_status`
  → `EventBus` topics `esp32.state` / `esp32.status` / `esp32.device`
  → `QtEventBridge` (marshal to GUI thread) → `MainWindow._on_connection` /
  `Esp32Page` / `DiagramPage`/`Schematic` → `ConnectionManager.latest_metrics`
  merged by `TelemetryEngine` → `Persistence`.
- Connection visualisation: `app/ui/widgets/schematic.py::Schematic` — a
  `QTimer`(33 ms) driving travelling particles; `DiagramPage` feeds it via
  `set_link_state(state, fan_state, rpm)`.
- Transports: WebSocket (RFC6455 via the `websockets` package) and a
  length-prefixed TCP frame stream. **There is no Serial transport** despite the
  mission's "where supported" wording — this is documented as a limitation.
- Discovery: `UdpDiscovery` broadcast + `manual_device`; `manual_host`/`port`
  config exists but the manager never uses it directly (services compose the
  `Esp32Device`).

### Defects and risks identified (root-cause hypotheses)

| # | Area | Finding | Severity |
| --- | --- | --- | --- |
| D1 | Heartbeat | `_heartbeat_loop` sends on a fixed `heartbeat_interval_s` and **never checks `heartbeat_timeout_s`**; no missed-heartbeat detection or disconnect | High |
| D2 | Latency | `latency_ms` is measured as time-to-*any* next packet, not to the heartbeat's response; `_pending_heartbeat_at` is overwritten every interval and never cleared on timeout | High |
| D3 | Backoff | `_run` resets `attempt=0` after **every** session end, so a device that accepts TCP, then immediately drops, retries at a constant 1 s — a tight reconnect loop; the schedule is a constant list, no jitter | High |
| D4 | Sessions | No session **generation id**. Stale state from an old session can overwrite a new one; `_set_state(CONNECTED)` is published on every packet, so transitions are noisy and not ordered | High |
| D5 | Teardown race | `disconnect()` sets `_desired=None` and only fires `_disconnect_event` **if it already exists**; during CONNECTING (`_disconnect_event is None`) the connect cannot be cancelled | High |
| D6 | Idempotency | `stop()` joins the thread but the `_run` loop can spin up a *new* session between `_stop.set()` and the join; `_session` publishes `DISCONNECTED` at the end which can overwrite STOPPING | Medium |
| D7 | Discovery | UDP broadcast is **entirely unwired** into the service container (no caller); `last_host` is written but never read back | Medium |
| D8 | Diagnostics | No monotonic instrumentation: no discovery/connect/handshake duration, no time-to-first-telemetry, no consecutive-failure count, no last-error category. `RSSI` row is mislabelled as `common.voltage` | Medium |
| D9 | Protocol | `decode_packet` rejects an unknown `type`; unknown/forward-compatible message types and versions are not degraded gracefully. No command acknowledgement correlation | Low |
| D10 | Chart/animation | `Schematic.stop_animation`/`Schematic.teardown` are never called from `DiagramPage`, so the 33 ms timer keeps firing after the page is torn down (language switch rebuild). Particles always animate while "connected", even when telemetry is stale | Medium |
| D11 | UI truth | `Schematic` treats only `CONNECTED` as live; `DEGRADED`/`STALE` show a live link with flowing packets, misrepresenting a link that is not delivering data | Medium |
| D12 | Wifi node | `DiagramPage._node_values("wifi")` hard-codes `status: "online"` — a fabricated value | Medium |
| D13 | Test coverage | No deterministic fault-injection harness: no slow/dropped/malformed/stale/disconnect-while-connecting coverage; no leak/stress test | High |

---

## Phase 1 — ESP32 connection reliability

- [PASS] 1A Connection lifecycle state machine
  - Authoritative `_set_state` with a session **generation guard**; states
    DISCONNECTED/DISCOVERING/CONNECTING/CONNECTED/DEGRADED/STALE/RECONNECTING/STOPPING.
  - Idempotent `disconnect`/`stop`; cancellable connect; STOPPING is terminal.
- [PASS] 1B Discovery: cached endpoint first, manual validation, bounded timeouts.
- [PASS] 1C Transports: heartbeat timeout, serialized writes, bounded reads.
  - Send lock on both transports (websockets forbids concurrent sends);
    `close()` swaps the handle then closes under the lock.
- [PASS] 1D Protocol: graceful unknown type/version, correlation ids.
  - A protocol-version mismatch or auth failure aborts the session, settles in
    PROTOCOL_MISMATCH/UNAUTHORIZED and stops retrying instead of spinning.
- [PASS] 1E Diagnostics: monotonic timestamps + structured diagnostics service.
  - Counters for discovery/connect/handshake/time-to-first-telemetry,
    consecutive failures, malformed packets, heartbeat timeouts, RTT.

## Phase 2 — Connection chart & animation

- [PASS] Timers stopped/restarted correctly on teardown and page rebuild.
- [PASS] Animation reflects real state (CONNECTED moves; DEGRADED slower; STALE
  dimmed/frozen; DISCONNECTED idle); no packets implied when there are none.
- [PASS] Wi-Fi node reports real status/RSSI/latency/loss; no fabricated "online".

## Phase 3 — Automated network/hardware test harness

- [PASS] Deterministic fake ESP32 with fault injection (slow, drop, malformed,
  bad version, stale, restart, disconnect-while-connecting).
- [PASS] Leak/stress: repeated connect/disconnect cycles, no leaked tasks,
  no duplicate callbacks, bounded buffers.

## Phase 4 — V2 features

- [TODO] Connection Center, diagnostics action, cooling, history/charts,
  hardware status, alerts/i18n.

## Phase 5 — Performance, security, recovery

- [TODO] Main-thread blocking, leaks, SQLite, redaction, shutdown, packaging.

## Phase 6 — Quality gates & release

- [TODO] Full gates + README/version/changelog.

## Hardware tests pending (need a physical ESP32)

- Real Wi-Fi RSSI, real fan RPM/INA219/DS18B20 values.
- Real disconnect/reconnect on AP drop.
- Discovery against a real firmware broadcast responder.
