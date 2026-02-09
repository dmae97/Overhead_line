"""PublicDataReader 기반 주소 데이터 관리 모듈.

법정동코드를 로드하고 시도/시군구/읍면동 목록을 제공하며,
사용자 선택을 한전 API 파라미터로 변환한다.

핵심 API:
- pdr.code_bdong() → 법정동코드 전체 DataFrame (인증키 불필요)

법정동코드 DataFrame 컬럼:
  시도코드(str), 시도명(str), 시군구코드(str), 시군구명(str),
  법정동코드(str), 읍면동명(str), 동리명(str), 생성일자(str), 말소일자(str)

주의:
- 말소일자가 비어있는 행만 현행(유효) 법정동
- 시군구코드는 5자리 (시도코드 2자리 + 시군구 고유 3자리)
- 한전 API의 cityCd는 시군구코드의 뒤 3자리
"""

from __future__ import annotations

import logging
from typing import Any, cast

import pandas as pd
import PublicDataReader
import streamlit as st

from src.core.exceptions import AddressDataError
from src.data.models import AddressParams, RegionInfo

logger = logging.getLogger(__name__)


@st.cache_data(ttl=86400, show_spinner="법정동코드 로딩 중...")
def load_bdong_codes() -> pd.DataFrame:
    """법정동코드 전체 로드 (현행 데이터만, 24시간 캐시)."""
    try:
        raw = PublicDataReader.code_bdong()
        df = cast("pd.DataFrame", raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw))

        if "말소일자" not in df.columns:
            raise AddressDataError("법정동코드 데이터에 '말소일자' 컬럼이 없습니다.")

        active = cast("pd.DataFrame", df[df["말소일자"].isna() | (df["말소일자"] == "")].copy())
        logger.info("법정동코드 로드 완료: %d건 (현행)", len(active))
        return active
    except Exception as e:
        raise AddressDataError(f"법정동코드 로드 실패: {e}") from e


def get_sido_list() -> list[str]:
    """시/도 목록 반환 (정렬)."""
    df = load_bdong_codes()
    col = cast("Any", df["시도명"])
    return sorted(col.dropna().unique().tolist())


def get_sigungu_list(sido_name: str) -> list[str]:
    """선택한 시/도 내 시/군/구 목록 반환 (정렬).

    시군구가 없는 광역시/특별자치시(세종시 등)는 시도명을 반환한다.
    """
    df = load_bdong_codes()
    filtered = cast("pd.DataFrame", df[df["시도명"] == sido_name])
    col = cast("Any", filtered["시군구명"])
    result = col.dropna().unique().tolist()
    result = sorted([s for s in result if s])

    # 시군구가 없으면 시도명을 시군구로 사용 (세종시 등)
    if not result:
        return [sido_name]
    return result


def get_dong_list(sido_name: str, sigungu_name: str) -> list[str]:
    """선택한 시/군/구 내 읍/면/동 목록 반환 (정렬)."""
    df = load_bdong_codes()
    # 세종시 등 시군구가 없는 케이스는 시군구명이 빈 행이므로 별도 처리
    if sigungu_name == sido_name:
        filtered = cast("pd.DataFrame", df[(df["시도명"] == sido_name) & (df["시군구명"] == "")])
    else:
        filtered = cast(
            "pd.DataFrame", df[(df["시도명"] == sido_name) & (df["시군구명"] == sigungu_name)]
        )
    col = cast("Any", filtered["읍면동명"])
    result = col.dropna().unique().tolist()
    return sorted([d for d in result if d])


def get_ri_list(sido_name: str, sigungu_name: str, dong_name: str) -> list[str]:
    """선택한 읍/면/동 내 리 목록 반환 (정렬).

    도심(동) 지역은 리가 없을 수 있으며, 그 경우 빈 리스트를 반환한다.
    """
    if not dong_name or dong_name == "전체":
        return []

    df = load_bdong_codes()
    if sigungu_name == sido_name:
        filtered = cast("pd.DataFrame", df[(df["시도명"] == sido_name) & (df["시군구명"] == "")])
    else:
        filtered = cast(
            "pd.DataFrame", df[(df["시도명"] == sido_name) & (df["시군구명"] == sigungu_name)]
        )

    filtered = cast("pd.DataFrame", filtered[filtered["읍면동명"] == dong_name])
    col = cast("Any", filtered["동리명"])
    result = col.dropna().unique().tolist()
    return sorted([r for r in result if r and r != "전체"])


def to_kepco_params(region: RegionInfo) -> AddressParams:
    """지역 선택 정보를 한전 API 파라미터로 변환.

    법정동코드 체계:
    - metroCd = 시도코드 앞 2자리
    - cityCd  = 시군구코드 뒤 3자리

    시군구가 없는 광역시/특별자치시(세종시 등)는 시군구명이 비어있으므로
    시도명만으로 매칭한다.
    """
    df = load_bdong_codes()

    # 시군구가 시도명과 같으면 (세종시 등) 시군구명이 빈 행을 찾음
    if region.sido == region.sigungu:
        matched = cast("pd.DataFrame", df[(df["시도명"] == region.sido) & (df["시군구명"] == "")])
        # 36000 같은 시도 전체 코드 제외, 36110 같은 실제 행정구역만
        codes = cast("Any", matched["시군구코드"]).astype(str)
        mask = codes.apply(lambda x: str(x).endswith("110"))
        matched = cast("pd.DataFrame", matched[mask])
    else:
        matched = cast(
            "pd.DataFrame", df[(df["시도명"] == region.sido) & (df["시군구명"] == region.sigungu)]
        )

    if matched.empty:
        raise AddressDataError(
            f"'{region.sido} {region.sigungu}'에 해당하는 법정동코드를 찾을 수 없습니다."
        )

    row = matched.iloc[0]
    sido_cd = str(row["시도코드"]).zfill(2)
    sigungu_cd = str(row["시군구코드"]).zfill(5)

    dong = region.dong if region.dong and region.dong != "전체" else ""
    ri = region.ri if dong and region.ri and region.ri != "전체" else ""

    return AddressParams(
        metro_cd=sido_cd,
        city_cd=sigungu_cd[2:],
        dong=dong,
        ri=ri,
    )
