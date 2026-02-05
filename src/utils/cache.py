"""Streamlit 캐싱 유틸리티 — 파일 업로드 및 샘플 데이터 캐시."""

from __future__ import annotations

import streamlit as st

from src.data.data_loader import load_sample_records
from src.data.models import CapacityRecord


@st.cache_data(show_spinner=False)
def get_sample_records() -> list[dict]:
    """샘플 데이터를 캐싱하여 반환.

    st.cache_data는 직렬화 가능한 타입만 캐싱하므로
    CapacityRecord를 dict로 변환하여 저장한다.
    """
    records = load_sample_records()
    return [r.model_dump() for r in records]


def get_cached_sample_records() -> list[CapacityRecord]:
    """캐시된 샘플 데이터를 CapacityRecord 리스트로 반환."""
    raw = get_sample_records()
    return [CapacityRecord(**d) for d in raw]
