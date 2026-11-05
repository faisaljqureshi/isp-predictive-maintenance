"""Optical network: every ONU in a table; click a row to open its history."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data, ui
from ispmon import analytics
from ispmon.alerts import fmt_age

HEALTH_ORDER = list(ui.HEALTH_COLORS)


def _fleet_with_trend(th) -> pd.DataFrame:
    fleet = analytics.build_fleet(data.latest_status(), data.latest_optics(), th.rx_warn_dbm, th.rx_crit_dbm)
    if fleet.empty:
        return fleet
    trend = data.optics_trend(24, bucket=30)
    if not trend.empty:
        grouped = trend.groupby(["olt_ip", "onu_id"])["rx_power"]
        extra = pd.DataFrame({
            "rx_change_24h": grouped.last() - grouped.first(),
            "spark": grouped.apply(lambda s: [round(v, 2) for v in s.dropna()] or None),
        }).reset_index()
        fleet = fleet.merge(extra, on=["olt_ip", "onu_id"], how="left")
    else:
        fleet["rx_change_24h"], fleet["spark"] = float("nan"), None
    return fleet


def _filters(fleet: pd.DataFrame) -> pd.DataFrame:
    olts = sorted(fleet["olt_ip"].unique())
    if "opt_olts" in st.session_state:  # keep the widget valid if the set of OLTs changed
        st.session_state["opt_olts"] = [o for o in st.session_state["opt_olts"] if o in olts] or olts
    c1, c2, c3 = st.columns([2, 3, 2])
    chosen_olts = c1.multiselect("OLT", olts, default=olts, key="opt_olts")
    chosen_health = c2.multiselect("Health", HEALTH_ORDER, default=HEALTH_ORDER, key="opt_health")
    needle = c3.text_input("Find ONU", placeholder="e.g. 22 or 11:22", key="opt_search").strip().lower()
    view = fleet[fleet["olt_ip"].isin(chosen_olts) & fleet["health"].isin(chosen_health)]
    if needle:
        view = view[view["short_id"].str.contains(needle, regex=False) | view["onu_id"].str.lower().str.contains(needle, regex=False)
                    | view["mac"].fillna("").str.contains(needle, regex=False)]
    return view


def _table(view: pd.DataFrame):
    shown = pd.DataFrame({
        "Health": [f"{ui.HEALTH_MARK[h]} {h}" for h in view["health"]],
        "OLT": view["olt_ip"], "ONU": view["onu_id"],
        "Rx (dBm)": view["rx_power"], "Change 24 h": view["rx_change_24h"],
        "Trend 24 h": view["spark"], "Tx (dBm)": view["tx_power"], "Temp (C)": view["temperature"],
        "Distance (m)": view["distance_m"],
    }).reset_index(drop=True)
    return st.dataframe(
        shown, hide_index=True, use_container_width=True, height=min(520, 40 + 35 * len(shown)),
        on_select="rerun", selection_mode="single-row", key="opt_table",
        column_config={
            "Rx (dBm)": st.column_config.NumberColumn(format="%.2f"),
            "Change 24 h": st.column_config.NumberColumn(format="%+.2f", help="Last minus first reading in the last 24 hours"),
            "Trend 24 h": st.column_config.LineChartColumn(help="Rx power, 30-minute averages", width="small"),
            "Tx (dBm)": st.column_config.NumberColumn(format="%.2f"),
            "Temp (C)": st.column_config.NumberColumn(format="%.1f"),
            "Distance (m)": st.column_config.NumberColumn(format="%d"),
        })


def _threshold_lines(fig: go.Figure, th) -> None:
    fig.add_hline(y=th.rx_warn_dbm, line_dash="dot", line_color="#F2A93B",
                  annotation_text=f"weak {th.rx_warn_dbm:g}", annotation_position="bottom right")
    fig.add_hline(y=th.rx_crit_dbm, line_dash="dot", line_color="#EF5B5B",
                  annotation_text=f"critical {th.rx_crit_dbm:g}", annotation_position="bottom right")


def _line(df: pd.DataFrame, column: str, title: str, unit: str, color: str) -> go.Figure:
    fig = go.Figure(go.Scatter(x=df["timestamp"], y=df[column], mode="lines", name=title, line=dict(color=color, width=2),
                               connectgaps=False))
    return ui.style_fig(fig, 240, unit)


def _detail(row: pd.Series, th) -> None:
    olt, onu = row["olt_ip"], row["onu_id"]
    with st.container(border=True):
        top, close = st.columns([6, 1])
        top.subheader(f"{onu}")
        top.markdown(f"on OLT {olt} &nbsp; {ui.pill(row['health'], ui.HEALTH_COLORS[row['health']])}", unsafe_allow_html=True)
        if close.button("Close", key="opt_close", use_container_width=True):
            st.session_state["focus_onu"] = None
            st.rerun()

        k = st.columns(6)
        k[0].metric("Rx power", "-" if pd.isna(row["rx_power"]) else f"{row['rx_power']:.2f} dBm")
        k[1].metric("Tx power", "-" if pd.isna(row["tx_power"]) else f"{row['tx_power']:.2f} dBm")
        k[2].metric("Temperature", "-" if pd.isna(row["temperature"]) else f"{row['temperature']:.1f} C")
        k[3].metric("Distance", "-" if pd.isna(row["distance_m"]) else f"{int(row['distance_m'])} m")
        k[4].metric("Online for", "-" if pd.isna(row["alive_seconds"]) or row["status"] != "online"
                    else fmt_age(row["alive_seconds"]))
        k[5].metric("Last drop reason", row["last_dereg_reason"] if isinstance(row["last_dereg_reason"], str) else "-")

        hours = ui.range_picker("onu_range", "24h")
        hist = data.onu_history(olt, onu, hours)
        signal, hardware, history = st.tabs(["Signal", "Hardware", "Events"])
        with signal:
            if hist.empty:
                st.info("No optical readings for this ONU in the selected range.")
            else:
                fig = _line(hist, "rx_power", "Rx power", "Rx power (dBm)", ui.PALETTE[0])
                _threshold_lines(fig, th)
                lo = min(hist["rx_power"].min(), th.rx_crit_dbm) - 1.5
                hi = max(hist["rx_power"].max(), th.rx_warn_dbm) + 1.5
                fig.update_yaxes(range=[lo, hi])
                ui.show(fig)
                ui.show(_line(hist, "tx_power", "Tx power", "ONU Tx power (dBm)", ui.PALETTE[1]))
        with hardware:
            if hist.empty:
                st.info("No readings in the selected range.")
            else:
                a, b, c = st.columns(3)
                with a:
                    ui.show(_line(hist, "temperature", "Temperature", "Temperature (C)", ui.PALETTE[2]))
                with b:
                    ui.show(_line(hist, "voltage", "Supply voltage", "Supply voltage (V)", ui.PALETTE[3]))
                with c:
                    ui.show(_line(hist, "bias_current", "Laser bias", "Laser bias current (mA)", ui.PALETTE[4]))
        with history:
            ev = data.events(max(hours, 168))
            ev = ev[(ev["olt_ip"] == olt) & (ev["onu_id"] == onu)]
            if ev.empty:
                st.info("No drop or re-registration events recorded for this ONU.")
            else:
                st.dataframe(ev[["timestamp", "event", "dereg_reason", "after_gap"]], hide_index=True, use_container_width=True,
                             column_config={"after_gap": st.column_config.CheckboxColumn("Found after gap")})
            st.caption(f"MAC {row['mac']}  |  RTT {row['rtt_tq']} TQ  |  Registered (OLT clock) {row['last_reg_time']}")


def render() -> None:
    th = data.settings().thresholds
    ui.page_header("Optical network", "Signal quality of every ONU. Select a row to open its history.")
    fleet = _fleet_with_trend(th)
    if fleet.empty:
        ui.empty_state("No ONUs yet", "Start the collector with: python -m ispmon.collector")
        return

    view = _filters(fleet).sort_values(["rx_power"], na_position="last").reset_index(drop=True)
    if view.empty:
        st.info("No ONUs match the filters.")
        return
    st.caption(f"{len(view)} of {len(fleet)} ONUs, weakest signal first.")
    event = _table(view)

    rows = []
    try:
        rows = list(event.selection.rows)
    except (AttributeError, KeyError):
        pass
    if rows and rows[0] < len(view):
        picked = view.iloc[rows[0]]
        st.session_state["focus_onu"] = (picked["olt_ip"], picked["onu_id"])
    focus = st.session_state.get("focus_onu")
    match = fleet[(fleet["olt_ip"] == focus[0]) & (fleet["onu_id"] == focus[1])] if focus else fleet.iloc[0:0]
    if match.empty:
        st.info("Select a row above to see that ONU's signal history, hardware readings and events.")
    else:
        _detail(match.iloc[0], th)
