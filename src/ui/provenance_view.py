"""실데이터(출처/원본) 표시 UI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import streamlit as st

from src.core.config import settings

if TYPE_CHECKING:
    from src.data.models import CapacityRecord


def _safe_meta(meta: Any) -> dict[str, Any]:
    if isinstance(meta, dict):
        return meta
    return {}


def render_provenance(records: list[CapacityRecord], meta: Any) -> None:
    """현재 화면의 데이터 출처/원본을 보여준다."""
    st.subheader("🧾 실데이터 / 원본")

    meta_dict = _safe_meta(meta)
    mode = str(meta_dict.get("mode") or "")

    if mode == "api":
        st.success("데이터 소스: 한전 전력데이터 개방포털 OpenAPI (실시간)")
        st.write(f"endpoint: `{settings.kepco_api_base_url}`")
        params = meta_dict.get("params")
        if isinstance(params, dict):
            # apiKey는 절대 출력하지 않는다.
            scrubbed = {k: v for k, v in params.items() if k.lower() != "apikey"}
            st.write("요청 파라미터:")
            st.json(scrubbed)
        st.caption("주의: 이 화면에는 API Key를 표시하지 않습니다.")
    elif mode == "online":
        st.warning("데이터 소스: 한전ON 웹(브라우저 자동화) 조회")
        st.write(f"url: `{settings.kepco_online_url}`")
        region = meta_dict.get("region")
        if isinstance(region, dict):
            st.write("조회 지역:")
            st.json(region)
    elif mode == "upload":
        st.info("데이터 소스: 사용자 업로드 파일")
        filename = str(meta_dict.get("filename") or "")
        if filename:
            st.write(f"file: `{filename}`")
    elif mode == "sample":
        st.info("데이터 소스: 샘플 데이터(데모)")
    else:
        st.info("데이터 소스: 알 수 없음(메타 정보 없음)")

    st.divider()
    st.write("원본 레코드(일부)")
    total = len(records)
    if total <= 1:
        sample_n = total
    else:
        upper = min(50, total)
        try:
            sample_n = st.slider(
                "표시 건수",
                min_value=1,
                max_value=upper,
                value=min(10, upper),
            )
        except Exception:
            sample_n = min(10, total)

    # CapacityRecord는 snake_case + alias 모두 지원하므로, by_alias=True로 원본 키를 보여준다.
    raw = [r.model_dump(by_alias=True) for r in records[: int(sample_n)]]
    st.json(raw)
