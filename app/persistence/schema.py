"""SQLite schema and forward-only migrations (spec §55–§58, §108–§110).

The database is the application's durable memory.  It must survive a power cut,
a full disk and a corrupted file without losing the user's configuration.

* ``SCHEMA_VERSION`` is stored in ``PRAGMA user_version``.
* Migrations are an ordered list of ``(version, sql)`` steps.  Each step is
  applied inside a transaction, so a failed migration rolls back completely and
  the previous schema stays valid (spec §58).
* Adding a column or table in a later release means appending a new step — old
  databases are upgraded in place, never recreated.
"""

from __future__ import annotations

#: Bump when a new migration step is appended.
SCHEMA_VERSION = 1

#: Applied to brand-new databases.  Keep statements idempotent where possible.
_INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,
    seq             INTEGER NOT NULL,
    cpu_temp        REAL,
    gpu_temp        REAL,
    cpu_usage       REAL,
    gpu_usage       REAL,
    ram_usage       REAL,
    cpu_clock_mhz   REAL,
    fan_rpm         REAL,
    fan_pwm         REAL,
    fan_target      REAL,
    fan_power_w     REAL,
    pad_temp        REAL,
    thermal_state   TEXT,
    validity        TEXT,
    simulated       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_simulated ON snapshots(simulated, ts);

CREATE TABLE IF NOT EXISTS sessions (
    session_id              TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    state                   TEXT NOT NULL,
    started_at              REAL NOT NULL,
    ended_at                REAL,
    profile_key             TEXT,
    simulated               INTEGER NOT NULL DEFAULT 0,
    max_cpu_c               REAL,
    max_gpu_c               REAL,
    avg_cpu_c               REAL,
    avg_gpu_c               REAL,
    peak_rpm                REAL,
    avg_rpm                 REAL,
    max_fan_power_w         REAL,
    time_above_threshold_s  REAL,
    esp32_connected         INTEGER NOT NULL DEFAULT 0,
    alert_count             INTEGER NOT NULL DEFAULT 0,
    sample_count            INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT NOT NULL DEFAULT '',
    provenance              TEXT NOT NULL DEFAULT 'measured'
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);

CREATE TABLE IF NOT EXISTS session_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    cpu_temp    REAL,
    gpu_temp    REAL,
    cpu_usage   REAL,
    gpu_usage   REAL,
    fan_rpm     REAL,
    fan_pwm     REAL,
    pad_temp    REAL
);
CREATE INDEX IF NOT EXISTS idx_session_samples ON session_samples(session_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    dedup_key   TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    value       REAL,
    threshold   REAL,
    unit        TEXT NOT NULL DEFAULT '',
    event_type  TEXT NOT NULL DEFAULT 'raised',
    acknowledged INTEGER NOT NULL DEFAULT 0,
    dismissed   INTEGER NOT NULL DEFAULT 0,
    provenance  TEXT NOT NULL DEFAULT 'derived'
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_dedup ON alerts(dedup_key, ts DESC);

CREATE TABLE IF NOT EXISTS profiles (
    key         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    builtin     INTEGER NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id           TEXT PRIMARY KEY,
    nickname            TEXT NOT NULL DEFAULT '',
    host                TEXT NOT NULL DEFAULT '',
    port                INTEGER NOT NULL DEFAULT 0,
    firmware_version    TEXT NOT NULL DEFAULT '',
    protocol_version    INTEGER NOT NULL DEFAULT 0,
    last_seen           REAL,
    trusted             INTEGER NOT NULL DEFAULT 0,
    is_mock             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS baselines (
    key             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    avg_cpu_c       REAL,
    avg_gpu_c       REAL,
    avg_fan_rpm     REAL,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cooling_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    before_c        REAL,
    after_c         REAL,
    delta_c         REAL,
    duty_before     REAL,
    duty_after      REAL,
    stabilization_s REAL,
    provenance      TEXT NOT NULL DEFAULT 'estimated'
);
CREATE INDEX IF NOT EXISTS idx_responses_started ON cooling_responses(started_at DESC);

CREATE TABLE IF NOT EXISTS exports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    format      TEXT NOT NULL,
    path        TEXT NOT NULL,
    row_count   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'ok',
    message     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    level       TEXT NOT NULL,
    component   TEXT NOT NULL,
    message     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnostics_ts ON diagnostics(ts DESC);
"""

#: Ordered forward-only migrations.  ``(target_version, sql)``.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _INITIAL_SCHEMA),
]


def migrations_from(current_version: int) -> list[tuple[int, str]]:
    """Return the migration steps needed to reach :data:`SCHEMA_VERSION`."""
    return [(version, sql) for version, sql in MIGRATIONS if version > current_version]


__all__ = ["SCHEMA_VERSION", "MIGRATIONS", "migrations_from"]
