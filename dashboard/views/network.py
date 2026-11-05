"""Router and traffic: throughput per interface, link state, router CPU and memory."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data, ui
from ispmon import analytics


def _display(catalog: pd.DataFrame) -> pd.DataFrame:
    return analytics.add_display_name(catalog) if not catalog.empty else catalog


def _save_selection() -> None:
    data.save_critical_interfaces(list(st.session_state.get("net_ifaces", [])))


def _traffic(catalog: pd.DataFrame) -> None:
    if catalog.empty:
        st.info("No interface data yet.")
        return
    options = sorted(catalog["display_name"])
    saved = [n for n in data.load_critical_interfaces() if n in options]
    st.multiselect("Interfaces to chart (also the ones watched for alerts)", options, default=saved, key="net_ifaces",
                   on_change=_save_selection, placeholder="Choose one or more interfaces")
    chosen = st.session_state.get("net_ifaces", saved)
    hours = ui.range_picker("net_range", "24h")
    if not chosen:
        st.info("Choose at least one interface to see its traffic.")
        return
    names = tuple(catalog[catalog["display_name"].isin(chosen)]["interface_name"].unique())
    raw = analytics.add_display_name(data.traffic_raw(hours, names))
    raw = raw[raw["display_name"].isin(chosen)]
    rates = analytics.compute_throughput(raw, bucket_minutes=data.bucket_minutes(hours) if hours > 6 else 0)
    if rates.empty:
        st.info("Not enough samples in this range yet.")
        return
    k = st.columns(4)
    with k[0]:
        ui.kpi("Download now", f"{rates.sort_values('timestamp')['rx_mbps'].iloc[-1]:.1f} Mbps")
    with k[1]:
        ui.kpi("Download peak", f"{rates['rx_mbps'].max():.1f} Mbps", f"average {rates['rx_mbps'].mean():.1f}")
    with k[2]:
        ui.kpi("Upload now", f"{rates.sort_values('timestamp')['tx_mbps'].iloc[-1]:.1f} Mbps")
    with k[3]:
        ui.kpi("Upload peak", f"{rates['tx_mbps'].max():.1f} Mbps", f"average {rates['tx_mbps'].mean():.1f}")
    left, right = st.columns(2)
    for col, field, title in ((left, "rx_mbps", "Download (Rx)"), (right, "tx_mbps", "Upload (Tx)")):
        with col:
            st.markdown(f"**{title}**")
            fig = go.Figure()
            for name, group in rates.groupby("display_name"):
                fig.add_trace(go.Scatter(x=group["timestamp"], y=group[field], name=name, mode="lines", line=dict(width=1.6)))
            ui.show(ui.style_fig(fig, 330, "Mbps"))


def _interfaces(catalog: pd.DataFrame) -> None:
    if catalog.empty:
        st.info("No interface data yet.")
        return
    view = catalog.assign(
        state=catalog["running"].map({1.0: "Up", 0.0: "Down"}).fillna("Not seen recently"))
    view = view.sort_values(["state", "interface_name"])[["interface_name", "interface_comment", "state", "last_seen"]]
    st.dataframe(view, hide_index=True, use_container_width=True,
                 column_config={"interface_name": "Interface", "interface_comment": "Comment", "state": "Link state",
                                "last_seen": "Last seen"})
    st.caption("Interfaces that only appear while a customer is connected (such as PPPoE sessions) show as 'Not seen recently' between sessions.")


def _health() -> None:
    hours = ui.range_picker("rh_range", "24h")
    health = data.router_health(hours)
    if health.empty:
        st.info("No router health data in this range.")
        return
    latest = health.iloc[-1]
    k = st.columns(3)
    with k[0]:
        ui.kpi("CPU load", f"{latest['cpu_load']:.0f}%", f"alert above {data.settings().thresholds.cpu_pct}%",
               "crit" if latest["cpu_load"] > data.settings().thresholds.cpu_pct else "ok")
    with k[1]:
        ui.kpi("Peak CPU", f"{health['cpu_load'].max():.0f}%", "in the selected range")
    with k[2]:
        ui.kpi("Free memory", f"{latest['free_memory_mb']:.0f} MB")
    a, b = st.columns(2)
    with a:
        st.markdown("**CPU load (%)**")
        fig = go.Figure(go.Scatter(x=health["timestamp"], y=health["cpu_load"], mode="lines", line=dict(color=ui.PALETTE[0])))
        fig.add_hline(y=data.settings().thresholds.cpu_pct, line_dash="dot", line_color="#EF5B5B")
        ui.show(ui.style_fig(fig, 300, "%").update_yaxes(range=[0, 100]))
    with b:
        st.markdown("**Free memory (MB)**")
        ui.show(ui.style_fig(go.Figure(go.Scatter(x=health["timestamp"], y=health["free_memory_mb"], mode="lines",
                                                  line=dict(color=ui.PALETTE[1]))), 300, "MB"))


def render() -> None:
    ui.page_header("Router and traffic", "Throughput, link state and health of the MikroTik edge router.")
    catalog = _display(data.interface_catalog())
    traffic, interfaces, health = st.tabs(["Traffic", "Interfaces", "Router health"])
    with traffic:
        _traffic(catalog)
    with interfaces:
        _interfaces(catalog)
    with health:
        _health()
