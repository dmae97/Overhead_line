"""Plotly 기반 시각화 차트."""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.ui.components import capacity_color

if TYPE_CHECKING:
    from src.data.models import CapacityRecord


def render_capacity_bar_chart(records: list[CapacityRecord]) -> None:
    """배전선로별 여유용량 수평 바 차트."""
    if not records:
        return

    sorted_records = sorted(records, key=lambda r: r.min_capacity)

    dl_names = [f"{r.subst_nm} / {r.dl_nm}" for r in sorted_records]
    min_caps = [r.min_capacity for r in sorted_records]
    colors = [capacity_color(c) for c in min_caps]

    fig = go.Figure(
        go.Bar(
            x=min_caps,
            y=dl_names,
            orientation="h",
            marker_color=colors,
            text=[f"{c:,} kW" for c in min_caps],
            textposition="auto",
        )
    )

    fig.update_layout(
        title="배전선로별 최소 여유용량",
        xaxis_title="여유용량 (kW)",
        yaxis_title="",
        height=max(300, len(records) * 35),
        margin=dict(l=10, r=10, t=40, b=30),
    )

    st.plotly_chart(fig, use_container_width=True)


def render_capacity_breakdown_chart(records: list[CapacityRecord]) -> None:
    """변전소/변압기/DL 3레벨 여유용량 비교 그룹 바 차트."""
    if not records:
        return

    import pandas as pd

    rows = []
    for r in records:
        label = f"{r.subst_nm}/{r.dl_nm}"
        rows.append({"선로": label, "구분": "변전소", "여유용량(kW)": r.substation_capacity})
        rows.append({"선로": label, "구분": "변압기", "여유용량(kW)": r.transformer_capacity})
        rows.append({"선로": label, "구분": "DL", "여유용량(kW)": r.dl_capacity})

    df = pd.DataFrame(rows)

    fig = px.bar(
        df,
        x="여유용량(kW)",
        y="선로",
        color="구분",
        orientation="h",
        barmode="group",
        color_discrete_map={"변전소": "#4e79a7", "변압기": "#f28e2b", "DL": "#e15759"},
        height=max(400, len(records) * 50),
    )

    fig.update_layout(
        title="변전소/변압기/DL 여유용량 비교",
        margin=dict(l=10, r=10, t=40, b=30),
    )

    st.plotly_chart(fig, use_container_width=True)
