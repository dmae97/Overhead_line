"""scraper_service 모듈 단위 테스트 — 3단계 폴백(online → playwright → selenium) + 재시도 로직.

실제 브라우저를 실행하지 않고 mock으로 엔진 선택·폴백·재시도 로직만 검증한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

# 테스트용 더미 레코드
_DUMMY_RECORDS = [
    CapacityRecord(
        substCd="S001",
        substNm="테스트변전소",
        mtrNo="#1",
        dlNm="테스트DL",
        vol1="10000",
        vol2="5000",
        vol3="2000",
    )
]


class TestFetchCapacityByBrowser:
    """fetch_capacity_by_browser 3단계 폴백 로직 테스트."""

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    @patch("src.data.scraper_service._run_kepco_online")
    def test_online_success_no_fallback(
        self, mock_online: MagicMock, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """online 성공 시 playwright/selenium은 호출되지 않는다."""
        mock_online.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
        assert records[0].subst_nm == "테스트변전소"
        mock_online.assert_called()
        mock_pw.assert_not_called()
        mock_sel.assert_not_called()

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    @patch("src.data.scraper_service._run_kepco_online")
    def test_online_fail_playwright_fallback(
        self, mock_online: MagicMock, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """online 실패 시 playwright 폴백으로 성공."""
        mock_online.side_effect = ScraperError("설치 오류")
        mock_pw.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
        mock_online.assert_called()
        mock_pw.assert_called()
        mock_sel.assert_not_called()

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    @patch("src.data.scraper_service._run_kepco_online")
    def test_online_pw_fail_selenium_fallback(
        self, mock_online: MagicMock, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """online + playwright 실패 시 selenium 폴백으로 성공."""
        mock_online.side_effect = ScraperError("설치 오류")
        mock_pw.side_effect = ScraperError("설치 오류")
        mock_sel.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
        mock_online.assert_called()
        mock_pw.assert_called()
        mock_sel.assert_called()

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    @patch("src.data.scraper_service._run_kepco_online")
    def test_all_engines_fail_raises(
        self, mock_online: MagicMock, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """세 엔진 모두 실패하면 ScraperError를 발생시킨다."""
        mock_online.side_effect = ScraperError("설치 오류")
        mock_pw.side_effect = ScraperError("playwright 설치 오류")
        mock_sel.side_effect = ScraperError("selenium 설치 오류")

        from src.data.scraper_service import fetch_capacity_by_browser

        with pytest.raises(ScraperError, match="모든 브라우저 자동화 엔진이 실패"):
            fetch_capacity_by_browser("세종특별자치시")

        mock_online.assert_called()
        mock_pw.assert_called()
        mock_sel.assert_called()

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    @patch("src.data.scraper_service._run_kepco_online")
    def test_generic_exception_triggers_fallback(
        self, mock_online: MagicMock, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """ScraperError가 아닌 예외도 폴백을 트리거한다."""
        mock_online.side_effect = RuntimeError("unexpected")
        mock_pw.side_effect = RuntimeError("unexpected")
        mock_sel.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
        mock_online.assert_called()
        mock_pw.assert_called()
        mock_sel.assert_called()


class TestRetryLogic:
    """_run_engine_with_retry 재시도 로직 테스트."""

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_playwright")
    def test_retry_succeeds_on_second_attempt(self, mock_pw: MagicMock) -> None:
        """첫 시도 실패 후 재시도에서 성공."""
        mock_pw.side_effect = [ScraperError("일시적 오류"), _DUMMY_RECORDS]

        from src.data.scraper_service import _run_engine_with_retry

        records = _run_engine_with_retry("playwright", "세종특별자치시")
        assert len(records) == 1
        assert mock_pw.call_count == 2

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_playwright")
    def test_install_error_skips_retry(self, mock_pw: MagicMock) -> None:
        """설치 관련 에러는 재시도 없이 즉시 포기."""
        mock_pw.side_effect = ScraperError("playwright 패키지가 설치되어 있지 않습니다.")

        from src.data.scraper_service import _run_engine_with_retry

        with pytest.raises(ScraperError, match="설치"):
            _run_engine_with_retry("playwright", "세종특별자치시")

        # 설치 문제는 1번만 시도
        assert mock_pw.call_count == 1

    @patch("src.data.scraper_service.RETRY_DELAY_SECONDS", 0)
    @patch("src.data.scraper_service._run_selenium")
    def test_all_retries_exhausted_raises(self, mock_sel: MagicMock) -> None:
        """모든 재시도 소진 후 마지막 예외를 던진다."""
        mock_sel.side_effect = ScraperError("봇 탐지")

        from src.data.scraper_service import MAX_RETRIES, _run_engine_with_retry

        with pytest.raises(ScraperError, match="봇 탐지"):
            _run_engine_with_retry("selenium", "세종특별자치시")

        assert mock_sel.call_count == MAX_RETRIES


class TestResolveEngineOrder:
    """_resolve_engine_order 엔진 순서 결정 로직 테스트."""

    def test_order_is_online_playwright_selenium(self) -> None:
        """엔진 순서: online → playwright → selenium."""
        from src.data.scraper_service import _resolve_engine_order

        order = _resolve_engine_order()
        assert order == ["online", "playwright", "selenium"]


class TestKeywordParsing:
    """키워드 파싱 로직 테스트."""

    def test_parse_4_parts(self) -> None:
        """4단어 키워드 파싱."""
        from src.data.kepco_playwright import _parse_keyword_to_region

        result = _parse_keyword_to_region("충청남도 천안시 서북구 불당동")
        assert result["sido"] == "충청남도"
        assert result["si"] == "천안시"
        assert result["gu"] == "서북구"
        assert result["dong"] == "불당동"

    def test_parse_2_parts_with_dong(self) -> None:
        """2단어 키워드 (시도 + 읍면동)."""
        from src.data.kepco_playwright import _parse_keyword_to_region

        result = _parse_keyword_to_region("세종특별자치시 조치원읍")
        assert result["sido"] == "세종특별자치시"
        assert result["dong"] == "조치원읍"

    def test_parse_1_part(self) -> None:
        """1단어 키워드."""
        from src.data.kepco_playwright import _parse_keyword_to_region

        result = _parse_keyword_to_region("경기도")
        assert result["sido"] == "경기도"

    def test_parse_empty_raises(self) -> None:
        """빈 키워드는 ScraperError."""
        from src.data.kepco_playwright import _parse_keyword_to_region

        with pytest.raises(ScraperError, match="비어있습니다"):
            _parse_keyword_to_region("")
