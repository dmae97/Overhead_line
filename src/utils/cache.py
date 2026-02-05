"""Streamlit 캐싱 유틸리티 — 샘플 데이터 캐시.

st.cache_data 를 함수 내부에서 적용하여,
모듈 임포트 시점의 Streamlit 런타임 의존성을 제거한다.
"""

from __future__ import annotations

from src.data.data_loader import load_sample_records
from src.data.models import CapacityRecord


def get_cached_sample_records() -> list[CapacityRecord]:
    """캐시된 샘플 데이터를 CapacityRecord 리스트로 반환.

    Streamlit 런타임이 존재하면 st.cache_data 를 활용하고,
    런타임 외부(테스트/CLI)에서는 매번 새로 로드한다.
    """
    try:
        import streamlit as st

        @st.cache_data(show_spinner=False)
        def _load() -> list[dict]:
            records = load_sample_records()
            return [r.model_dump() for r in records]

        raw = _load()
    except Exception:
        # Streamlit 런타임 없이 실행되는 경우 (테스트, CLI 등)
        raw = [r.model_dump() for r in load_sample_records()]

    return [CapacityRecord(**d) for d in raw]
