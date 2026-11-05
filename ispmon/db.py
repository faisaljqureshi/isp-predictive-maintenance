"""SQLite access: connection settings, idempotent schema migration, retention.

INSERTs elsewhere always name their columns, because databases upgraded with ALTER TABLE
can have a different physical column order than a freshly created one.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 3

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS system_health (
    timestamp DATETIME, cpu_load INTEGER, free_memory_mb REAL);

CREATE TABLE IF NOT EXISTS interface_stats (
    timestamp DATETIME, interface_name TEXT, interface_comment TEXT,
    running INTEGER DEFAULT 1, rx_byte INTEGER, tx_byte INTEGER,
    rx_drop INTEGER, tx_drop INTEGER);

CREATE TABLE IF NOT EXISTS security_logs (
    ingest_date TEXT, router_time TEXT, topics TEXT, message TEXT,
    UNIQUE(ingest_date, router_time, topics, message));

CREATE TABLE IF NOT EXISTS olt_optical_stats (
    timestamp DATETIME, olt_ip TEXT, onu_id TEXT, temperature REAL,
    voltage REAL, bias_current REAL, tx_power REAL, rx_power REAL);

CREATE TABLE IF NOT EXISTS onu_status (
    timestamp DATETIME, olt_ip TEXT, onu_id TEXT, status TEXT, mac TEXT,
    distance_m INTEGER, rtt_tq INTEGER, last_reg_time TEXT, last_dereg_time TEXT,
    last_dereg_reason TEXT, alive_seconds INTEGER);

CREATE TABLE IF NOT EXISTS onu_events (
    timestamp DATETIME, olt_ip TEXT, onu_id TEXT, event TEXT, dereg_reason TEXT,
    after_gap INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS collector_heartbeat (
    timestamp DATETIME, mikrotik_ok INTEGER, olts_ok INTEGER,
    onus_logged INTEGER, cycle_seconds REAL);

CREATE TABLE IF NOT EXISTS device_heartbeat (
    timestamp DATETIME, device TEXT, ok INTEGER, items INTEGER,
    duration_s REAL, error TEXT);

CREATE TABLE IF NOT EXISTS user_settings (setting_key TEXT UNIQUE, setting_value TEXT);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_optics_onu ON olt_optical_stats(olt_ip, onu_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_optics_ts ON olt_optical_stats(timestamp);
CREATE INDEX IF NOT EXISTS idx_status_onu ON onu_status(olt_ip, onu_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_status_ts ON onu_status(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_ts ON onu_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_iface_name ON interface_stats(interface_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_iface_ts ON interface_stats(timestamp);
CREATE INDEX IF NOT EXISTS idx_health_ts ON system_health(timestamp);
CREATE INDEX IF NOT EXISTS idx_hb_ts ON collector_heartbeat(timestamp);
CREATE INDEX IF NOT EXISTS idx_devhb_ts ON device_heartbeat(timestamp);
"""

# Columns added after the first prototype; added in place to older databases.
_REQUIRED_COLUMNS = {
    "interface_stats": {"running": "INTEGER DEFAULT 1"},
    "olt_optical_stats": {"voltage": "REAL", "bias_current": "REAL"},
    "onu_events": {"after_gap": "INTEGER DEFAULT 0"},
}


def connect(path: Path, timeout: float = 30.0) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _upgrade_legacy_security_logs(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "security_logs")
    if columns and "ingest_date" not in columns:
        conn.executescript(
            """
            ALTER TABLE security_logs RENAME TO security_logs_legacy;
            CREATE TABLE security_logs (
                ingest_date TEXT, router_time TEXT, topics TEXT, message TEXT,
                UNIQUE(ingest_date, router_time, topics, message));
            INSERT OR IGNORE INTO security_logs
                SELECT '1970-01-01', router_time, topics, message FROM security_logs_legacy;
            DROP TABLE security_logs_legacy;
            """
        )


def migrate(conn: sqlite3.Connection) -> None:
    """Bring any earlier database up to the current schema. Safe to run repeatedly."""
    _upgrade_legacy_security_logs(conn)
    conn.executescript(TABLES_SQL)
    for table, columns in _REQUIRED_COLUMNS.items():
        existing = _columns(conn, table)
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.executescript(INDEXES_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def prune(conn: sqlite3.Connection, interface_days: int, log_days: int, now: datetime | None = None) -> dict[str, int]:
    """Delete bulky, low-value history. Optical and ONU data are kept: they are the training data."""
    now = now or datetime.now()
    removed: dict[str, int] = {}
    if interface_days > 0:
        cutoff = (now - timedelta(days=interface_days)).strftime("%Y-%m-%d %H:%M:%S")
        removed["interface_stats"] = conn.execute("DELETE FROM interface_stats WHERE timestamp < ?", (cutoff,)).rowcount
    if log_days > 0:
        cutoff_day = (now - timedelta(days=log_days)).strftime("%Y-%m-%d")
        removed["security_logs"] = conn.execute("DELETE FROM security_logs WHERE ingest_date < ?", (cutoff_day,)).rowcount
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    removed["device_heartbeat"] = conn.execute("DELETE FROM device_heartbeat WHERE timestamp < ?", (cutoff,)).rowcount
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return removed
