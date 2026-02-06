"""scraper_service 모듈 단위 테스트 — Playwright 우선 + Selenium 폴백 + 재시도 로직.

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
    """fetch_capacity_by_browser 통합 폴백 로직 테스트."""

    @patch("src.data.scraper_service._run_playwright")
    def test_playwright_success_no_fallback(self, mock_pw: MagicMock) -> None:
        """Playwright 성공 시 Selenium은 호출되지 않는다."""
        mock_pw.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
        assert records[0].subst_nm == "테스트변전소"
        mock_pw.assert_called_once_with("세종특별자치시")

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    def test_playwright_fail_selenium_fallback(
        self, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """Playwright 실패 시 Selenium 폴백으로 성공한다."""
        mock_pw.side_effect = ScraperError("playwright 설치 오류")
        mock_sel.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
        mock_pw.assert_called_once()
        mock_sel.assert_called_once_with("세종특별자치시")

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    def test_both_engines_fail_raises(self, mock_pw: MagicMock, mock_sel: MagicMock) -> None:
        """두 엔진 모두 실패하면 ScraperError를 발생시킨다."""
        mock_pw.side_effect = ScraperError("playwright 설치 오류")
        mock_sel.side_effect = ScraperError("selenium 설치 오류")

        from src.data.scraper_service import fetch_capacity_by_browser

        with pytest.raises(ScraperError, match="모든 브라우저 자동화 엔진이 실패"):
            fetch_capacity_by_browser("세종특별자치시")

        mock_pw.assert_called_once()
        mock_sel.assert_called_once()

    @patch("src.data.scraper_service._run_selenium")
    @patch("src.data.scraper_service._run_playwright")
    def test_generic_exception_triggers_fallback(
        self, mock_pw: MagicMock, mock_sel: MagicMock
    ) -> None:
        """Playwright가 ScraperError가 아닌 예외를 던져도 Selenium 폴백이 작동한다."""
        mock_pw.side_effect = RuntimeError("unexpected")
        mock_sel.return_value = _DUMMY_RECORDS

        from src.data.scraper_service import fetch_capacity_by_browser

        records = fetch_capacity_by_browser("세종특별자치시")
        assert len(records) == 1
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

    @patch("src.data.scraper_service.settings")
    def test_default_playwright_first(self, mock_settings: MagicMock) -> None:
        """기본 설정은 playwright 우선."""
        mock_settings.scraper_engine = "playwright"

        from src.data.scraper_service import _resolve_engine_order

        order = _resolve_engine_order()
        assert order == ["playwright", "selenium"]

    @patch("src.data.scraper_service.settings")
    def test_selenium_first_when_configured(self, mock_settings: MagicMock) -> None:
        """SCRAPER_ENGINE=selenium이면 selenium 우선."""
        mock_settings.scraper_engine = "selenium"

        from src.data.scraper_service import _resolve_engine_order

        order = _resolve_engine_order()
        assert order == ["selenium", "playwright"]

    @patch("src.data.scraper_service.settings")
    def test_unknown_engine_defaults_to_playwright(self, mock_settings: MagicMock) -> None:
        """알 수 없는 엔진 이름은 playwright로 폴백."""
        mock_settings.scraper_engine = "unknown"

        from src.data.scraper_service import _resolve_engine_order

        order = _resolve_engine_order()
        assert order == ["playwright", "selenium"]
