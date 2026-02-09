"""Pydantic 데이터 모델 모듈

모든 외부 데이터(한전 API 응답, 주소 정보)는 이 모듈의 모델로 검증한다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AddressParams(BaseModel):
    """한전 API 요청 파라미터

    PublicDataReader 법정동코드에서 변환된 한전 API 파라미터.
    """

    metro_cd: str = Field(..., description="시도코드 (2자리)", examples=["44"])
    city_cd: str = Field(..., description="시군구코드 뒤 3자리", examples=["131"])
    dong: str = Field(default="", description="읍면동명")
    ri: str = Field(default="", description="동리명")
    jibun: str = Field(default="", description="상세번지")


class CapacityRecord(BaseModel):
    """단일 배전선로 여유용량 레코드

    한전 API 응답의 개별 레코드를 나타낸다.
    alias를 사용하여 API 응답 필드명과 매핑한다.
    """

    model_config = {
        # Accept both API-style keys (aliases) and internal snake_case keys.
        "populate_by_name": True,
        # KEPCO OpenAPI returns numeric fields as int/float sometimes; coerce to str.
        "coerce_numbers_to_str": True,
    }

    # 변전소 정보
    subst_cd: str = Field(alias="substCd", default="", description="변전소 코드")
    subst_nm: str = Field(alias="substNm", default="", description="변전소명")
    js_subst_pwr: str = Field(alias="jsSubstPwr", default="0", description="변전소 용량 (kW)")
    subst_pwr: str = Field(alias="substPwr", default="0", description="변전소 누적 연계용량 (kW)")

    # 변압기 정보
    mtr_no: str = Field(alias="mtrNo", default="", description="변압기 번호")
    js_mtr_pwr: str = Field(alias="jsMtrPwr", default="0", description="변압기 용량 (kW)")
    mtr_pwr: str = Field(alias="mtrPwr", default="0", description="변압기 누적 연계용량 (kW)")

    # DL(배전선로) 정보
    dl_cd: str = Field(alias="dlCd", default="", description="DL 코드")
    dl_nm: str = Field(alias="dlNm", default="", description="DL명")
    js_dl_pwr: str = Field(alias="jsDlPwr", default="0", description="DL 용량 (kW)")
    dl_pwr: str = Field(alias="dlPwr", default="0", description="DL 누적연계용량 (kW)")

    # 여유용량 (핵심 데이터)
    vol1: str = Field(default="0", description="변전소 여유용량 (kW)")
    vol2: str = Field(default="0", description="변압기 여유용량 (kW)")
    vol3: str = Field(default="0", description="DL 여유용량 (kW)")

    @property
    def substation_capacity(self) -> int:
        """변전소 여유용량 (정수 변환)"""
        try:
            return int(float(self.vol1))
        except (ValueError, TypeError):
            return 0

    @property
    def transformer_capacity(self) -> int:
        """변압기 여유용량 (정수 변환)"""
        try:
            return int(float(self.vol2))
        except (ValueError, TypeError):
            return 0

    @property
    def dl_capacity(self) -> int:
        """DL 여유용량 (정수 변환)"""
        try:
            return int(float(self.vol3))
        except (ValueError, TypeError):
            return 0

    @property
    def min_capacity(self) -> int:
        """3가지 여유용량 중 최소값 (실질적 연계가능 용량)"""
        return min(
            self.substation_capacity,
            self.transformer_capacity,
            self.dl_capacity,
        )

    @property
    def is_connectable(self) -> bool:
        """연계 가능 여부 (모든 여유용량 > 0)"""
        return self.min_capacity > 0


class CapacityResponse(BaseModel):
    """한전 API 응답 전체"""

    data: list[CapacityRecord] = Field(default_factory=list)


class RegionInfo(BaseModel):
    """사용자 지역 선택 정보

    사이드바 Cascading Dropdown에서 선택된 지역 정보.
    """

    sido: str = Field(description="시도명")
    sigungu: str = Field(description="시군구명")
    dong: str = Field(default="전체", description="읍면동명")
    ri: str = Field(default="", description="리명(선택)")

    @property
    def display_name(self) -> str:
        """표시용 지역명"""
        parts = [self.sido, self.sigungu]
        if self.dong and self.dong != "전체":
            parts.append(self.dong)
        if self.ri and self.ri != "전체" and self.dong and self.dong != "전체":
            parts.append(self.ri)
        return " ".join(parts)


class QueryHistoryRecord(BaseModel):
    id: int | None = None
    region_name: str
    metro_cd: str
    city_cd: str
    dong: str = ""
    sigungu: str = ""
    sido: str = ""
    mode: str = ""
    jibun: str = ""
    result_count: int = 0
    connectable_count: int = 0
    not_connectable_count: int = 0
    min_cap_min: int = 0
    min_cap_median: int = 0
    min_cap_max: int = 0
    queried_at: datetime = Field(default_factory=datetime.now)
