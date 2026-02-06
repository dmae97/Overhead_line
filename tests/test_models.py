"""Pydantic 모델 단위 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.data.models import AddressParams, CapacityRecord, RegionInfo


class TestCapacityRecord:
    def test_from_api_response(self) -> None:
        record = CapacityRecord(
            substCd="S001",
            substNm="천안",
            jsSubstPwr="50000",
            substPwr="30000",
            mtrNo="#1",
            jsMtrPwr="20000",
            mtrPwr="10000",
            dlCd="D001",
            dlNm="불당1",
            jsDlPwr="10000",
            dlPwr="6800",
            vol1="20000",
            vol2="10000",
            vol3="3200",
        )
        assert record.subst_nm == "천안"
        assert record.dl_nm == "불당1"
        assert record.substation_capacity == 20000
        assert record.transformer_capacity == 10000
        assert record.dl_capacity == 3200
        assert record.min_capacity == 3200
        assert record.is_connectable is True

    def test_accepts_numeric_fields(self) -> None:
        """실제 OpenAPI는 숫자 필드를 int/float로 반환할 수 있다."""
        record = CapacityRecord(
            substCd=2462,
            substNm="공주",
            dlCd=16,
            dlNm="정안",
            jsSubstPwr=0,
            substPwr=0,
            jsMtrPwr=0,
            mtrPwr=0,
            jsDlPwr=0,
            dlPwr=0,
            vol1=98973,
            vol2=0,
            vol3=1199,
        )
        assert record.subst_cd == "2462"
        assert record.dl_cd == "16"
        assert record.substation_capacity == 98973
        assert record.transformer_capacity == 0
        assert record.dl_capacity == 1199
        assert record.min_capacity == 0
        assert record.is_connectable is False

    def test_zero_capacity_not_connectable(self) -> None:
        record = CapacityRecord(vol1="0", vol2="5000", vol3="3000")
        assert record.min_capacity == 0
        assert record.is_connectable is False

    def test_all_zero(self) -> None:
        record = CapacityRecord()
        assert record.min_capacity == 0
        assert record.is_connectable is False

    def test_invalid_vol_defaults_to_zero(self) -> None:
        record = CapacityRecord(vol1="abc", vol2="", vol3="NaN")
        assert record.substation_capacity == 0
        assert record.transformer_capacity == 0
        assert record.dl_capacity == 0

    def test_float_vol_truncated(self) -> None:
        record = CapacityRecord(vol1="3200.7", vol2="1500.3", vol3="200.9")
        assert record.substation_capacity == 3200
        assert record.transformer_capacity == 1500
        assert record.dl_capacity == 200


class TestAddressParams:
    def test_required_fields(self) -> None:
        params = AddressParams(metro_cd="44", city_cd="131")
        assert params.metro_cd == "44"
        assert params.city_cd == "131"
        assert params.dong == ""
        assert params.ri == ""
        assert params.jibun == ""

    def test_with_dong(self) -> None:
        params = AddressParams(metro_cd="44", city_cd="131", dong="불당동")
        assert params.dong == "불당동"

    def test_missing_required_raises(self) -> None:
        with pytest.raises(ValidationError):
            AddressParams()  # type: ignore[call-arg]


class TestRegionInfo:
    def test_display_name_full(self) -> None:
        region = RegionInfo(sido="충청남도", sigungu="천안시 서북구", dong="불당동")
        assert region.display_name == "충청남도 천안시 서북구 불당동"

    def test_display_name_all(self) -> None:
        region = RegionInfo(sido="충청남도", sigungu="천안시 서북구", dong="전체")
        assert region.display_name == "충청남도 천안시 서북구"

    def test_default_dong(self) -> None:
        region = RegionInfo(sido="서울특별시", sigungu="강남구")
        assert region.dong == "전체"
