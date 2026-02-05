"""Streamlit 캐싱 유틸리티.

- OpenAPI 실시간 조회 결과: 5분 TTL 캐시
"""

from __future__ import annotations

from src.data.kepco_api import KepcoApiClient
from src.data.models import AddressParams, CapacityRecord


def fetch_capacity_cached(params: AddressParams) -> list[CapacityRecord]:
    """한전 OpenAPI 호출 결과를 캐시하여 반환 (TTL 5분)."""

    try:
        import streamlit as st

        @st.cache_data(ttl=300, show_spinner=False)
        def _fetch(
            metro_cd: str,
            city_cd: str,
            dong: str,
            ri: str,
            jibun: str,
        ) -> list[dict]:
            client = KepcoApiClient()
            try:
                records = client.fetch_capacity(
                    AddressParams(metro_cd=metro_cd, city_cd=city_cd, dong=dong, ri=ri, jibun=jibun)
                )
            finally:
                client.close()
            return [r.model_dump() for r in records]

        raw = _fetch(params.metro_cd, params.city_cd, params.dong, params.ri, params.jibun)
        return [CapacityRecord(**d) for d in raw]
    except Exception:
        # Streamlit 런타임 외부(테스트/CLI)에서는 캐시 없이 실행
        client = KepcoApiClient()
        try:
            return client.fetch_capacity(params)
        finally:
            client.close()
