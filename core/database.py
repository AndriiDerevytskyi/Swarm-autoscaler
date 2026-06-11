import os
import sqlite3
import threading
from typing import List

from core.logging import log

_DB_DIR  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DB_PATH = os.path.join(_DB_DIR, "autoscaler.db")

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(_DB_DIR, exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def run_migrations() -> None:
    conn = _get_conn()
    with _lock:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        applied = {r["version"] for r in rows}

        for fname in sorted(os.listdir(_MIGRATIONS_DIR)):
            if not fname.endswith(".sql"):
                continue
            version = fname.replace(".sql", "")
            if version in applied:
                continue

            path = os.path.join(_MIGRATIONS_DIR, fname)
            with open(path) as f:
                sql = f.read()

            conn.executescript(sql)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.commit()
            log.info("  Migration applied: %s", version)


# ── Events ──────────────────────────────────────────────────────────────────

def record_event(
    service_name: str,
    action: str,
    from_replicas: int,
    to_replicas: int,
    reason: str = "",
    cpu_pct: float = 0.0,
    mem_pct: float = 0.0,
) -> None:
    conn = _get_conn()
    with _lock:
        conn.execute(
            """INSERT INTO events (service_name, action, from_replicas, to_replicas, reason, cpu_pct, mem_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (service_name, action, from_replicas, to_replicas, reason, cpu_pct, mem_pct),
        )
        conn.commit()


def get_events(limit: int = 50, service_name: str = "") -> List[dict]:
    conn = _get_conn()
    with _lock:
        if service_name:
            rows = conn.execute(
                "SELECT * FROM events WHERE service_name = ? ORDER BY id DESC LIMIT ?",
                (service_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def clear_events(service_name: str = "") -> int:
    conn = _get_conn()
    with _lock:
        if service_name:
            cur = conn.execute("DELETE FROM events WHERE service_name = ?", (service_name,))
        else:
            cur = conn.execute("DELETE FROM events")
        conn.commit()
        return cur.rowcount


# ── Replica history ─────────────────────────────────────────────────────────

def record_replica_snapshot(service_name: str, replicas: int) -> None:
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO replica_history (service_name, replicas) VALUES (?, ?)",
            (service_name, replicas),
        )
        conn.execute(
            "DELETE FROM replica_history WHERE timestamp < datetime('now', '-24 hours')"
        )
        conn.commit()


def get_replica_history(service_name: str, minutes: int = 60) -> List[dict]:
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            """SELECT timestamp, replicas FROM replica_history
               WHERE service_name = ? AND timestamp >= datetime('now', ?)
               ORDER BY timestamp ASC""",
            (service_name, f"-{minutes} minutes"),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Pause / Resume ──────────────────────────────────────────────────────────

def is_paused(service_name: str) -> bool:
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT 1 FROM paused_services WHERE service_name = ?", (service_name,)
        ).fetchone()
    return row is not None


def pause_service(service_name: str, duration_minutes: int = 0) -> None:
    conn = _get_conn()
    with _lock:
        if duration_minutes > 0:
            conn.execute(
                """INSERT OR REPLACE INTO paused_services (service_name, paused_at, resume_after)
                   VALUES (?, datetime('now'), datetime('now', ?))""",
                (service_name, f"+{duration_minutes} minutes"),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO paused_services (service_name, paused_at, resume_after)
                   VALUES (?, datetime('now'), NULL)""",
                (service_name,),
            )
        conn.commit()


def resume_service(service_name: str) -> None:
    conn = _get_conn()
    with _lock:
        conn.execute(
            "DELETE FROM paused_services WHERE service_name = ?", (service_name,)
        )
        conn.commit()


def get_paused_services() -> List[str]:
    conn = _get_conn()
    with _lock:
        rows = conn.execute("SELECT service_name FROM paused_services").fetchall()
    return [r["service_name"] for r in rows]


def expire_paused() -> List[str]:
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            """DELETE FROM paused_services
               WHERE resume_after IS NOT NULL AND resume_after <= datetime('now')
               RETURNING service_name"""
        ).fetchall()
        if rows:
            conn.commit()
    return [r["service_name"] for r in rows]


# ── Node metrics (agent reports) ────────────────────────────────────────────

def record_node_metrics(node: str, service_name: str, cpu_pct: float, mem_pct: float, replicas: int) -> None:
    conn = _get_conn()
    with _lock:
        conn.execute(
            """INSERT INTO node_metrics (node, service_name, cpu_pct, mem_pct, replicas)
               VALUES (?, ?, ?, ?, ?)""",
            (node, service_name, cpu_pct, mem_pct, replicas),
        )
        conn.execute(
            "DELETE FROM node_metrics WHERE timestamp < datetime('now', '-2 minutes')"
        )
        conn.commit()


def get_node_metrics(service_name: str) -> List[dict]:
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            """SELECT node, cpu_pct, mem_pct, replicas FROM node_metrics
               WHERE service_name = ? AND timestamp >= datetime('now', '-2 minutes')
               ORDER BY timestamp DESC""",
            (service_name,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Meta key-value store ────────────────────────────────────────────────────

def meta_get(key: str, default: str = "") -> str:
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def meta_set(key: str, value: str) -> None:
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        conn.commit()
