CREATE TABLE IF NOT EXISTS node_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node         TEXT    NOT NULL,
    service_name TEXT    NOT NULL,
    timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
    cpu_pct      REAL    NOT NULL,
    mem_pct      REAL    NOT NULL,
    replicas     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_node_metrics_svc_time
    ON node_metrics (service_name, timestamp);
