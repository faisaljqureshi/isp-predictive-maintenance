"""Shared presentation helpers: styling, KPI tiles, alert rows, chart defaults, navigation."""
from __future__ import annotations

from html import escape

import plotly.graph_objects as go
import streamlit as st

from dashboard import data

PALETTE = ["#4C9BE8", "#5ED3C5", "#F2A93B", "#B58CF0", "#EF7A9B", "#8FD14F", "#7C93B3"]
HEALTH_COLORS = {
    "Critical": "#EF5B5B",
    "Weak": "#F2A93B",
    "OK": "#3DBE8B",
    "No data": "#7C93B3",
    "Offline": "#52627A",
}
HEALTH_MARK = {"Critical": "🔴", "Weak": "🟠", "OK": "🟢", "No data": "⚪", "Offline": "⚫"}

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
.stApp { font-family: 'IBM Plex Sans', 'Segoe UI', system-ui, sans-serif; font-variant-numeric: tabular-nums; }
.block-container { padding-top: 2.4rem; max-width: 1480px; }
h1 { font-size: 1.65rem !important; font-weight: 600 !important; letter-spacing: -0.01em; padding-bottom: 0.1rem !important; }
h2, h3 { font-weight: 600 !important; letter-spacing: -0.005em; }
.kpi-label { font-size: 0.78rem; color: #8FA3BC; margin-bottom: 0.15rem; }
.kpi-value { font-size: 1.7rem; font-weight: 600; line-height: 1.15; }
.kpi-sub { font-size: 0.78rem; color: #8FA3BC; margin-top: 0.1rem; min-height: 1.1rem; }
.tone-ok { color: #3DBE8B; } .tone-warn { color: #F2A93B; } .tone-crit { color: #EF5B5B; }
.alert-row { border-left: 3px solid #7C93B3; padding: 0.55rem 0.9rem; margin: 0.35rem 0; border-radius: 3px;
             background: rgba(124,147,179,0.10); }
.alert-row.critical { border-color: #EF5B5B; background: rgba(239,91,91,0.10); }
.alert-row.warning { border-color: #F2A93B; background: rgba(242,169,59,0.10); }
.alert-row .t { font-weight: 600; }
.alert-row .d { color: #B8C6D9; font-size: 0.9rem; }
.pill { display: inline-block; padding: 0.05rem 0.6rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
        border: 1px solid currentColor; }
.empty { padding: 2.2rem 1rem; text-align: center; color: #8FA3BC; border: 1px dashed #2B3F5C; border-radius: 6px; }
.empty b { color: #E6EDF5; font-size: 1.05rem; }
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def kpi(label: str, value: str, sub: str = "", tone: str = "") -> None:
    cls = f" tone-{tone}" if tone else ""
    with st.container(border=True):
        st.markdown(
            f"<div class='kpi-label'>{escape(label)}</div><div class='kpi-value{cls}'>{escape(value)}</div>"
            f"<div class='kpi-sub'>{escape(sub)}</div>",
            unsafe_allow_html=True,
        )


def pill(text: str, color: str) -> str:
    return f"<span class='pill' style='color:{color}'>{escape(text)}</span>"


def alert_row(level: str, title: str, detail: str) -> None:
    st.markdown(
        f"<div class='alert-row {escape(level)}'><div class='t'>{escape(title)}</div>"
        f"<div class='d'>{escape(detail)}</div></div>",
        unsafe_allow_html=True,
    )


def empty_state(title: str, hint: str) -> None:
    st.markdown(f"<div class='empty'><b>{escape(title)}</b><br>{escape(hint)}</div>", unsafe_allow_html=True)


def range_picker(key: str, default: str = "24h") -> float:
    """Time-range control. Returns the number of hours."""
    options = list(data.RANGES)
    if hasattr(st, "segmented_control"):
        choice = st.segmented_control("Time range", options, default=default, key=key, label_visibility="collapsed")
    else:
        choice = st.radio("Time range", options, index=options.index(default), horizontal=True, key=key,
                          label_visibility="collapsed")
    return data.RANGES[choice or default]


def style_fig(fig: go.Figure, height: int = 320, y_title: str | None = None) -> go.Figure:
    grid = "rgba(143,163,188,0.14)"
    fig.update_layout(
        height=height, margin=dict(l=4, r=4, t=28, b=4), hovermode="x unified", colorway=PALETTE,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#C9D5E4", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=11)),
    )
    fig.update_xaxes(gridcolor=grid, zeroline=False)
    fig.update_yaxes(gridcolor=grid, zeroline=False, title=y_title)
    return fig


def show(fig: go.Figure, **kwargs):
    return st.plotly_chart(fig, use_container_width=True, theme=None, config={"displaylogo": False}, **kwargs)


def go_to(page_key: str) -> None:
    pages = st.session_state.get("pages")
    if pages and page_key in pages:
        st.switch_page(pages[page_key])


def csv_button(label: str, df, filename: str, key: str) -> None:
    st.download_button(label, df.to_csv(index=False).encode("utf-8"), file_name=filename, mime="text/csv", key=key)
