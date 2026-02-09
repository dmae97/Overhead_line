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

    st.caption(
        "가독성을 위해 변전소/변압기 필터를 제공하고, 노드 드래그(움직임)는 기본으로 막습니다."
    )

    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
        s = (hex_color or "").strip()
        if not s.startswith("#") or len(s) != 7:
            return f"rgba(100,116,139,{alpha})"
        r = int(s[1:3], 16)
        g = int(s[3:5], 16)
        b = int(s[5:7], 16)
        a = max(0.0, min(1.0, float(alpha)))
        return f"rgba({r},{g},{b},{a})"

    # -----------------------------
    # 필터/표시 설정
    # -----------------------------
    subst_list = sorted(
        {(r.subst_nm or r.subst_cd or "").strip() for r in records if r.subst_nm or r.subst_cd}
    )
    subst_options = ["전체"] + subst_list
    default_subst = subst_list[0] if len(subst_list) > 6 and subst_list else "전체"
    subst_idx = subst_options.index(default_subst) if default_subst in subst_options else 0
    selected_subst = st.selectbox("변전소 필터", options=subst_options, index=subst_idx)

    filtered = (
        [r for r in records if (r.subst_nm or r.subst_cd or "").strip() == selected_subst]
        if selected_subst != "전체"
        else records
    )

    mtr_list = sorted({(r.mtr_no or "").strip() for r in filtered if r.mtr_no})
    mtr_options = ["전체"] + mtr_list
    selected_mtr = st.selectbox("변압기 필터", options=mtr_options, index=0)
    if selected_mtr != "전체":
        filtered = [r for r in filtered if (r.mtr_no or "").strip() == selected_mtr]

    # 너무 많은 DL은 가독성을 위해 상한 적용
    dl_keys_all: dict[tuple[str, str, str], int] = {}
    for r in filtered:
        subst_key = (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)"
        mtr_key = (r.mtr_no or "").strip() or "(unknown-mtr)"
        dl_id = (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)"
        key = (subst_key, mtr_key, dl_id)
        dl_keys_all[key] = min(dl_keys_all.get(key, 10**12), int(r.min_capacity))

    dl_total = len(dl_keys_all)
    max_dl_default = 60 if dl_total > 60 else max(10, dl_total)
    max_dl = st.slider(
        "DL 표시 상한",
        min_value=10,
        max_value=max(10, min(300, dl_total or 10)),
        value=max_dl_default,
        step=10,
        help="DL이 많으면 선이 겹쳐 시인성이 떨어집니다.",
    )
    sort_mode = st.radio(
        "DL 선택 기준",
        options=["최소여유 낮은순", "최소여유 높은순"],
        index=0,
        horizontal=True,
    )
    show_dl_labels = st.checkbox("DL 라벨 표시", value=dl_total <= 30)

    if dl_total > max_dl:
        reverse = sort_mode == "최소여유 높은순"
        dl_sorted = sorted(dl_keys_all.items(), key=lambda x: x[1], reverse=reverse)
        keep = {k for k, _ in dl_sorted[: int(max_dl)]}
        filtered = [
            r
            for r in filtered
            if (
                (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)",
                (r.mtr_no or "").strip() or "(unknown-mtr)",
                (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)",
            )
            in keep
        ]

    if not filtered:
        st.info("필터 조건에서 표시할 데이터가 없습니다.")
        return

    # -----------------------------
    # 노드 생성(정렬 고정)
    # -----------------------------
    def _subst_key(r: CapacityRecord) -> str:
        return (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)"

    def _mtr_key(r: CapacityRecord) -> str:
        return (r.mtr_no or "").strip() or "(unknown-mtr)"

    def _dl_key(r: CapacityRecord) -> str:
        return (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)"

    rows = sorted(filtered, key=lambda r: (_subst_key(r), _mtr_key(r), _dl_key(r), r.min_capacity))

    subst_keys = sorted({_subst_key(r) for r in rows})
    mtr_keys = sorted({(_subst_key(r), _mtr_key(r)) for r in rows})
    dl_keys = sorted({(_subst_key(r), _mtr_key(r), _dl_key(r)) for r in rows})

    labels: list[str] = []
    colors: list[str] = []
    xs: list[float] = []
    ys: list[float] = []

    subst_nodes: dict[str, int] = {}
    mtr_nodes: dict[tuple[str, str], int] = {}
    dl_nodes: dict[tuple[str, str, str], int] = {}

    def _y(i: int, n: int) -> float:
        if n <= 1:
            return 0.5
        return float(i) / float(n - 1)

    def _add_node(label: str, color: str, x: float, y: float) -> int:
        idx = len(labels)
        labels.append(label)
        colors.append(color)
        xs.append(x)
        ys.append(y)
        return idx

    # 변전소(좌측)
    for i, s in enumerate(subst_keys):
        subst_nodes[s] = _add_node(f"🏭 {s}", "#0f172a", 0.02, _y(i, len(subst_keys)))

    # 변압기(중간)
    for i, (s, m) in enumerate(mtr_keys):
        mtr_nodes[(s, m)] = _add_node(f"🔌 {m}", "#334155", 0.46, _y(i, len(mtr_keys)))

    # DL(우측)
    dl_caps: dict[tuple[str, str, str], int] = {}
    for r in rows:
        key = (_subst_key(r), _mtr_key(r), _dl_key(r))
        dl_caps[key] = min(dl_caps.get(key, 10**12), int(r.min_capacity))

    for i, (s, m, d) in enumerate(dl_keys):
        cap = int(dl_caps.get((s, m, d), 0))
        dl_label = f"⚡ {d}" if show_dl_labels else ""
        dl_nodes[(s, m, d)] = _add_node(dl_label, capacity_color(cap), 0.93, _y(i, len(dl_keys)))

    sources: list[int] = []
    targets: list[int] = []
    values: list[int] = []
    link_colors: list[str] = []

    # 링크 생성(정렬 고정)
    for r in rows:
        s = _subst_key(r)
        m = _mtr_key(r)
        d = _dl_key(r)

        s_idx = subst_nodes[s]
        m_idx = mtr_nodes[(s, m)]
        d_idx = dl_nodes[(s, m, d)]

        # 변전소 -> 변압기
        sources.append(s_idx)
        targets.append(m_idx)
        values.append(1)
        link_colors.append("rgba(148,163,184,0.25)")

        # 변압기 -> DL (용량 색)
        cap = int(r.min_capacity)
        sources.append(m_idx)
        targets.append(d_idx)
        values.append(1)
        link_colors.append(_hex_to_rgba(capacity_color(cap), 0.55))

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="fixed",
                node=dict(
                    pad=10,
                    thickness=14,
                    line=dict(color="rgba(0,0,0,0.20)", width=0.8),
                    label=labels,
                    color=colors,
                    x=xs,
                    y=ys,
                ),
                link=dict(
                    source=sources,
                    target=targets,
                    value=values,
                    color=link_colors,
                ),
            )
        ]
    )

    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=720,
        font=dict(size=12, color="#0f172a"),
        uirevision="sankey-fixed",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
