"""Database access for the dashboard. Every query is cached for a few seconds and read-only."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from ispmon import alerts as alert_engine
from ispmon import db
from ispmon.config import TS_FMT, Settings, load_settings

RANGES = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}
CRITICAL_INTERFACES_KEY = "selected_interfaces"  # same key the first prototype used


@st.cache_resource
def settings() -> Settings:
    return load_settings()


@st.cache_resource
def _ensure_schema() -> bool:
    conn = db.connect(settings().db_path)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    return True


def _connect() -> sqlite3.Connection:
    _ensure_schema()
    conn = sqlite3.connect(settings().db_path, timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn


@st.cache_data(ttl=15, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def since(hours: float) -> str:
    return (datetime.now() - timedelta(hours=hours)).strftime(TS_FMT)


def bucket_minutes(hours: float) -> int:
    """Downsample long ranges so charts stay fast and readable."""
    if hours <= 6:
        return 1
    if hours <= 24:
        return 5
    if hours <= 168:
        return 30
    return 120


# -- settings stored in the database ---------------------------------------------------------
def load_critical_interfaces() -> list[str]:
    df = query("SELECT setting_value FROM user_settings WHERE setting_key = ?", (CRITICAL_INTERFACES_KEY,))
    if df.empty:
        return []
    try:
        return list(json.loads(df["setting_value"].iloc[0]))
    except (ValueError, TypeError):
        return []


def save_critical_interfaces(names: list[str]) -> None:
    conn = db.connect(settings().db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO user_settings (setting_key, setting_value) VALUES (?, ?)",
                     (CRITICAL_INTERFACES_KEY, json.dumps(names)))
        conn.commit()
    finally:
        conn.close()
    st.cache_data.clear()


# -- optical / ONU -----------------------------------------------------------------------------
def latest_heartbeat() -> pd.DataFrame:
    return query("SELECT timestamp, mikrotik_ok, olts_ok, onus_logged, cycle_seconds "
                 "FROM collector_heartbeat ORDER BY timestamp DESC LIMIT 1")


def _recent_window(table: str) -> str:
    newest = query(f"SELECT MAX(timestamp) AS ts FROM {table}")["ts"].iloc[0]
    if not newest:
        return ""
    return (datetime.strptime(newest, TS_FMT) - timedelta(minutes=15)).strftime(TS_FMT)


def latest_optics() -> pd.DataFrame:
    start = _recent_window("olt_optical_stats")
    if not start:
        return pd.DataFrame()
    df = query("SELECT timestamp, olt_ip, onu_id, rx_power, tx_power, temperature, voltage, bias_current "
               "FROM olt_optical_stats WHERE timestamp >= ?", (start,))
    return df.sort_values("timestamp").groupby(["olt_ip", "onu_id"]).tail(1).reset_index(drop=True)


def latest_status() -> pd.DataFrame:
    start = _recent_window("onu_status")
    if not start:
        return pd.DataFrame()
    df = query("SELECT timestamp, olt_ip, onu_id, status, mac, distance_m, rtt_tq, alive_seconds, "
               "last_reg_time, last_dereg_time, last_dereg_reason FROM onu_status WHERE timestamp >= ?", (start,))
    return df.sort_values("timestamp").groupby(["olt_ip", "onu_id"]).tail(1).reset_index(drop=True)


_OPTICS_COLS = ("AVG(rx_power) AS rx_power, AVG(tx_power) AS tx_power, AVG(temperature) AS temperature, "
                "AVG(voltage) AS voltage, AVG(bias_current) AS bias_current")


def optics_trend(hours: float, bucket: int | None = None) -> pd.DataFrame:
    """All ONUs, bucketed. Used for sparklines and 24-hour change."""
    step = (bucket or bucket_minutes(hours)) * 60
    df = query(
        f"SELECT MIN(timestamp) AS timestamp, olt_ip, onu_id, {_OPTICS_COLS} FROM olt_optical_stats "
        "WHERE timestamp >= ? GROUP BY olt_ip, onu_id, CAST(strftime('%s', timestamp) AS INTEGER) / ? "
        "ORDER BY timestamp", (since(hours), step))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def onu_history(olt_ip: str, onu_id: str, hours: float) -> pd.DataFrame:
    step = bucket_minutes(hours) * 60
    df = query(
        f"SELECT MIN(timestamp) AS timestamp, {_OPTICS_COLS}, MIN(rx_power) AS rx_min, MAX(rx_power) AS rx_max "
        "FROM olt_optical_stats WHERE olt_ip = ? AND onu_id = ? AND timestamp >= ? "
        "GROUP BY CAST(strftime('%s', timestamp) AS INTEGER) / ? ORDER BY timestamp",
        (olt_ip, onu_id, since(hours), step))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def online_counts(hours: float) -> pd.DataFrame:
    """Online ONUs per OLT over time. Buckets keep the minimum so short outages stay visible."""
    step = bucket_minutes(hours) * 60
    df = query(
        "SELECT MIN(timestamp) AS timestamp, olt_ip, MIN(online) AS online, MAX(total) AS total FROM ("
        "  SELECT timestamp, olt_ip, SUM(status = 'online') AS online, COUNT(*) AS total FROM onu_status "
        "  WHERE timestamp >= ? GROUP BY timestamp, olt_ip) "
        "GROUP BY olt_ip, CAST(strftime('%s', timestamp) AS INTEGER) / ? ORDER BY timestamp",
        (since(hours), step))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def events(hours: float) -> pd.DataFrame:
    df = query("SELECT timestamp, olt_ip, onu_id, event, dereg_reason, after_gap FROM onu_events "
               "WHERE timestamp >= ? ORDER BY timestamp DESC", (since(hours),))
    df["after_gap"] = df["after_gap"].fillna(0).astype(int)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def optics_window(hours: float) -> pd.DataFrame:
    return query("SELECT olt_ip, onu_id, rx_power FROM olt_optical_stats WHERE timestamp >= ?", (since(hours),))


# -- router --------------------------------------------------------------------------------------
def interface_catalog() -> pd.DataFrame:
    df = query("SELECT interface_name, interface_comment, MAX(timestamp) AS last_seen FROM interface_stats "
               "WHERE timestamp >= ? GROUP BY interface_name, interface_comment", (since(168),))
    latest = query("SELECT interface_name, interface_comment, running, timestamp FROM interface_stats "
                   "WHERE timestamp >= ?", (since(0.25),))
    if not latest.empty:
        latest = latest.sort_values("timestamp").groupby(["interface_name", "interface_comment"]).tail(1)
        df = df.merge(latest[["interface_name", "interface_comment", "running"]],
                      on=["interface_name", "interface_comment"], how="left")
    else:
        df["running"] = float("nan")
    return df


def traffic_raw(hours: float, interface_names: tuple[str, ...]) -> pd.DataFrame:
    if not interface_names:
        return pd.DataFrame()
    marks = ",".join("?" * len(interface_names))
    return query("SELECT timestamp, interface_name, interface_comment, running, rx_byte, tx_byte FROM interface_stats "
                 f"WHERE timestamp >= ? AND interface_name IN ({marks}) ORDER BY timestamp",
                 (since(hours), *interface_names))


def router_health(hours: float) -> pd.DataFrame:
    step = bucket_minutes(hours) * 60
    df = query("SELECT MIN(timestamp) AS timestamp, AVG(cpu_load) AS cpu_load, AVG(free_memory_mb) AS free_memory_mb "
               "FROM system_health WHERE timestamp >= ? GROUP BY CAST(strftime('%s', timestamp) AS INTEGER) / ? "
               "ORDER BY timestamp", (since(hours), step))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def security_logs(hours: float, limit: int = 2000) -> pd.DataFrame:
    day = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d")
    return query("SELECT ingest_date, router_time, topics, message FROM security_logs "
                 "WHERE ingest_date >= ? ORDER BY rowid DESC LIMIT ?", (day, limit))


# -- collector -----------------------------------------------------------------------------------
def heartbeats(hours: float) -> pd.DataFrame:
    df = query("SELECT timestamp, mikrotik_ok, olts_ok, onus_logged, cycle_seconds FROM collector_heartbeat "
               "WHERE timestamp >= ? ORDER BY timestamp", (since(hours),))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def device_heartbeats(hours: float) -> pd.DataFrame:
    df = query("SELECT timestamp, device, ok, items, duration_s, error FROM device_heartbeat "
               "WHERE timestamp >= ? ORDER BY timestamp", (since(hours),))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def table_sizes() -> pd.DataFrame:
    names = ["olt_optical_stats", "onu_status", "onu_events", "interface_stats", "system_health",
             "security_logs", "collector_heartbeat", "device_heartbeat"]
    sql = " UNION ALL ".join(f"SELECT '{n}' AS table_name, COALESCE(MAX(rowid), 0) AS rows FROM {n}" for n in names)
    return query(sql)


def get_alerts(interfaces: tuple[str, ...]) -> list:
    return _cached_alerts(interfaces)


@st.cache_data(ttl=15, show_spinner=False)
def _cached_alerts(interfaces: tuple[str, ...]) -> list:
    conn = _connect()
    try:
        return alert_engine.evaluate(conn, settings().thresholds, list(interfaces))
    finally:
        conn.close()
