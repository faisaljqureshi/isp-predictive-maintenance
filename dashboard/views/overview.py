"""Overview: one screen to answer 'is the network healthy right now?', with a clickable ONU grid."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data, ui
from ispmon import analytics
from ispmon.alerts import fmt_age


def _points(selection) -> list[dict]:
    try:
        return list(selection["selection"]["points"])
    except (KeyError, TypeError):
        return []


def _fleet_figure(fleet: pd.DataFrame) -> go.Figure:
    olts = list(fleet["olt_ip"].unique())
    fig = go.Figure()
    for health, color in ui.HEALTH_COLORS.items():
        sub = fleet[fleet["health"] == health]
        if sub.empty:
            continue
        rx = sub["rx_power"].map(lambda v: "no reading" if pd.isna(v) else f"{v:.2f} dBm")
        fig.add_trace(go.Scatter(
            x=sub["onu_num"], y=sub["olt_ip"], mode="markers+text", name=health,
            text=sub["onu_num"].astype(int).astype(str),
            textfont=dict(size=10, color="#C9D5E4" if health in ("Offline", "No data") else "#0F1B2D"),
            marker=dict(symbol="square", size=27, color=color),
            customdata=list(zip(sub["olt_ip"], sub["onu_id"])),
            hovertext=[f"{i}<br>Rx {r}<br>{health}" for i, r in zip(sub["onu_id"], rx)],
            hovertemplate="%{hovertext}<extra></extra>",
        ))
    fig = ui.style_fig(fig, height=120 + 70 * len(olts))
    fig.update_layout(hovermode="closest", clickmode="event+select", dragmode="select")
    fig.update_xaxes(title="ONU number", dtick=2, range=[0, float(fleet["onu_num"].max()) + 1.5], showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False, type="category", title=None)
    return fig


def render() -> None:
    th = data.settings().thresholds
    ui.page_header("Network overview", "Select an ONU in the grid to inspect it.")

    status, optics = data.latest_status(), data.latest_optics()
    if status.empty and optics.empty:
        ui.empty_state("No telemetry yet", "Start the collector with: python -m ispmon.collector. Data appears within a minute.")
        return
    fleet = analytics.build_fleet(status, optics, th.rx_warn_dbm, th.rx_crit_dbm)
    alerts = data.get_alerts(tuple(data.load_critical_interfaces()))
    heartbeat = data.latest_heartbeat()

    ev = data.events(24)
    drops_24h = int(((ev["event"] == "went_offline") & (ev["after_gap"] == 0)).sum()) if not ev.empty else 0
    online, total = int((fleet["status"] == "online").sum()), len(fleet)
    critical, weak = int((fleet["health"] == "Critical").sum()), int((fleet["health"] == "Weak").sum())
    worst = fleet["rx_power"].min()

    cols = st.columns(5)
    with cols[0]:
        ui.kpi("ONUs online", f"{online} / {total}", f"{total - online} offline", "ok" if online == total else "")
    with cols[1]:
        ui.kpi("Critical optical level", str(critical), f"Rx below {th.rx_crit_dbm:g} dBm", "crit" if critical else "ok")
    with cols[2]:
        ui.kpi("Weak optical level", str(weak), f"{th.rx_crit_dbm:g} to {th.rx_warn_dbm:g} dBm", "warn" if weak else "ok")
    with cols[3]:
        ui.kpi("Drops in 24 h", str(drops_24h), "ONUs that went offline")
    with cols[4]:
        age = ""
        if not heartbeat.empty:
            age = fmt_age((pd.Timestamp.now() - pd.to_datetime(heartbeat["timestamp"].iloc[0])).total_seconds()) + " ago"
        ui.kpi("Weakest Rx", "-" if pd.isna(worst) else f"{worst:.1f} dBm", f"data updated {age}" if age else "")

    st.subheader("Active alerts")
    if not alerts:
        st.success("No active alerts.")
    for alert in alerts:
        ui.alert_row(alert.level, alert.title, alert.detail)

    st.subheader("Optical health by ONU")
    st.caption(f"Each square is one ONU, grouped by OLT. Green is above {th.rx_warn_dbm:g} dBm, "
               f"orange down to {th.rx_crit_dbm:g}, red below. Click a square to select it.")
    selection = ui.show(_fleet_figure(fleet), on_select="rerun", selection_mode="points", key="fleet_grid")
    points = _points(selection)
    if points and points[0].get("customdata"):
        st.session_state["focus_onu"] = tuple(points[0]["customdata"][:2])

    focus = st.session_state.get("focus_onu")
    row = fleet[(fleet["olt_ip"] == focus[0]) & (fleet["onu_id"] == focus[1])] if focus else fleet.iloc[0:0]
    if not row.empty:
        r = row.iloc[0]
        with st.container(border=True):
            a, b, c, d, e = st.columns([2, 1, 1, 1, 1.4])
            a.markdown(f"**{r['onu_id']}** on {r['olt_ip']}<br>{ui.pill(r['health'], ui.HEALTH_COLORS[r['health']])}",
                       unsafe_allow_html=True)
            b.metric("Rx power", "-" if pd.isna(r["rx_power"]) else f"{r['rx_power']:.2f} dBm")
            c.metric("Distance", "-" if pd.isna(r["distance_m"]) else f"{int(r['distance_m'])} m")
            d.metric("Last drop", r["last_dereg_reason"] if isinstance(r["last_dereg_reason"], str) else "-")
            with e:
                if st.button("Open ONU detail", key="ov_open", use_container_width=True):
                    ui.go_to("optical")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Online ONUs over time")
        hours = ui.range_picker("ov_range", "24h")
        counts = data.online_counts(hours)
        if counts.empty:
            st.info("No ONU history in this range.")
        else:
            fig = go.Figure()
            for olt, group in counts.groupby("olt_ip"):
                fig.add_trace(go.Scatter(x=group["timestamp"], y=group["online"], name=olt, mode="lines",
                                         line=dict(shape="hv", width=2)))
            ui.show(ui.style_fig(fig, 300, "ONUs online"))
    with right:
        st.subheader("By OLT")
        summary = fleet.groupby("olt_ip").agg(
            online=("status", lambda s: int((s == "online").sum())),
            offline=("status", lambda s: int((s != "online").sum())),
            critical=("health", lambda s: int((s == "Critical").sum())),
            weak=("health", lambda s: int((s == "Weak").sum())),
            median_rx=("rx_power", "median"),
            worst_rx=("rx_power", "min"),
        ).reset_index()
        st.dataframe(summary, hide_index=True, use_container_width=True, column_config={
            "olt_ip": "OLT", "median_rx": st.column_config.NumberColumn("Median Rx", format="%.1f"),
            "worst_rx": st.column_config.NumberColumn("Worst Rx", format="%.1f")})
