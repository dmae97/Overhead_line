from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from src.ui.components import capacity_color, capacity_emoji, capacity_label

if TYPE_CHECKING:
    from src.data.models import CapacityRecord


def group_records_by_substation(
    records: list[CapacityRecord],
) -> dict[str, dict[str, list[CapacityRecord]]]:
    grouped: dict[str, dict[str, list[CapacityRecord]]] = {}
    for record in records:
        subst = record.subst_nm
        mtr = record.mtr_no

        grouped.setdefault(subst, {})
        grouped[subst].setdefault(mtr, [])
        grouped[subst][mtr].append(record)
    return grouped


def render_substation_group_view(records: list[CapacityRecord]) -> None:
    if not records:
        st.info("표시할 데이터가 없습니다.")
        return

    grouped = group_records_by_substation(records)

    sorted_substations = sorted(grouped.items())

    for subst_nm, subst_data in sorted_substations:
        subst_caps = [r.substation_capacity for mtr in subst_data.values() for r in mtr]
        subst_cap = min(subst_caps) if subst_caps else 0
        subst_emoji = capacity_emoji(subst_cap)
        label = f"{subst_emoji} {subst_nm} (변전소 여유: {subst_cap:,} kW)"

        with st.expander(label, expanded=True):
            sorted_transformers = sorted(subst_data.items())

            for mtr_no, dls in sorted_transformers:
                mtr_caps = [r.transformer_capacity for r in dls]
                mtr_cap = min(mtr_caps) if mtr_caps else 0
                mtr_emoji = capacity_emoji(mtr_cap)

                st.markdown(f"**{mtr_emoji} 변압기 {mtr_no}** (여유: {mtr_cap:,} kW)")

                with st.container():
                    for dl in dls:
                        _render_dl_row(dl)

                st.divider()


def _render_dl_row(dl: CapacityRecord) -> None:
    cols = st.columns([3, 2, 2, 2])

    with cols[0]:
        st.text(f"DL: {dl.dl_nm}")
        st.caption(f"({dl.dl_cd})")

    with cols[1]:
        st.metric("DL 여유", f"{dl.dl_capacity:,} kW")

    with cols[2]:
        color = capacity_color(dl.min_capacity)
        st.markdown(f"<span style='color:{color}'>최소 여유</span>", unsafe_allow_html=True)
        st.markdown(f"**{dl.min_capacity:,} kW**")

    with cols[3]:
        label = capacity_label(dl.min_capacity)
        emoji = capacity_emoji(dl.min_capacity)
        st.write(f"{emoji} {label}")
