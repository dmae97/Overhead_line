"""address 모듈 단위 테스트."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.core.exceptions import AddressDataError
from src.data.address import to_kepco_params
from src.data.models import RegionInfo


def _mock_bdong_df() -> pd.DataFrame:
    # 최소 컬럼만 포함해 to_kepco_params 로직을 검증한다.
    return pd.DataFrame(
        [
            {
                "시도코드": "44",
                "시도명": "충청남도",
                "시군구코드": "44131",
                "시군구명": "천안시 동남구",
                "말소일자": "",
            },
            # 세종시는 시군구명이 비어있는 케이스를 처리한다.
            {
                "시도코드": "36",
                "시도명": "세종특별자치시",
                "시군구코드": "36110",
                "시군구명": "",
                "말소일자": "",
            },
        ]
    )


def test_to_kepco_params_normal_region() -> None:
    df = _mock_bdong_df()
    with patch("src.data.address.load_bdong_codes", return_value=df):
        params = to_kepco_params(
            RegionInfo(sido="충청남도", sigungu="천안시 동남구", dong="광덕면")
        )
        assert params.metro_cd == "44"
        assert params.city_cd == "131"
        assert params.dong == "광덕면"


def test_to_kepco_params_all_dong_to_empty() -> None:
    df = _mock_bdong_df()
    with patch("src.data.address.load_bdong_codes", return_value=df):
        params = to_kepco_params(RegionInfo(sido="충청남도", sigungu="천안시 동남구", dong="전체"))
        assert params.dong == ""


def test_to_kepco_params_sigungu_missing_case_sejong() -> None:
    df = _mock_bdong_df()
    with patch("src.data.address.load_bdong_codes", return_value=df):
        params = to_kepco_params(
            RegionInfo(sido="세종특별자치시", sigungu="세종특별자치시", dong="조치원읍")
        )
        assert params.metro_cd == "36"
        assert params.city_cd == "110"
        assert params.dong == "조치원읍"


def test_to_kepco_params_missing_region_raises() -> None:
    df = _mock_bdong_df()
    with patch("src.data.address.load_bdong_codes", return_value=df), pytest.raises(
        AddressDataError
    ):
        to_kepco_params(RegionInfo(sido="없는시도", sigungu="없는시군구", dong="전체"))
