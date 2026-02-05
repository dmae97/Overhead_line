"""한전 배전선로 여유용량 스캐너 — Streamlit 메인 앱.

API 키 없이 동작한다.
- 샘플 데이터: 내장된 예제 데이터로 즉시 사용 가능
- 파일 업로드: 한전ON에서 다운로드한 CSV/Excel/JSON 파일을 업로드하여 분석
"""

from __future__ import annotations

import logging
from datetime import datetime

import streamlit as st

from src.core.config import settings
from src.data.data_loader import load_records_from_uploaded_file
from src.data.history_db import HistoryRepository
from src.data.models import CapacityRecord, QueryHistoryRecord
from src.ui.charts import render_capacity_bar_chart, render_capacity_breakdown_chart
from src.ui.dashboard import render_history_panel, render_result_table
from src.ui.group_view import render_substation_group_view
from src.utils.cache import get_cached_sample_records
from src.utils.export import render_download_buttons

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _render_data_source_selector() -> list[CapacityRecord] | None:
    """사이드바에 데이터 소스 선택 UI를 렌더링하고 레코드를 반환."""
    st.sidebar.header("📂 데이터 소스")

    data_mode = st.sidebar.radio(
        "데이터 선택",
        options=["📊 샘플 데이터", "📁 파일 업로드"],
        index=0,
        help="샘플 데이터로 즉시 시작하거나, 한전ON에서 다운로드한 파일을 업로드하세요.",
    )

    if data_mode == "📊 샘플 데이터":
        st.sidebar.caption(
            "💡 내장된 샘플 데이터입니다.\n"
            "실제 데이터는 [한전ON](https://online.kepco.co.kr)에서 다운로드 후 업로드하세요."
        )
        return get_cached_sample_records()

    # 파일 업로드 모드
    uploaded_file = st.sidebar.file_uploader(
        "CSV / Excel / JSON 파일 업로드",
        type=["csv", "xlsx", "xls", "json"],
        help="한전ON 또는 전력데이터 개방포털에서 다운로드한 파일을 업로드하세요.",
    )

    if uploaded_file is None:
        st.sidebar.info("👆 파일을 업로드하면 분석이 시작됩니다.")
        return None

    # 업로드된 파일 처리
    file_bytes = uploaded_file.read()
    records = load_records_from_uploaded_file(file_bytes, uploaded_file.name)

    if not records:
        st.sidebar.error("파일에서 유효한 데이터를 찾을 수 없습니다.")
        st.sidebar.caption(
            "지원 컬럼명: substNm/변전소명, dlNm/DL명, "
            "vol1/변전소여유, vol2/변압기여유, vol3/DL여유 등"
        )
        return None

    st.sidebar.success(f"✅ {len(records)}건의 데이터를 로드했습니다.")
    return records


def main() -> None:
    st.set_page_config(
        page_title="⚡ 한전 선로용량 스캐너",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("⚡ 한전 배전선로 여유용량 스캐너")
    st.caption("태양광 발전사업 계통연계 가능 여부를 빠르게 확인하세요.")

    records = _render_data_source_selector()

    if records is None:
        st.info("👈 사이드바에서 데이터 소스를 선택해주세요.")
        return

    if not records:
        st.warning("데이터가 비어 있습니다.")
        return

    # 데이터 소스명 결정
    data_label = (
        "샘플 데이터" if st.session_state.get("_data_mode") != "upload" else "업로드 데이터"
    )

    st.subheader(f"📊 분석 결과 ({len(records)}건)")

    # 결과를 session_state에 저장
    st.session_state["last_records"] = records

    render_result_table(records)

    st.divider()

    tab1, tab2, tab3 = st.tabs(["📊 최소 여유용량", "📈 레벨별 비교", "🏭 변전소별 그룹핑"])
    with tab1:
        render_capacity_bar_chart(records)
    with tab2:
        render_capacity_breakdown_chart(records)
    with tab3:
        render_substation_group_view(records)

    st.divider()
    render_download_buttons(records, region_name=data_label)

    st.divider()

    # 조회 이력 저장
    try:
        repo = HistoryRepository()
        repo.save(
            QueryHistoryRecord(
                region_name=data_label,
                metro_cd="",
                city_cd="",
                dong="",
                result_count=len(records),
                queried_at=datetime.now(),
            )
        )
    except Exception:
        logger.warning("조회 이력 저장 실패", exc_info=True)

    render_history_panel()


if __name__ == "__main__":
    main()
