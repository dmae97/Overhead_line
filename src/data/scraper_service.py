"""브라우저 자동화 스크래퍼 통합 서비스 — Playwright 우선, Selenium 폴백.

API 키가 없을 때 한전ON 접속가능 용량조회를 브라우저 자동화로 수행한다.
엔진 우선순위:
  1. Playwright (경량, 네이티브 response 이벤트, stealth 내장)
  2. Selenium (레거시 폴백 — Playwright 미설치/실패 시)

설정:
  - SCRAPER_ENGINE 환경변수로 1차 엔진 지정 (기본 "playwright")
  - 1차 엔진 실패 시 자동으로 나머지 엔진을 폴백 시도
"""

from __future__ import annotations

import logging
from typing import Literal

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)

EngineType = Literal["playwright", "selenium"]

# 엔진별 지연 import + 실행을 담당하는 내부 함수
# (각 패키지가 미설치여도 import 시점에 앱이 죽지 않도록 lazy import)


def _run_playwright(keyword: str) -> list[CapacityRecord]:
    """Playwright 엔진으로 용량 조회."""
    from src.data.kepco_playwright import KepcoPlaywrightScraper

    scraper = KepcoPlaywrightScraper()
    return scraper.fetch_capacity_by_keyword(keyword)


def _run_selenium(keyword: str) -> list[CapacityRecord]:
    """Selenium 엔진으로 용량 조회."""
    from src.data.kepco_scraper import KepcoCapacityScraper

    scraper = KepcoCapacityScraper()
    return scraper.fetch_capacity_by_keyword(keyword)


def _resolve_engine_order() -> list[EngineType]:
    """설정에 따라 (1차 엔진, 폴백 엔진) 순서를 결정.

    Returns:
        [primary, fallback] 순서의 엔진 이름 리스트.
    """
    primary: EngineType = (
        "selenium" if settings.scraper_engine.strip().lower() == "selenium" else "playwright"
    )
    fallback: EngineType = "selenium" if primary == "playwright" else "playwright"
    return [primary, fallback]


def _get_runner(engine_name: EngineType):
    """엔진 이름에 해당하는 실행 함수를 반환.

    모듈 레벨의 _run_playwright / _run_selenium을 **런타임에** 참조하므로
    unittest.mock.patch와 호환된다.
    """
    if engine_name == "selenium":
        return _run_selenium
    return _run_playwright


def fetch_capacity_by_browser(keyword: str) -> list[CapacityRecord]:
    """Playwright 우선 → Selenium 폴백으로 용량 조회를 시도.

    Args:
        keyword: 검색할 주소 키워드 (예: "세종특별자치시 조치원읍 142-1")

    Returns:
        CapacityRecord 리스트

    Raises:
        ScraperError: 모든 엔진이 실패한 경우
    """
    engines = _resolve_engine_order()
    errors: list[tuple[str, Exception]] = []

    for engine_name in engines:
        runner = _get_runner(engine_name)
        try:
            logger.info("🚀 [%s] 엔진으로 브라우저 조회 시작: %s", engine_name, keyword)
            records = runner(keyword)
            logger.info(
                "✅ [%s] 엔진 조회 성공 — %d건 반환",
                engine_name,
                len(records),
            )
            return records
        except ScraperError as exc:
            logger.warning(
                "⚠️ [%s] 엔진 실패: %s",
                engine_name,
                exc.message,
            )
            errors.append((engine_name, exc))
        except Exception as exc:
            logger.warning(
                "⚠️ [%s] 엔진 예외: %s: %s",
                engine_name,
                type(exc).__name__,
                exc,
            )
            errors.append((engine_name, exc))

    # 모든 엔진이 실패한 경우 — 에러 요약 메시지 생성
    summary_lines = ["모든 브라우저 자동화 엔진이 실패했습니다."]
    for engine_name, exc in errors:
        msg = getattr(exc, "message", str(exc))
        summary_lines.append(f"  - {engine_name}: {msg}")
    summary_lines.append(
        "해결: Playwright(`uv add playwright && playwright install chromium`) 또는 "
        "Selenium(`uv add selenium`) 설치를 확인하세요."
    )

    raise ScraperError("\n".join(summary_lines))
