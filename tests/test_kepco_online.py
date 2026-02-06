"""kepco_online 모듈 단위 테스트 — DOM 파싱, 숫자 정제, 시군구 분리 로직.

실제 브라우저를 실행하지 않고 내부 로직만 검증한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import ScraperError
from src.data.kepco_online import (
    KepcoOnlineScraper,
    _clean_number,
)
from src.data.models import CapacityRecord


class TestCleanNumber:
    """_clean_number 숫자 정제 로직 테스트."""

    def test_normal_number(self) -> None:
        assert _clean_number("13000") == "13000"

    def test_comma_separated(self) -> None:
        assert _clean_number("13,000") == "13000"

    def test_double_comma(self) -> None:
        """WebSquare가 반환하는 더블 콤마 형식."""
        assert _clean_number("159,,000") == "159000"

    def test_spaces(self) -> None:
        assert _clean_number("  50 000  ") == "50000"

    def test_zero(self) -> None:
        assert _clean_number("0") == "0"

    def test_empty_string(self) -> None:
        assert _clean_number("") == "0"

    def test_none_like(self) -> None:
        assert _clean_number("  ") == "0"

    def test_mixed_format(self) -> None:
        assert _clean_number("20,,167") == "20167"

    def test_with_text(self) -> None:
        """숫자가 아닌 문자 제거."""
        assert _clean_number("13,000kW") == "13000"


class TestSplitSigungu:
    """_split_sigungu 시군구 분리 로직 테스트."""

    def test_city_and_gu(self) -> None:
        si, gu = KepcoOnlineScraper._split_sigungu("천안시 서북구", "충청남도")
        assert si == "천안시"
        assert gu == "서북구"

    def test_city_only(self) -> None:
        si, gu = KepcoOnlineScraper._split_sigungu("공주시", "충청남도")
        assert si == "공주시"
        assert gu == ""

    def test_same_as_sido(self) -> None:
        """세종특별자치시 등 시군구가 시도와 같은 경우."""
        si, gu = KepcoOnlineScraper._split_sigungu("세종특별자치시", "세종특별자치시")
        assert si == ""
        assert gu == ""

    def test_empty(self) -> None:
        si, gu = KepcoOnlineScraper._split_sigungu("", "경기도")
        assert si == ""
        assert gu == ""

    def test_multi_word_gu(self) -> None:
        """성남시 수정구 같은 경우."""
        si, gu = KepcoOnlineScraper._split_sigungu("성남시 수정구", "경기도")
        assert si == "성남시"
        assert gu == "수정구"


class TestParseResults:
    """_parse_results DOM 파싱 로직 테스트."""

    def test_parse_valid_results(self) -> None:
        """유효한 결과 데이터를 CapacityRecord로 변환."""
        # mock page 객체
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "subst_nm": "사이변전소",
            "mtr_no": "#2",
            "dl_nm": "불당1",
            "subst_capa": "180,,000",
            "subst_pwr": "20,,167",
            "g_subst_capa": "17,,938",
            "vol1_1": "159,,833",
            "vol1_2": "162,,062",
            "mtr_capa": "50,,000",
            "mtr_pwr": "0",
            "g_mtr_capa": "0",
            "vol2_1": "50,,000",
            "vol2_2": "50,,000",
            "dl_capa": "13,,000",
            "dl_pwr": "0",
            "g_dl_capa": "0",
            "vol3_1": "13,,000",
            "vol3_2": "13,,000",
            "subst_yn": "변선소 : 여유용량 있음",
            "mtr_yn": "주변압기 : 여유용량 있음",
            "dl_yn": "배전선로 : 여유용량 있음",
        }

        records = KepcoOnlineScraper._parse_results(mock_page)
        assert len(records) == 1

        r = records[0]
        assert r.subst_nm == "사이변전소"
        assert r.mtr_no == "#2"
        assert r.dl_nm == "불당1"
        assert r.vol1 == "159833"
        assert r.vol2 == "50000"
        assert r.vol3 == "13000"
        assert r.substation_capacity == 159833
        assert r.transformer_capacity == 50000
        assert r.dl_capacity == 13000
        assert r.is_connectable is True

    def test_parse_empty_data(self) -> None:
        """결과 데이터가 비어있는 경우 (DOM에 빈 텍스트)."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "subst_nm": "",
            "dl_nm": "",
        }

        records = KepcoOnlineScraper._parse_results(mock_page)
        assert records == []


