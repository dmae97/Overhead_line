"""CSV/Excel 다운로드 유틸리티."""

from __future__ import annotations

import io
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

if TYPE_CHECKING:
    from src.data.models import CapacityRecord


def _records_to_export_df(records: list[CapacityRecord]) -> pd.DataFrame:
    """CapacityRecord 리스트를 내보내기용 DataFrame으로 변환."""
    rows = []
    for r in records:
        rows.append(
            {
                "변전소코드": r.subst_cd,
                "변전소명": r.subst_nm,
                "변전소용량(kW)": r.js_subst_pwr,
                "변전소누적연계(kW)": r.subst_pwr,
                "변전소여유(kW)": r.substation_capacity,
                "변압기번호": r.mtr_no,
                "변압기용량(kW)": r.js_mtr_pwr,
                "변압기누적연계(kW)": r.mtr_pwr,
                "변압기여유(kW)": r.transformer_capacity,
                "DL코드": r.dl_cd,
                "DL명": r.dl_nm,
                "DL용량(kW)": r.js_dl_pwr,
                "DL누적연계(kW)": r.dl_pwr,
                "DL여유(kW)": r.dl_capacity,
                "최소여유(kW)": r.min_capacity,
                "연계가능": "O" if r.is_connectable else "X",
            }
        )
    return pd.DataFrame(rows)


def render_download_buttons(
    records: list[CapacityRecord],
    region_name: str = "",
) -> None:
    """CSV/Excel 다운로드 버튼을 렌더링."""
    if not records:
        return

    df = _records_to_export_df(records)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_base = (
        f"여유용량_{region_name}_{timestamp}" if region_name else f"여유용량_{timestamp}"
    )

    col1, col2 = st.columns(2)

    csv_data = df.to_csv(index=False, encoding="utf-8-sig")
    col1.download_button(
        label="📥 CSV 다운로드",
        data=csv_data,
        file_name=f"{filename_base}.csv",
        mime="text/csv",
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="여유용량")
    col2.download_button(
        label="📥 Excel 다운로드",
        data=buffer.getvalue(),
        file_name=f"{filename_base}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
