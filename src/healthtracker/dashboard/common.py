"""Shared pieces for the Streamlit dashboard: data access, units, and chart styling.

The dashboard is read-only. It reads daily_features, insights, and the
recommendation tables; it never writes.

Chart rules: one y-axis per chart, thin 2px lines, hairline grids, categorical
colors in a fixed order (validated palette), status colors only for status,
and every chart has a table view.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.db import connect, fetch_df
from healthtracker.units import Display

# The phone app's palette (web/src/styles.css): one saturated hue per metric on near-black.
# Series slots: 0 HRV green, 1 sleep violet, 2 heart red, 3 load amber.
DARK = {"series": ["#14f0a0", "#8b6dff", "#ff4f6e", "#ffa424"], "muted": "#7c7c86", "grid": "rgba(255,255,255,0.07)",
        "axis": "rgba(255,255,255,0.13)", "band": "rgba(139,109,255,0.16)", "ink2": "#b6b6be", "neutral": "#45454d",
        "ink": "#f4f4f5"}
LIGHT = DARK  # the dashboard is dark-only, like the app
STATUS = {"good": "#14f0a0", "warning": "#ffb21f", "serious": "#ff8540", "critical": "#ff4258"}
STATUS_ICON = {"good": "✓", "warning": "!", "serious": "!!", "critical": "✕"}
RANGES = {"30 days": 30, "90 days": 90, "6 months": 182, "1 year": 365, "All": 3650}


def setup(title: str) -> None:
    st.set_page_config(page_title=f"{title} · Health tracker", page_icon="📈", layout="wide")
    # Theme (colors, fonts, radius) lives in .streamlit/config.toml; this adds the app's card look,
    # its small-caps labels, the soft violet glow at the top, and phone-sized spacing.
    st.markdown(
        """<style>
        .stApp {background: radial-gradient(120% 40% at 50% -10%, rgba(139,109,255,0.14), transparent 60%), #050506;}
        .block-container {padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1180px;}
        h1, h2, h3 {letter-spacing: -0.025em;}
        h1 {font-weight: 600;}
        div[data-testid="stMetric"] {background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012)), #111114;
            border: 1px solid rgba(255,255,255,0.07); border-radius: 22px; padding: 14px 16px;}
        div[data-testid="stMetricLabel"] p {font-family: "Geist Mono", monospace; font-size: 0.7rem; letter-spacing: 0.08em;
            text-transform: uppercase; color: #7c7c86;}
        div[data-testid="stMetricValue"] {font-family: "Domine", serif; font-weight: 600; letter-spacing: -0.025em;
            font-variant-numeric: tabular-nums;}
        div[data-testid="stPlotlyChart"], div[data-testid="stDataFrame"] {background: #111114; border: 1px solid rgba(255,255,255,0.07);
            border-radius: 22px; padding: 6px 8px;}
        div[data-testid="stExpander"] details {border-radius: 18px;}
        div[data-testid="stCaptionContainer"] {color: #7c7c86;}
        @media (max-width: 640px) {
          .block-container {padding: 1.2rem 0.75rem 4rem;}
          h1 {font-size: 1.9rem !important;}
          div[data-testid="stMetric"] {padding: 12px 14px;}
          div[data-testid="stPlotlyChart"] {padding: 4px 2px;}
        }
        </style>""",
        unsafe_allow_html=True,
    )


def palette() -> dict:
    return DARK


@st.cache_data(ttl=60, show_spinner=False)
def query(sql: str, params: tuple | dict | None = None) -> pd.DataFrame:
    with connect() as conn:
        return fetch_df(conn, sql, params)


@st.cache_data(ttl=60, show_spinner=False)
def profile() -> dict:
    df = query("SELECT * FROM core.profile WHERE id = 1")
    return df.iloc[0].to_dict() if not df.empty else {"display_units": "imperial", "timezone": "UTC"}


def display() -> Display:
    return Display(profile().get("display_units") or "imperial")


def features(days: int) -> pd.DataFrame:
    df = query("""SELECT * FROM analytics.daily_features
                  WHERE date >= (SELECT max(date) FROM analytics.daily_features) - %s ORDER BY date""", (days,))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def range_filter(key: str = "range", default: str = "90 days") -> int:
    """The one filter row above the charts it scopes."""
    choice = st.segmented_control("Range", list(RANGES), default=default, key=key, label_visibility="collapsed")
    return RANGES[choice or default]


def empty_state(msg: str = "No data yet for this view.") -> None:
    st.info(msg, icon="ℹ️")


# ------------------------------------------------------------------ charts
def base_layout(fig: go.Figure, title: str, y_title: str = "", height: int = 300, legend: bool = True) -> go.Figure:
    pal = palette()
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left",
               "font": {"size": 16, "family": "Domine, Georgia, serif", "color": pal["ink"]}},
        font={"family": "Geist, system-ui, sans-serif", "color": pal["ink2"], "size": 12},
        height=height, margin={"l": 8, "r": 8, "t": 72 if legend else 48, "b": 8},
        hovermode="x unified", showlegend=legend,
        # Legend in its own row under the title: top-right collides with the title on a phone,
        # and below the plot it collides with two-line date ticks.
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "left", "x": 0, "title": None,
                "font": {"color": pal["ink2"]}},
        hoverlabel={"bgcolor": "#1a1a1f", "bordercolor": pal["axis"], "font": {"family": "Geist", "color": pal["ink"]}},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, linecolor=pal["axis"], ticks="", title=None)
    fig.update_yaxes(gridcolor=pal["grid"], gridwidth=1, zeroline=False, linecolor=pal["axis"],
                     title={"text": y_title, "font": {"size": 12, "color": pal["muted"]}})
    return fig


def line(fig: go.Figure, x, y, name: str, slot: int = 0, width: float = 2, dash: str | None = None,
         mode: str = "lines", fmt: str = ".1f", opacity: float = 1.0) -> None:
    color = palette()["series"][slot]
    fig.add_trace(go.Scatter(
        x=x, y=y, name=name, mode=mode, opacity=opacity, connectgaps=False,
        line={"color": color, "width": width, "dash": dash, "shape": "spline", "smoothing": 0.4},
        marker={"size": 8, "color": color, "line": {"width": 2, "color": "rgba(0,0,0,0)"}},
        hovertemplate=f"{name}: %{{y:{fmt}}}<extra></extra>"))


def dots(fig: go.Figure, x, y, name: str, slot: int = 0, fmt: str = ".1f", size: int = 8) -> None:
    color = palette()["series"][slot]
    fig.add_trace(go.Scatter(x=x, y=y, name=name, mode="markers", opacity=0.55,
                             marker={"size": size, "color": color},
                             hovertemplate=f"{name}: %{{y:{fmt}}}<extra></extra>"))


def bars(fig: go.Figure, x, y, name: str, slot: int = 0, fmt: str = ".0f", colors=None) -> None:
    fig.add_trace(go.Bar(x=x, y=y, name=name, marker={"color": colors or palette()["series"][slot],
                                                      "cornerradius": 4, "line": {"width": 0}},
                         hovertemplate=f"{name}: %{{y:{fmt}}}<extra></extra>"))
    fig.update_layout(bargap=0.25)


def band(fig: go.Figure, lo: float | None, hi: float | None, label: str) -> None:
    """A recessive target band (horizontal), labeled once."""
    if lo is None and hi is None:
        return
    pal = palette()
    fig.add_hrect(y0=lo if lo is not None else hi, y1=hi if hi is not None else lo, line_width=0,
                  fillcolor=pal["band"], layer="below",
                  annotation_text=label, annotation_position="top left",
                  annotation_font={"size": 11, "color": pal["muted"]})


def show(fig: go.Figure, table: pd.DataFrame | None = None, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", theme="streamlit", key=key,
                    config={"displayModeBar": False})
    if table is not None and not table.empty:
        with st.expander("Table view"):
            st.dataframe(table, hide_index=True, width="stretch")


def status_chip(level: str, text: str) -> str:
    color = STATUS[level]
    return (f"<span style='display:inline-flex;gap:6px;align-items:center;padding:2px 10px;border-radius:999px;"
            f"border:1px solid {color};font-size:0.85rem'><b style='color:{color}'>{STATUS_ICON[level]}</b>"
            f"{text}</span>")


def z_status(z: float | None, higher_is_better: bool = True) -> tuple[str, str]:
    if z is None or pd.isna(z):
        return "warning", "no baseline yet"
    z = round(float(z), 1) + 0.0  # avoid "-0.0"
    good = z if higher_is_better else -z
    if good <= -1.5:
        return "critical", f"{z:+.1f} SD vs baseline"
    if good <= -1.0:
        return "serious", f"{z:+.1f} SD vs baseline"
    if good <= -0.5:
        return "warning", f"{z:+.1f} SD vs baseline"
    return "good", f"{z:+.1f} SD vs baseline"


def last_date() -> date:
    df = query("SELECT max(date) AS d FROM analytics.daily_features")
    d = df["d"].iloc[0] if not df.empty else None
    return d if d is not None and not pd.isna(d) else date.today()


def date_window(days: int) -> tuple[date, date]:
    end = last_date()
    return end - timedelta(days=days), end
