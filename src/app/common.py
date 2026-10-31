"""Thành phần dùng chung cho dashboard Streamlit."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from olap import connect  # noqa: E402

PALETTE = ["#1f4e79", "#2e86ab", "#f18f01", "#c73e1d", "#3b8b5a", "#7d5ba6", "#8c8c8c", "#e0a458"]


def setup(title: str, icon: str = "📦") -> None:
    st.set_page_config(page_title=f"{title} · Olist DW & DSS", page_icon=icon, layout="wide")
    st.markdown("""
        <style>
          .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
          div[data-testid="stMetricValue"] {font-size: 1.55rem;}
          .note {color:#5b6670; font-size:0.88rem;}
        </style>""", unsafe_allow_html=True)
    st.title(title)


@st.cache_resource
def _con():
    return connect(read_only=True)


@st.cache_data(show_spinner=False, ttl=3600)
def q(sql: str, params: tuple | None = None) -> pd.DataFrame:
    cur = _con().cursor()
    return cur.execute(sql, list(params) if params else None).df()


def brl(x: float) -> str:
    if x >= 1e6:
        return f"R$ {x / 1e6:,.2f} tr"
    if x >= 1e3:
        return f"R$ {x / 1e3:,.1f} N"
    return f"R$ {x:,.0f}"


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}%"


def sql_in(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)
