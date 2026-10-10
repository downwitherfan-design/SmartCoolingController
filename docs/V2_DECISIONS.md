# V2 Decisions — SmartCooling Controller

Why each non-obvious V2 choice was made. Append-only.

## D-001 — Keep the existing architecture, harden it

The connection manager, transports, protocol and UI already have the right seams
(async session on a dedicated thread, a transport ABC, an event bus with a single
Qt marshalling point). Replacing them would be a rewrite with no reliability
payoff and would break the frozen packaging assumptions. V2 therefore *hardens*
these components and adds missing lifecycle guarantees rather than introducing a
new framework. The only additions are a diagnostics record on the existing
manager and a discovery wiring that reuses the existing `UdpDiscovery`.

## D-002 — No Serial transport (mission says "where supported")

The protocol module supports WebSocket and length-prefixed TCP only; there is no
pyserial dependency, no serial code, and the firmware contract (per the repo)
speaks WebSocket. Adding a serial transport would invent a firmware contract we
cannot verify. Serial is recorded as **not supported** in the engineering plan
rather than implemented speculatively.

## D-003 — Session generation guard instead of locks around state

The manager runs one session coroutine at a time, but cancellation is
cooperative: a cancelled coroutine can still run its `finally` and publish a
final state *after* a newer session has begun. A monotonically increasing
`_generation` captured by each session and checked before every publish makes a
stale session's writes no-ops. This is simpler and more robust than trying to
serialise all state writes under the RLock, and it directly satisfies "stale
callbacks from an old session must not overwrite the current session".

## D-004 — Heartbeat RTT only counts real heartbeat responses

v1 set `latency_ms` on *any* inbound packet, which measures inter-packet spacing,
not round-trip time. V2 keeps a small queue of outstanding heartbeat send times
and, when a packet arrives that is a direct response to the oldest outstanding
heartbeat, records `now - sent`. If a heartbeat is unanswered past
`heartbeat_timeout_s`, the link is declared DEGRADED and a short grace period
later STALE, then the session is torn down for a bounded reconnect. This gives an
*actionable* error category ("heartbeat timeout") instead of a silent freeze.

## D-005 — Backoff resets only after a genuinely healthy session

v1 reset the backoff attempt counter after every session, including a session
that connected and dropped in milliseconds. V2 resets the counter only when the
session stayed healthy (received valid telemetry) for at least
`healthy_reset_s` (default 5 s). Short-lived flapping therefore escalates the
backoff instead of spinning at 1 s.
