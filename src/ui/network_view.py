"""선로 연결도(계층 그래프) UI.

지도 기반 '실제 선로 경로'는 현재 데이터로는 제공 불가하므로,
변전소→변압기→DL 구조를 그래프로 보여준다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
import streamlit as st

from src.ui.components import capacity_color

if TYPE_CHECKING:
    from src.data.models import CapacityRecord


def render_hierarchy_sankey(records: list[CapacityRecord]) -> None:
    """변전소→변압기→DL 연결을 Sankey로 시각화."""
    st.subheader("🔗 선로 연결도(변전소→변압기→DL)")
    if not records:
        st.info("표시할 선로 데이터가 없습니다.")
        return

    # 노드: subst, mtr, dl
    subst_nodes: dict[str, int] = {}
    mtr_nodes: dict[tuple[str, str], int] = {}
    dl_nodes: dict[tuple[str, str, str], int] = {}

    labels: list[str] = []
    colors: list[str] = []

    def _add_node(key: object, label: str, color: str) -> int:
        idx = len(labels)
        labels.append(label)
        colors.append(color)
        return idx

    # 먼저 노드 생성
    for r in records:
        subst_key = r.subst_cd or r.subst_nm
        if subst_key not in subst_nodes:
            subst_nodes[subst_key] = _add_node(
                subst_key, f"🏭 {r.subst_nm or r.subst_cd}", "#94a3b8"
            )

        mtr_key = (subst_key, r.mtr_no)
        if mtr_key not in mtr_nodes:
            mtr_nodes[mtr_key] = _add_node(mtr_key, f"🔌 {r.mtr_no}", "#cbd5e1")

        dl_key = (subst_key, r.mtr_no, r.dl_cd or r.dl_nm)
        if dl_key not in dl_nodes:
            cap = int(r.min_capacity)
            dl_nodes[dl_key] = _add_node(
                dl_key,
                f"⚡ {r.dl_nm} ({cap:,}kW)",
                capacity_color(cap),
            )

    sources: list[int] = []
    targets: list[int] = []
    values: list[int] = []

    # 링크 생성
    for r in records:
        subst_key = r.subst_cd or r.subst_nm
        s_idx = subst_nodes[subst_key]

        mtr_key = (subst_key, r.mtr_no)
        m_idx = mtr_nodes[mtr_key]

        dl_key = (subst_key, r.mtr_no, r.dl_cd or r.dl_nm)
        d_idx = dl_nodes[dl_key]

        # 중복 링크 허용(가중치 증가)
        sources.append(s_idx)
        targets.append(m_idx)
        values.append(1)

        sources.append(m_idx)
        targets.append(d_idx)
        values.append(1)

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                node=dict(
                    pad=12,
                    thickness=14,
                    line=dict(color="rgba(0,0,0,0.15)", width=0.8),
                    label=labels,
                    color=colors,
                ),
                link=dict(
                    source=sources, target=targets, value=values, color="rgba(100,116,139,0.35)"
                ),
            )
        ]
    )

    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=520)
    st.plotly_chart(fig, use_container_width=True)
