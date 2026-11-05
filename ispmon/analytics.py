"""Pure pandas helpers shared by the dashboard, the alert engine and (later) the ML pipeline."""
from __future__ import annotations

from collections import Counter

import pandas as pd

POWER_OFF = "Power Off"
WIRE_DOWN = "Wire Down"
OPTICAL_COLUMNS = ["rx_power", "tx_power", "temperature", "voltage", "bias_current"]


def short_id(olt_ip: str, onu_id: str) -> str:
    """'172.30.49.11' + 'EPON0/1:22' -> '11:22'"""
    return f"{olt_ip.rsplit('.', 1)[-1]}:{onu_id.rsplit(':', 1)[-1]}"


def add_display_name(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    name = out["interface_name"].astype(str)
    comment = out["interface_comment"].fillna("").astype(str).str.strip()
    out["display_name"] = name.where(comment == "", name + " (" + comment + ")")
    return out


def compute_throughput(df: pd.DataFrame, gap_s: int = 300, bucket_minutes: int = 0) -> pd.DataFrame:
    """Byte counters -> Mbps. Skips counter resets and intervals spanning a collector gap."""
    columns = ["timestamp", "display_name", "rx_mbps", "tx_mbps"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values(["display_name", "timestamp"])
    grouped = d.groupby("display_name")
    seconds = grouped["timestamp"].diff().dt.total_seconds()
    rx_delta, tx_delta = grouped["rx_byte"].diff(), grouped["tx_byte"].diff()
    valid = seconds.between(1, gap_s) & (rx_delta >= 0) & (tx_delta >= 0)
    d["rx_mbps"] = (rx_delta * 8 / (seconds * 1e6)).where(valid)
    d["tx_mbps"] = (tx_delta * 8 / (seconds * 1e6)).where(valid)
    d = d.dropna(subset=["rx_mbps", "tx_mbps"])[columns]
    if bucket_minutes > 1 and not d.empty:
        d = (
            d.set_index("timestamp")
            .groupby("display_name")[["rx_mbps", "tx_mbps"]]
            .resample(f"{bucket_minutes}min")
            .mean()
            .dropna()
            .reset_index()
        )
    return d.reset_index(drop=True)


def classify_rx(rx: float, status: str, warn: float, crit: float) -> str:
    if status != "online":
        return "Offline"
    if pd.isna(rx):
        return "No data"
    if rx < crit:
        return "Critical"
    if rx < warn:
        return "Weak"
    return "OK"


def build_fleet(status: pd.DataFrame, optics: pd.DataFrame, warn: float, crit: float) -> pd.DataFrame:
    """One row per ONU: latest status joined with its latest optical reading and a health class."""
    status_cols = ["olt_ip", "onu_id", "status", "mac", "distance_m", "rtt_tq", "alive_seconds",
                   "last_dereg_reason", "last_dereg_time", "last_reg_time"]
    key = ["olt_ip", "onu_id"]
    fleet = status.reindex(columns=status_cols).merge(optics.reindex(columns=key + OPTICAL_COLUMNS), on=key, how="outer")
    # Optical rows exist only for ONUs the OLT reports online, so a missing status means online.
    fleet["status"] = fleet["status"].fillna("online")
    fleet.loc[fleet["status"] != "online", OPTICAL_COLUMNS] = float("nan")  # no stale readings on dead links
    fleet["health"] = [classify_rx(r, s, warn, crit) for r, s in zip(fleet["rx_power"], fleet["status"])]
    fleet["onu_num"] = pd.to_numeric(fleet["onu_id"].str.extract(r":(\d+)$")[0])
    fleet["short_id"] = [short_id(o, u) for o, u in zip(fleet["olt_ip"], fleet["onu_id"])]
    return fleet.sort_values(["olt_ip", "onu_num"]).reset_index(drop=True)


def _incident_kind(reasons: Counter, size: int, min_size: int) -> str:
    total = sum(reasons.values()) or 1
    power, wire = reasons.get(POWER_OFF, 0) / total, reasons.get(WIRE_DOWN, 0) / total
    if size >= min_size:
        if power >= 0.7:
            return "Area power outage"
        if wire >= 0.5:
            return "Feeder or splitter fault"
        return "Mixed cause"
    if wire >= 0.5:
        return "Drop fibre fault"
    if power >= 0.5:
        return "Customer power off"
    return "Single ONU drop"


def cluster_outages(events: pd.DataFrame, window_s: int = 120, min_size: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Group went_offline events on the same OLT that happen close together into incidents.

    Many ONUs dropping together points to a shared cause (area power, feeder, splitter); a lone ONU
    points to its own drop cable or the customer's power. Events found after a collector gap are skipped
    because their timing is unknown.
    Returns (incidents, per-event table with an incident id).
    """
    columns = ["start", "end", "olt_ip", "onus", "kind", "reasons", "onu_ids", "incident"]
    if events.empty:
        return pd.DataFrame(columns=columns), events
    drops = events[(events["event"] == "went_offline") & (events["after_gap"].fillna(0) == 0)].copy()
    if drops.empty:
        return pd.DataFrame(columns=columns), drops
    drops["timestamp"] = pd.to_datetime(drops["timestamp"])
    drops = drops.sort_values(["olt_ip", "timestamp"])
    gap = drops.groupby("olt_ip")["timestamp"].diff().dt.total_seconds()
    drops["incident"] = (gap.isna() | (gap > window_s)).cumsum()
    rows = []
    for incident, group in drops.groupby("incident"):
        reasons = Counter(group["dereg_reason"].fillna("N/A"))
        rows.append(
            {
                "start": group["timestamp"].min(),
                "end": group["timestamp"].max(),
                "olt_ip": group["olt_ip"].iloc[0],
                "onus": len(group),
                "kind": _incident_kind(reasons, len(group), min_size),
                "reasons": ", ".join(f"{name} x{count}" for name, count in reasons.most_common()),
                "onu_ids": list(group["onu_id"]),
                "incident": incident,
            }
        )
    incidents = pd.DataFrame(rows, columns=columns).sort_values("start", ascending=False).reset_index(drop=True)
    return incidents, drops


def rx_flatness(optics: pd.DataFrame, min_samples: int = 30) -> pd.DataFrame:
    """Per-ONU variability of Rx power. A reading that never changes is suspicious: it may be cached by the OLT."""
    columns = ["olt_ip", "onu_id", "samples", "distinct_values", "rx_min", "rx_max", "rx_std", "flat"]
    if optics.empty:
        return pd.DataFrame(columns=columns)
    out = (
        optics.groupby(["olt_ip", "onu_id"])["rx_power"]
        .agg(samples="count", distinct_values="nunique", rx_min="min", rx_max="max", rx_std="std")
        .reset_index()
    )
    out["flat"] = (out["samples"] >= min_samples) & (out["distinct_values"] <= 2)
    return out[columns]
