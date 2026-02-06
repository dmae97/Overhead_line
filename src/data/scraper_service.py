"""브라우저 자동화 스크래퍼 통합 서비스 — Playwright 우선, Selenium 폴백.

API 키가 없을 때 한전ON 접속가능 용량조회를 브라우저 자동화로 수행한다.
엔진 우선순위:
  1. Playwright (경량, 네이티브 response 이벤트, stealth 내장)
  2. Selenium (레거시 폴백 — Playwright 미설치/실패 시)

설정:
  - SCRAPER_ENGINE 환경변수로 1차 엔진 지정 (기본 "playwright")
  - 1차 엔진 실패 시 자동으로 나머지 엔진을 폴백 시도
  - 각 엔진은 최대 MAX_RETRIES회 재시도 (봇 탐지 등 일시적 실패 대응)
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)

EngineType = Literal["playwright", "selenium"]

# 엔진당 최대 재시도 횟수 (첫 시도 포함)
MAX_RETRIES = 2
# 재시도 사이 대기 시간 (초)
RETRY_DELAY_SECONDS = 3.0

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


def _run_engine_with_retry(
    engine_name: EngineType,
    keyword: str,
) -> list[CapacityRecord]:
    """단일 엔진을 최대 MAX_RETRIES회 재시도하며 실행.

    첫 시도 실패 시 RETRY_DELAY_SECONDS만큼 대기 후 재시도한다.
    ImportError 등 설치 문제는 재시도 의미가 없으므로 즉시 포기.
    """
    runner = _get_runner(engine_name)
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "🚀 [%s] 엔진 시도 %d/%d: %s",
                engine_name,
                attempt,
                MAX_RETRIES,
                keyword,
            )
            records = runner(keyword)
            logger.info(
                "✅ [%s] 엔진 조회 성공 — %d건 반환",
                engine_name,
                len(records),
            )
            return records
        except ScraperError as exc:
            last_exc = exc
            # 설치 문제(Import 관련)는 재시도 무의미
            if "설치" in exc.message or "import" in exc.message.lower():
                logger.warning(
                    "⚠️ [%s] 설치 문제로 즉시 포기: %s",
                    engine_name,
                    exc.message[:200],
                )
                break
            logger.warning(
                "⚠️ [%s] 시도 %d 실패: %s",
                engine_name,
                attempt,
                exc.message[:200],
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "⚠️ [%s] 시도 %d 예외: %s: %s",
                engine_name,
                attempt,
                type(exc).__name__,
                str(exc)[:200],
            )

        # 재시도 전 대기 (마지막 시도 후에는 불필요)
        if attempt < MAX_RETRIES:
            logger.info(
                "⏳ [%s] %.1f초 후 재시도...",
                engine_name,
                RETRY_DELAY_SECONDS,
            )
            time.sleep(RETRY_DELAY_SECONDS)

    # 모든 재시도 소진
    assert last_exc is not None
    raise last_exc


def fetch_capacity_by_browser(keyword: str) -> list[CapacityRecord]:
    """Playwright 우선 → Selenium 폴백으로 용량 조회를 시도.

    각 엔진은 내부적으로 최대 MAX_RETRIES회 재시도한다.

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
        try:
            return _run_engine_with_retry(engine_name, keyword)
        except ScraperError as exc:
            errors.append((engine_name, exc))
        except Exception as exc:
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
