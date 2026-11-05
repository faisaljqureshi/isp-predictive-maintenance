"""Alert rules. Pure logic over the database: the dashboard shows the result, a notifier can push it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from ispmon import analytics
from ispmon.config import TS_FMT, Thresholds

LEVELS = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Alert:
    level: str   # critical | warning | info
    source: str  # collector | router | optical | onu | interface
    title: str
    detail: str


def fmt_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def _q(conn, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def _cut(now: datetime, **delta) -> str:
    return (now - timedelta(**delta)).strftime(TS_FMT)


def evaluate(conn, th: Thresholds, critical_interfaces: list[str], now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now()
    alerts: list[Alert] = []

    # Collector health first: everything else is only as fresh as the collector.
    hb = _q(conn, "SELECT timestamp FROM collector_heartbeat ORDER BY timestamp DESC LIMIT 1")
    if hb.empty:
        alerts.append(Alert("info", "collector", "No collector data yet",
                            "Start the collector: python -m ispmon.collector"))
    else:
        age = (now - datetime.strptime(hb["timestamp"].iloc[0], TS_FMT)).total_seconds()
        if age > th.collector_stale_s:
            alerts.append(Alert("critical", "collector", "Collector is not reporting",
                                f"Last cycle was {fmt_age(age)} ago. Every value in the dashboard may be out of date."))
        else:
            failing = _q(conn, "SELECT device, error FROM device_heartbeat WHERE ok = 0 AND timestamp = "
                               "(SELECT MAX(timestamp) FROM device_heartbeat)")
            for row in failing.itertuples():
                alerts.append(Alert("warning", "collector", f"Cannot read {row.device}",
                                    f"{row.error or 'No response'}. Its data is missing from the latest cycle."))

    cpu = _q(conn, "SELECT cpu_load FROM system_health ORDER BY timestamp DESC LIMIT 1")
    if not cpu.empty and cpu["cpu_load"].iloc[0] > th.cpu_pct:
        alerts.append(Alert("critical", "router", "Router CPU is high",
                            f"{int(cpu['cpu_load'].iloc[0])}% (limit {th.cpu_pct}%)."))

    # Optical level, latest reading per ONU.
    newest = _q(conn, "SELECT MAX(timestamp) AS ts FROM olt_optical_stats")["ts"].iloc[0]
    if newest:
        window_start = (datetime.strptime(newest, TS_FMT) - timedelta(minutes=15)).strftime(TS_FMT)
        recent = _q(conn, "SELECT timestamp, olt_ip, onu_id, rx_power FROM olt_optical_stats WHERE timestamp >= ?",
                    (window_start,))
        latest = recent.sort_values("timestamp").groupby(["olt_ip", "onu_id"]).tail(1)
        low = latest[latest["rx_power"] < th.rx_crit_dbm].sort_values("rx_power")
        if not low.empty:
            names = ", ".join(f"{analytics.short_id(r.olt_ip, r.onu_id)} ({r.rx_power:.1f})"
                              for r in low.head(8).itertuples())
            more = f" and {len(low) - 8} more" if len(low) > 8 else ""
            alerts.append(Alert("critical", "optical", f"{len(low)} ONU(s) below {th.rx_crit_dbm:g} dBm",
                                f"{names}{more}."))

    # ONU drops in the last 10 minutes (events found after a collector gap are excluded).
    drops = _q(conn, "SELECT timestamp, olt_ip, onu_id, dereg_reason FROM onu_events "
                     "WHERE event = 'went_offline' AND after_gap = 0 AND timestamp >= ?", (_cut(now, minutes=10),))
    mass_olts = set()
    for olt, group in drops.groupby("olt_ip"):
        if len(group) >= th.mass_drop_min_onus:
            mass_olts.add(olt)
            reasons = ", ".join(f"{k} x{v}" for k, v in group["dereg_reason"].fillna("N/A").value_counts().items())
            alerts.append(Alert("critical", "onu", f"{len(group)} ONUs dropped together on OLT {olt}",
                                f"{reasons}. A shared cause (area power, feeder or splitter) is more likely than separate customer faults."))
    singles = drops[~drops["olt_ip"].isin(mass_olts)]
    if not singles.empty:
        names = ", ".join(f"{analytics.short_id(r.olt_ip, r.onu_id)} ({r.dereg_reason})" for r in singles.head(8).itertuples())
        alerts.append(Alert("warning", "onu", f"{len(singles)} ONU(s) went offline", f"{names}."))

    # Selected uplinks: link state and zero traffic.
    if critical_interfaces:
        traffic = _q(conn, "SELECT timestamp, interface_name, interface_comment, running, rx_byte, tx_byte "
                           "FROM interface_stats WHERE timestamp >= ?", (_cut(now, minutes=10),))
        if not traffic.empty:
            traffic = analytics.add_display_name(traffic)
            selected = traffic[traffic["display_name"].isin(critical_interfaces)]
            last_state = selected.sort_values("timestamp").groupby("display_name").tail(1)
            down = last_state[last_state["running"] == 0]["display_name"].tolist()
            if down:
                alerts.append(Alert("critical", "interface", "Interface is down", ", ".join(down)))
            rates = analytics.compute_throughput(selected)
            if not rates.empty:
                newest_rate = rates.sort_values("timestamp").groupby("display_name").tail(1)
                idle = newest_rate[(newest_rate["rx_mbps"] == 0) & (newest_rate["tx_mbps"] == 0)]["display_name"].tolist()
                idle = [name for name in idle if name not in down]
                if idle:
                    alerts.append(Alert("critical", "interface", "No traffic on a critical interface", ", ".join(idle)))

    return sorted(alerts, key=lambda a: LEVELS[a.level])
