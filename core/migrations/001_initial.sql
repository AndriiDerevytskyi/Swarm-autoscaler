CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
    service_name TEXT   NOT NULL,
    action      TEXT    NOT NULL,
    from_replicas INTEGER NOT NULL,
    to_replicas   INTEGER NOT NULL,
    reason      TEXT,
    cpu_pct     REAL,
    mem_pct     REAL
);

CREATE TABLE IF NOT EXISTS replica_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
    service_name TEXT   NOT NULL,
    replicas    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_replica_history_service
    ON replica_history (service_name, timestamp);

CREATE TABLE IF NOT EXISTS paused_services (
    service_name TEXT PRIMARY KEY,
    paused_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_version (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
