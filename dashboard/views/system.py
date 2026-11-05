"""Collector and data: is the pipeline healthy, and can the data be trusted for modelling?"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data, ui
from ispmon import analytics
from ispmon.alerts import fmt_age

GAP_MIN_S = 300


def _gaps(heartbeats: pd.DataFrame) -> pd.DataFrame:
    if len(heartbeats) < 2:
        return pd.DataFrame(columns=["from", "to", "duration"])
    ts = heartbeats["timestamp"]
    spans = ts.diff().dt.total_seconds()
    gaps = heartbeats[spans > GAP_MIN_S]
    return pd.DataFrame({
        "from": ts.shift(1)[gaps.index].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "to": gaps["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": [fmt_age(s) for s in spans[gaps.index]],
        "seconds": spans[gaps.index].values,
    }).sort_values("seconds", ascending=False).drop(columns="seconds")


def _collector() -> None:
    hours = ui.range_picker("sys_range", "24h")
    hb, devices = data.heartbeats(hours), data.device_heartbeats(hours)
    if hb.empty:
        st.info("No collector cycles recorded in this range.")
        return
    expected = hours * 3600 / data.settings().interval_s
    coverage = min(100.0, 100 * len(hb) / expected)
    k = st.columns(4)
    age = (pd.Timestamp.now() - hb["timestamp"].iloc[-1]).total_seconds()
    with k[0]:
        ui.kpi("Last cycle", f"{fmt_age(age)} ago", "", "crit" if age > data.settings().thresholds.collector_stale_s else "ok")
    with k[1]:
        ui.kpi("Cycles recorded", f"{len(hb):,}", f"{coverage:.0f}% of the expected {int(expected):,}",
               "ok" if coverage >= 95 else "warn")
    with k[2]:
        ui.kpi("Median cycle time", f"{hb['cycle_seconds'].median():.1f} s")
    with k[3]:
        ui.kpi("Cycles with a failure", str(int(((hb["mikrotik_ok"] == 0) | (hb["olts_ok"] < hb["olts_ok"].max())).sum())))

    st.subheader("Devices")
    if devices.empty:
        st.info("No per-device history yet (it is recorded by the updated collector).")
    else:
        summary = devices.groupby("device").agg(cycles=("ok", "size"), success=("ok", "mean"), avg_seconds=("duration_s", "mean"),
                                                last_error=("error", lambda s: next((e for e in reversed(list(s)) if e), ""))).reset_index()
        summary["success"] = (summary["success"] * 100).round(1)
        st.dataframe(summary, hide_index=True, use_container_width=True, column_config={
            "device": "Device", "cycles": "Cycles", "success": st.column_config.ProgressColumn("Success %", min_value=0, max_value=100, format="%.1f"),
            "avg_seconds": st.column_config.NumberColumn("Avg time (s)", format="%.1f"), "last_error": "Last error"})

    a, b = st.columns(2)
    with a:
        st.markdown("**Cycle duration (s)**")
        ui.show(ui.style_fig(go.Figure(go.Scatter(x=hb["timestamp"], y=hb["cycle_seconds"], mode="lines",
                                                  line=dict(color=ui.PALETTE[0], width=1.5))), 280, "seconds"))
    with b:
        st.markdown("**ONUs read per cycle**")
        ui.show(ui.style_fig(go.Figure(go.Scatter(x=hb["timestamp"], y=hb["onus_logged"], mode="lines",
                                                  line=dict(color=ui.PALETTE[1], width=1.5, shape="hv"))), 280, "ONUs"))
    st.subheader("Data gaps")
    gaps = _gaps(hb)
    if gaps.empty:
        st.success("No gaps longer than 5 minutes in this range.")
    else:
        st.caption("Periods when the collector was not recording. Exclude them when building training sets.")
        st.dataframe(gaps, hide_index=True, use_container_width=True)


def _quality() -> None:
    st.markdown("**Does the optical data actually change?** If an ONU reports the exact same Rx value for a day, "
                "the OLT may be returning a cached reading, and a model trained on it would learn nothing.")
    hours = st.select_slider("Look back", options=[6, 24, 72, 168], value=24, format_func=lambda h: f"{h} h", key="dq_hours")
    report = analytics.rx_flatness(data.optics_window(hours), min_samples=max(10, int(hours * 60 / data.settings().interval_s * 0.5)))
    if report.empty:
        st.info("No optical readings in this range.")
        return
    flat = int(report["flat"].sum())
    k = st.columns(3)
    with k[0]:
        ui.kpi("ONUs checked", str(len(report)))
    with k[1]:
        ui.kpi("Flat Rx readings", str(flat), "two or fewer distinct values", "crit" if flat > len(report) / 2 else ("warn" if flat else "ok"))
    with k[2]:
        ui.kpi("Median variation", f"{report['rx_std'].median():.3f} dB", "standard deviation of Rx")
    if flat:
        st.warning("Many ONUs report an unchanging Rx value. Before relying on Rx trends for prediction, confirm the OLT refreshes "
                   "these readings (compare against a reading taken at the ONU, or check whether `show onu opm-diag` has a refresh interval).")
    st.dataframe(report.sort_values(["flat", "rx_std"], ascending=[False, True]), hide_index=True, use_container_width=True,
                 column_config={"olt_ip": "OLT", "onu_id": "ONU", "samples": "Samples", "distinct_values": "Distinct values",
                                "rx_min": st.column_config.NumberColumn("Min Rx", format="%.2f"),
                                "rx_max": st.column_config.NumberColumn("Max Rx", format="%.2f"),
                                "rx_std": st.column_config.NumberColumn("Std dev", format="%.3f"), "flat": "Flat"})


def _storage() -> None:
    path = data.settings().db_path
    size_mb = os.path.getsize(path) / 1e6 if path.exists() else 0
    wal = path.with_name(path.name + "-wal")
    k = st.columns(2)
    with k[0]:
        ui.kpi("Database size", f"{size_mb:,.1f} MB", str(path))
    with k[1]:
        ui.kpi("Write-ahead log", f"{(os.path.getsize(wal) / 1e6 if wal.exists() else 0):,.1f} MB", "checkpointed daily by the collector")
    sizes = data.table_sizes().sort_values("rows", ascending=False)
    st.dataframe(sizes, hide_index=True, use_container_width=True,
                 column_config={"table_name": "Table", "rows": st.column_config.NumberColumn("Rows (approx.)", format="%d")})
    st.caption("Optical and ONU history is kept indefinitely (it is the training data). Interface counters are pruned after "
               f"{data.settings().retention_interface_days} days and router logs after {data.settings().retention_log_days} days.")


def render() -> None:
    ui.page_header("Collector and data", "Pipeline health and data quality. Check here before trusting a trend or training a model.")
    collector, quality, storage = st.tabs(["Collector", "Data quality", "Storage"])
    with collector:
        _collector()
    with quality:
        _quality()
    with storage:
        _storage()
