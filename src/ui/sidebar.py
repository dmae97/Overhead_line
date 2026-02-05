"""사이드바 — 3단계 Cascading 지역 선택 드롭다운."""

from __future__ import annotations

import streamlit as st

from src.data.address import get_dong_list, get_sido_list, get_sigungu_list
from src.data.models import RegionInfo


def render_region_selector() -> RegionInfo | None:
    """사이드바에 시도/시군구/읍면동 3단계 드롭다운을 렌더링하고 선택 결과를 반환.

    아무것도 선택하지 않았으면 None을 반환한다.
    """
    st.sidebar.header("📍 지역 선택")

    sido_list = get_sido_list()
    if not sido_list:
        st.sidebar.warning("시/도 목록을 불러올 수 없습니다.")
        return None

    selected_sido = st.sidebar.selectbox(
        "시/도",
        options=sido_list,
        index=None,
        placeholder="시/도를 선택하세요",
    )
    if not selected_sido:
        return None

    sigungu_list = get_sigungu_list(selected_sido)
    selected_sigungu = st.sidebar.selectbox(
        "시/군/구",
        options=sigungu_list,
        index=None,
        placeholder="시/군/구를 선택하세요",
    )
    if not selected_sigungu:
        return None

    dong_list = get_dong_list(selected_sido, selected_sigungu)
    selected_dong = st.sidebar.selectbox(
        "읍/면/동",
        options=["전체"] + dong_list,
        index=0,
    )

    return RegionInfo(
        sido=selected_sido,
        sigungu=selected_sigungu,
        dong=selected_dong or "전체",
    )