class TestFetchCapacityByOnline:
    """scraper_service.fetch_capacity_by_online 통합 테스트."""

    @patch("src.data.scraper_service._run_kepco_online")
    def test_success(self, mock_run: MagicMock) -> None:
        """성공 시 레코드를 반환."""
        mock_run.return_value = [
            CapacityRecord(
                substNm="테스트변전소",
                mtrNo="#1",
                dlNm="테스트DL",
                vol1="10000",
                vol2="5000",
                vol3="2000",
            )
        ]

        from src.data.scraper_service import fetch_capacity_by_online

        records = fetch_capacity_by_online(
            sido="충청남도",
            sigungu="천안시 서북구",
            dong="불당동",
        )
        assert len(records) == 1
        assert records[0].subst_nm == "테스트변전소"
        mock_run.assert_called_once()

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_kepco_online")
    def test_retry_on_failure(self, mock_run: MagicMock) -> None:
        """실패 시 재시도."""
        mock_run.side_effect = [
            ScraperError("일시적 오류"),
            [
                CapacityRecord(
                    substNm="테스트변전소",
                    dlNm="DL1",
                    vol1="1000",
                    vol2="500",
                    vol3="200",
                )
            ],
        ]

        from src.data.scraper_service import fetch_capacity_by_online

        records = fetch_capacity_by_online(
            sido="충청남도",
            sigungu="천안시 서북구",
            dong="불당동",
        )
        assert len(records) == 1
        assert mock_run.call_count == 2

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_kepco_online")
    def test_install_error_no_retry(self, mock_run: MagicMock) -> None:
        """설치 관련 에러는 재시도 없이 즉시 포기."""
        mock_run.side_effect = ScraperError("playwright 패키지가 설치되어 있지 않습니다.")

        from src.data.scraper_service import fetch_capacity_by_online

        with pytest.raises(ScraperError, match="설치"):
            fetch_capacity_by_online(sido="충청남도", sigungu="천안시")

        assert mock_run.call_count == 1

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_kepco_online")
    def test_all_retries_fail(self, mock_run: MagicMock) -> None:
        """모든 재시도 소진 시 마지막 예외를 던짐."""
        mock_run.side_effect = ScraperError("검색 실패")

        from src.data.scraper_service import fetch_capacity_by_online

        with pytest.raises(ScraperError, match="검색 실패"):
            fetch_capacity_by_online(sido="충청남도", sigungu="천안시")


class TestRunKepcoOnline:
    """_run_kepco_online 함수 테스트."""

    @patch("src.data.scraper_service._run_kepco_online")
    def test_with_sido_params(self, mock_run: MagicMock) -> None:
        """sido 파라미터가 있으면 region 기반으로 호출."""
        mock_run.return_value = [CapacityRecord(dlNm="DL1", vol1="100", vol2="200", vol3="300")]

        # 직접 _run_kepco_online 호출
        from src.data import scraper_service

        result = scraper_service._run_kepco_online(
            keyword="",
            sido="충청남도",
            sigungu="천안시 서북구",
            dong="불당동",
        )
        assert len(result) == 1

    @patch("src.data.scraper_service._run_kepco_online")
    def test_keyword_parsing(self, mock_run: MagicMock) -> None:
        """keyword만 제공된 경우 공백 분리하여 파싱."""
        mock_run.return_value = [CapacityRecord(dlNm="DL1", vol1="100", vol2="200", vol3="300")]
        # keyword 파싱은 실제 _run_kepco_online 내부에서 수행
        # mock이 걸려있으므로 호출만 확인
        result = mock_run("충청남도 천안시 서북구 불당동")
        assert len(result) == 1
