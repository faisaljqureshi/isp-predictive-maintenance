"""Security: router log activity and failed-login sources."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import data, ui

LOGIN_FAILURE = "login failure"


def render() -> None:
    ui.page_header("Security", "Router log activity. Failed logins and their sources are pulled out for you.")
    hours = ui.range_picker("sec_range", "24h")
    logs = data.security_logs(hours)
    if logs.empty:
        ui.empty_state("No router log entries in this range", "Entries appear once the collector has read the MikroTik log.")
        return
    message = logs["message"].fillna("")
    failures = logs[message.str.contains(LOGIN_FAILURE, case=False, regex=False)].copy()
    failures["source"] = failures["message"].str.extract(r"from ([0-9a-fA-F\.:]+)")[0]
    failures["user"] = failures["message"].str.extract(r"for user (\S+)")[0]

    k = st.columns(4)
    with k[0]:
        ui.kpi("Log entries", f"{len(logs):,}", "in the selected days")
    with k[1]:
        ui.kpi("Failed logins", str(len(failures)), "", "crit" if len(failures) >= 10 else ("warn" if len(failures) else "ok"))
    with k[2]:
        ui.kpi("Distinct sources", str(failures["source"].nunique()), "addresses with failed logins")
    with k[3]:
        ui.kpi("Topics seen", str(logs["topics"].nunique()))

    overview, entries = st.tabs(["Overview", "Log entries"])
    with overview:
        a, b = st.columns(2)
        with a:
            st.markdown("**Failed logins by source**")
            if failures.empty:
                st.success("No failed logins in this range.")
            else:
                top = failures.groupby(["source", "user"], dropna=False).size().reset_index(name="attempts") \
                    .sort_values("attempts", ascending=False).head(15)
                st.dataframe(top, hide_index=True, use_container_width=True,
                             column_config={"source": "Source address", "user": "User tried", "attempts": "Attempts"})
        with b:
            st.markdown("**Entries by topic**")
            topics = logs["topics"].fillna("(none)").value_counts().head(12).sort_values()
            fig = go.Figure(go.Bar(x=topics.values, y=topics.index, orientation="h", marker_color=ui.PALETTE[0]))
            ui.show(ui.style_fig(fig, 340).update_layout(hovermode="closest"))
    with entries:
        c1, c2 = st.columns([2, 3])
        topic_options = sorted(logs["topics"].fillna("(none)").unique())
        chosen = c1.multiselect("Topic", topic_options, default=[], key="sec_topics", placeholder="All topics")
        needle = c2.text_input("Search messages", key="sec_search")
        view = logs
        if chosen:
            view = view[view["topics"].fillna("(none)").isin(chosen)]
        if needle:
            view = view[view["message"].fillna("").str.contains(needle, case=False, regex=False)]
        st.caption(f"{len(view)} entries (newest first)")
        st.dataframe(view, hide_index=True, use_container_width=True, height=min(560, 40 + 35 * len(view)),
                     column_config={"ingest_date": "Date read", "router_time": "Router time", "topics": "Topics",
                                    "message": st.column_config.TextColumn("Message", width="large")})
        ui.csv_button("Download CSV", view, "router_logs.csv", "sec_csv")
