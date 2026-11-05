import sqlite3

from ispmon import db


def legacy_database(path):
    """Schema as created by the very first prototype loggers."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE interface_stats (timestamp DATETIME, interface_name TEXT, interface_comment TEXT,
            rx_byte INTEGER, tx_byte INTEGER, rx_drop INTEGER, tx_drop INTEGER);
        CREATE TABLE security_logs (router_time TEXT, topics TEXT, message TEXT, UNIQUE(router_time, topics, message));
        CREATE TABLE olt_optical_stats (timestamp DATETIME, olt_ip TEXT, onu_id TEXT, temperature REAL,
            tx_power REAL, rx_power REAL);
        CREATE TABLE onu_events (timestamp DATETIME, olt_ip TEXT, onu_id TEXT, event TEXT, dereg_reason TEXT);
        INSERT INTO interface_stats VALUES ('2026-10-01 10:00:00','ether1','uplink',1,2,0,0);
        INSERT INTO security_logs VALUES ('10:00:01','system,info','login failure for user x from 1.2.3.4');
        INSERT INTO olt_optical_stats VALUES ('2026-10-01 10:00:00','10.0.0.1','EPON0/1:1',40.0,2.1,-20.5);
        INSERT INTO onu_events VALUES ('2026-10-01 10:05:00','10.0.0.1','EPON0/1:1','went_offline','Wire Down');
        """
    )
    conn.commit()
    return conn


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_legacy_database_is_upgraded_without_losing_rows(tmp_path):
    conn = legacy_database(tmp_path / "old.db")
    db.migrate(conn)
    assert {"running"} <= set(columns(conn, "interface_stats"))
    assert {"voltage", "bias_current"} <= set(columns(conn, "olt_optical_stats"))
    assert "after_gap" in columns(conn, "onu_events")
    assert "ingest_date" in columns(conn, "security_logs")
    assert conn.execute("SELECT COUNT(*) FROM interface_stats").fetchone()[0] == 1
    assert conn.execute("SELECT rx_power FROM olt_optical_stats").fetchone()[0] == -20.5
    assert conn.execute("SELECT after_gap FROM onu_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM security_logs").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migration_is_idempotent_and_named_inserts_work_on_upgraded_tables(tmp_path):
    conn = legacy_database(tmp_path / "old.db")
    db.migrate(conn)
    db.migrate(conn)
    conn.execute(
        "INSERT INTO olt_optical_stats (timestamp, olt_ip, onu_id, temperature, voltage, bias_current, tx_power, rx_power)"
        " VALUES ('2026-10-01 10:01:00','10.0.0.1','EPON0/1:1',41,3.3,12,2.2,-20.4)"
    )
    row = conn.execute("SELECT voltage, tx_power, rx_power FROM olt_optical_stats WHERE timestamp LIKE '%10:01%'").fetchone()
    assert row == (3.3, 2.2, -20.4)


def test_fresh_database_and_prune(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    db.migrate(conn)
    conn.execute("INSERT INTO interface_stats (timestamp, interface_name) VALUES ('2020-01-01 00:00:00','old')")
    conn.execute("INSERT INTO interface_stats (timestamp, interface_name) VALUES ('2999-01-01 00:00:00','new')")
    conn.execute("INSERT INTO olt_optical_stats (timestamp, olt_ip, onu_id) VALUES ('2020-01-01 00:00:00','o','u')")
    conn.commit()
    removed = db.prune(conn, interface_days=30, log_days=30)
    assert removed["interface_stats"] == 1
    assert conn.execute("SELECT COUNT(*) FROM olt_optical_stats").fetchone()[0] == 1  # training data is kept
