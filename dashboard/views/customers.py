"""Customers and outages: grouped incidents, the raw event log and the ONU registry."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import data, ui
from ispmon import analytics
from ispmon.alerts import fmt_age

SHARED_MIN_ONUS = 3  # drops this close together on one OLT are treated as a shared-cause incident


def _outages(events: pd.DataFrame, th) -> None:
    incidents, tagged = analytics.cluster_outages(events, th.mass_drop_window_s, SHARED_MIN_ONUS)
    if incidents.empty:
        st.info("No ONU drops in this range.")
        return
    shared = incidents[incidents["onus"] >= SHARED_MIN_ONUS]
    k = st.columns(4)
    with k[0]:
        ui.kpi("Incidents", str(len(incidents)), "groups of related drops")
    with k[1]:
        ui.kpi("ONU drops", str(int(incidents["onus"].sum())))
    with k[2]:
        ui.kpi("Shared-cause incidents", str(len(shared)), "3 or more ONUs together", "warn" if len(shared) else "ok")
    with k[3]:
        ui.kpi("Power-related", f"{int(incidents['kind'].isin(['Area power outage', 'Customer power off']).sum())}",
               "incidents caused by power loss")
    st.caption("Drops on one OLT within a short window are treated as one incident. Select a row to see the affected ONUs.")
    table = incidents.assign(start=incidents["start"].dt.strftime("%Y-%m-%d %H:%M:%S"))[
        ["start", "olt_ip", "kind", "onus", "reasons"]]
    event = st.dataframe(table, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                         key="incident_table", column_config={"start": "Start", "olt_ip": "OLT", "kind": "Likely cause",
                                                             "onus": "ONUs", "reasons": "Reasons"})
    try:
        picked = event.selection.rows
    except AttributeError:
        picked = []
    if picked:
        incident = incidents.iloc[picked[0]]
        members = tagged[tagged["incident"] == incident["incident"]][["timestamp", "onu_id", "dereg_reason"]]
        with st.container(border=True):
            st.markdown(f"**{incident['kind']}** on {incident['olt_ip']}, {incident['onus']} ONU(s)")
            st.dataframe(members, hide_index=True, use_container_width=True,
                         column_config={"timestamp": "Detected", "onu_id": "ONU", "dereg_reason": "Reason"})
            if st.button("Open first ONU in optical view", key="inc_open"):
                st.session_state["focus_onu"] = (incident["olt_ip"], members["onu_id"].iloc[0])
                ui.go_to("optical")


def _event_log(events: pd.DataFrame) -> None:
    if events.empty:
        st.info("No events in this range.")
        return
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    kinds = c1.multiselect("Event", sorted(events["event"].unique()), default=sorted(events["event"].unique()), key="ev_kind")
    reasons = sorted(events["dereg_reason"].fillna("N/A").unique())
    chosen = c2.multiselect("Reason", reasons, default=reasons, key="ev_reason")
    olts = sorted(events["olt_ip"].unique())
    chosen_olts = c3.multiselect("OLT", olts, default=olts, key="ev_olt")
    with c4:
        include_gap = st.checkbox("Include events found after a collector gap", value=False,
                                  help="These changes are real but their timestamp is only an upper bound.")
    view = events[events["event"].isin(kinds) & events["dereg_reason"].fillna("N/A").isin(chosen) & events["olt_ip"].isin(chosen_olts)]
    if not include_gap:
        view = view[view["after_gap"] == 0]
    st.caption(f"{len(view)} events")
    st.dataframe(view, hide_index=True, use_container_width=True, height=min(520, 40 + 35 * len(view)),
                 column_config={"after_gap": st.column_config.CheckboxColumn("After gap")})
    ui.csv_button("Download CSV", view, "onu_events.csv", "ev_csv")


def _registry() -> None:
    status = data.latest_status()
    if status.empty:
        st.info("No ONU status data yet.")
        return
    c1, c2 = st.columns([2, 3])
    state = c1.segmented_control("Status", ["all", "online", "offline"], default="all", key="reg_state") \
        if hasattr(st, "segmented_control") else "all"
    needle = c2.text_input("Search by ONU, MAC or OLT", key="reg_search").strip().lower()
    view = status.copy()
    if state in ("online", "offline"):
        view = view[view["status"] == state]
    if needle:
        mask = (view["onu_id"].str.lower().str.contains(needle, regex=False) | view["mac"].fillna("").str.contains(needle, regex=False)
                | view["olt_ip"].str.contains(needle, regex=False))
        view = view[mask]
    view = view.assign(online_for=[fmt_age(a) if s == "online" else "-" for a, s in zip(view["alive_seconds"], view["status"])])
    view = view.sort_values(["olt_ip", "onu_id"])[["olt_ip", "onu_id", "status", "mac", "distance_m", "online_for", "last_dereg_reason"]]
    st.dataframe(view, hide_index=True, use_container_width=True, height=min(560, 40 + 35 * len(view)),
                 column_config={"olt_ip": "OLT", "onu_id": "ONU", "status": "Status", "mac": "MAC", "distance_m": "Distance (m)",
                                "online_for": "Online for", "last_dereg_reason": "Last drop reason"})
    ui.csv_button("Download CSV", view, "onu_registry.csv", "reg_csv")


def render() -> None:
    th = data.settings().thresholds
    ui.page_header("Customers and outages", "What dropped, when, and whether it looks like one shared cause.")
    hours = ui.range_picker("cu_range", "7d")
    events = data.events(hours)
    outages, log, registry = st.tabs(["Outages", "Event log", "ONU registry"])
    with outages:
        _outages(events, th)
    with log:
        _event_log(events)
    with registry:
        _registry()
