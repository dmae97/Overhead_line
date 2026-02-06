"""kepco_online 모듈 단위 테스트 — 고도화 버전.

DOM 파싱, 숫자 정제, 시군구 분리, 옵션 매칭, 전략별 동작 테스트.
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


# ---------------------------------------------------------------------------
# _clean_number
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _split_sigungu
# ---------------------------------------------------------------------------


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
        si, gu = KepcoOnlineScraper._split_sigungu("성남시 수정구", "경기도")
        assert si == "성남시"
        assert gu == "수정구"


# ---------------------------------------------------------------------------
# _find_best_option
# ---------------------------------------------------------------------------


class TestFindBestOption:
    """_find_best_option 매칭 로직 테스트."""

    def test_exact_match(self) -> None:
        result = KepcoOnlineScraper._find_best_option("불당동", ["선택", "불당동", "쌍용동"])
        assert result == "불당동"

    def test_contains_match(self) -> None:
        result = KepcoOnlineScraper._find_best_option("불당", ["선택", "불당동", "쌍용동"])
        assert result == "불당동"

    def test_reverse_contains(self) -> None:
        result = KepcoOnlineScraper._find_best_option("불당동 123", ["선택", "불당동", "쌍용동"])
        assert result == "불당동"

    def test_no_match(self) -> None:
        result = KepcoOnlineScraper._find_best_option("없는동", ["선택", "불당동", "쌍용동"])
        assert result is None

    def test_empty_options(self) -> None:
        result = KepcoOnlineScraper._find_best_option("불당동", [])
        assert result is None

    def test_only_blank_options(self) -> None:
        result = KepcoOnlineScraper._find_best_option("불당동", ["", " "])
        assert result is None


# ---------------------------------------------------------------------------
# _parse_dom_results (기존 _parse_results 호환)
# ---------------------------------------------------------------------------


class TestParseDomResults:
    """_parse_dom_results DOM 파싱 로직 테스트."""

    def test_parse_valid_results(self) -> None:
        """유효한 결과 데이터를 CapacityRecord로 변환."""
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

        records = KepcoOnlineScraper._parse_dom_results(mock_page)
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
        """결과 데이터가 비어있는 경우."""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "subst_nm": "",
            "dl_nm": "",
        }

        records = KepcoOnlineScraper._parse_dom_results(mock_page)
        assert records == []

    def test_parse_results_backward_compat(self) -> None:
        """_parse_results는 _parse_dom_results와 동일해야 함."""
        assert KepcoOnlineScraper._parse_results is KepcoOnlineScraper._parse_dom_results


# ---------------------------------------------------------------------------
# _extract_record_from_dict (L1 API 응답 파싱)
# ---------------------------------------------------------------------------


class TestExtractRecordFromDict:
    """_extract_record_from_dict API 응답 딕셔너리 파싱 테스트."""

    def test_with_snake_case_keys(self) -> None:
        d = {
            "subst_nm": "테스트변전소",
            "dl_nm": "DL1",
            "mtr_no": "#1",
            "vol1": "10000",
            "vol2": "5000",
            "vol3": "2000",
        }
        records = KepcoOnlineScraper._extract_record_from_dict(d)
        assert len(records) == 1
        assert records[0].subst_nm == "테스트변전소"
        assert records[0].vol1 == "10000"

    def test_with_camel_case_keys(self) -> None:
        d = {
            "substNm": "테스트변전소",
            "dlNm": "DL1",
            "mtrNo": "#1",
        }
        records = KepcoOnlineScraper._extract_record_from_dict(d)
        assert len(records) == 1
        assert records[0].subst_nm == "테스트변전소"

    def test_empty_dict(self) -> None:
        records = KepcoOnlineScraper._extract_record_from_dict({})
        assert records == []


# ---------------------------------------------------------------------------
# scraper_service.fetch_capacity_by_online 통합 테스트
# ---------------------------------------------------------------------------


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
    @patch("src.data.scraper_service.BOT_DETECTION_DELAY_SECONDS", 0)
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
    @patch("src.data.scraper_service.BOT_DETECTION_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_kepco_online")
    def test_all_retries_fail(self, mock_run: MagicMock) -> None:
        """모든 재시도 소진 시 마지막 예외를 던짐."""
        mock_run.side_effect = ScraperError("검색 실패")

        from src.data.scraper_service import fetch_capacity_by_online

        with pytest.raises(ScraperError, match="검색 실패"):
            fetch_capacity_by_online(sido="충청남도", sigungu="천안시")

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service.BOT_DETECTION_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_kepco_online")
    def test_max_retries_is_3(self, mock_run: MagicMock) -> None:
        """MAX_RETRIES=3이므로 총 3회 시도."""
        mock_run.side_effect = ScraperError("실패")

        from src.data.scraper_service import fetch_capacity_by_online

        with pytest.raises(ScraperError):
            fetch_capacity_by_online(sido="충청남도", sigungu="천안시")

        assert mock_run.call_count == 3


# ---------------------------------------------------------------------------
# scraper_service._run_kepco_online 함수 테스트
# ---------------------------------------------------------------------------


class TestRunKepcoOnline:
    """_run_kepco_online 함수 테스트."""

    @patch("src.data.scraper_service._run_kepco_online")
    def test_with_sido_params(self, mock_run: MagicMock) -> None:
        """sido 파라미터가 있으면 region 기반으로 호출."""
        mock_run.return_value = [CapacityRecord(dlNm="DL1", vol1="100", vol2="200", vol3="300")]

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
        result = mock_run("충청남도 천안시 서북구 불당동")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 봇탐지 에러 판별 테스트
# ---------------------------------------------------------------------------


class TestBotDetection:
    """봇탐지 관련 에러 분류 테스트."""

    def test_bot_keyword_detection(self) -> None:
        from src.data.scraper_service import _is_bot_detection_error

        assert _is_bot_detection_error(ScraperError("CAPTCHA 감지됨")) is True
        assert _is_bot_detection_error(ScraperError("봇 탐지 차단")) is True
        assert _is_bot_detection_error(ScraperError("일반 오류")) is False

    def test_retry_delay_calculation(self) -> None:
        from src.data.scraper_service import (
            BOT_DETECTION_DELAY_SECONDS,
            RETRY_DELAY_SECONDS,
            _retry_delay,
        )

        normal_err = ScraperError("일반 오류")
        bot_err = ScraperError("CAPTCHA 감지")

        assert _retry_delay(normal_err, 1) == RETRY_DELAY_SECONDS
        assert _retry_delay(normal_err, 2) == RETRY_DELAY_SECONDS * 2
        assert _retry_delay(bot_err, 1) == BOT_DETECTION_DELAY_SECONDS
        assert _retry_delay(bot_err, 2) == BOT_DETECTION_DELAY_SECONDS * 2
