"""Dashboard entry point.  Run from the project root:  streamlit run dashboard/app.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # lets `ispmon` and `dashboard` import without installing the project
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="ISP Predictive Maintenance", page_icon=":material/hub:", layout="wide",
                   initial_sidebar_state="expanded")

import pandas as pd  # noqa: E402
from streamlit_autorefresh import st_autorefresh  # noqa: E402

from dashboard import data, ui  # noqa: E402
from dashboard.views import customers, network, optical, overview, security, system  # noqa: E402
from ispmon.alerts import fmt_age  # noqa: E402

pages = {
    "overview": st.Page(overview.render, title="Overview", icon=":material/space_dashboard:", url_path="overview", default=True),
    "optical": st.Page(optical.render, title="Optical network", icon=":material/settings_input_antenna:", url_path="optical"),
    "customers": st.Page(customers.render, title="Customers and outages", icon=":material/group:", url_path="customers"),
    "network": st.Page(network.render, title="Router and traffic", icon=":material/router:", url_path="network"),
    "security": st.Page(security.render, title="Security", icon=":material/shield:", url_path="security"),
    "system": st.Page(system.render, title="Collector and data", icon=":material/monitor_heart:", url_path="system"),
}
st.session_state["pages"] = pages
navigation = st.navigation(
    {
        "Monitor": [pages["overview"], pages["optical"], pages["customers"]],
        "Infrastructure": [pages["network"], pages["security"]],
        "Operations": [pages["system"]],
    }
)
ui.inject_css()

with st.sidebar:
    st.divider()
    heartbeat = data.latest_heartbeat()
    if heartbeat.empty:
        st.caption("Collector: no data yet")
    else:
        age = (pd.Timestamp.now() - pd.to_datetime(heartbeat["timestamp"].iloc[0])).total_seconds()
        stale = age > data.settings().thresholds.collector_stale_s
        st.markdown(f"{'🔴' if stale else '🟢'} Collector, last cycle {fmt_age(age)} ago")
    if st.toggle("Auto-refresh every 60 s", value=True, key="auto_refresh"):
        st_autorefresh(interval=60_000, key="autorefresh_tick")
    if st.button("Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

navigation.run()
