"""메인 대시보드 — 결과 테이블 + 요약 통계."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from src.core.exceptions import HistoryDBError
from src.data.history_db import HistoryRepository
from src.ui.components import capacity_emoji, capacity_label, format_capacity

if TYPE_CHECKING:
    from src.data.models import CapacityRecord


def render_summary_metrics(records: list[CapacityRecord]) -> None:
    """조회 결과 요약 메트릭 (총 선로 수, 연계 가능/불가 수)."""
    total = len(records)
    connectable = sum(1 for r in records if r.is_connectable)
    not_connectable = total - connectable

    col1, col2, col3 = st.columns(3)
    col1.metric("총 배전선로", f"{total}개")
    col2.metric("연계 가능", f"{connectable}개")
    col3.metric(
        "연계 불가",
        f"{not_connectable}개",
        delta=f"-{not_connectable}" if not_connectable else None,
        delta_color="inverse",
    )


def records_to_dataframe(records: list[CapacityRecord]) -> pd.DataFrame:
    """CapacityRecord 리스트를 표시용 DataFrame으로 변환."""
    rows = []
    for r in records:
        min_cap = r.min_capacity
        rows.append(
            {
                "상태": f"{capacity_emoji(min_cap)} {capacity_label(min_cap)}",
                "변전소": r.subst_nm,
                "변압기": r.mtr_no,
                "DL명": r.dl_nm,
                "DL용량(kW)": r.js_dl_pwr,
                "변전소 여유(kW)": format_capacity(r.substation_capacity),
                "변압기 여유(kW)": format_capacity(r.transformer_capacity),
                "DL 여유(kW)": format_capacity(r.dl_capacity),
                "최소 여유(kW)": min_cap,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("최소 여유(kW)", ascending=True).reset_index(drop=True)
    return df


def render_result_table(records: list[CapacityRecord]) -> None:
    """조회 결과를 테이블로 렌더링."""
    if not records:
        st.info("조회 결과가 없습니다. 다른 지역을 선택해보세요.")
        return

    render_summary_metrics(records)
    st.divider()

    df = records_to_dataframe(records)
    values = df["최소 여유(kW)"].tolist() if not df.empty else []
    max_value: int = int(max(values)) if values else 10000
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "최소 여유(kW)": st.column_config.ProgressColumn(
                "최소 여유(kW)",
                min_value=0,
                max_value=max_value,
                format="%d kW",
            ),
        },
    )


def render_history_panel(limit: int = 20) -> None:
    st.subheader("📜 조회 이력")

    try:
        repo = HistoryRepository()
        rows = repo.list_recent(limit=limit)
    except HistoryDBError:
        st.info("조회 이력을 불러올 수 없습니다.")
        return

    if not rows:
        st.info("아직 조회 이력이 없습니다.")
        return

    for row in rows:
        timestamp = row.queried_at.strftime("%Y-%m-%d %H:%M")
        label = f"{timestamp} · {row.region_name}"
        st.write(f"{label} — 결과 {row.result_count}건")
