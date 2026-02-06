"""브라우저 자동화 스크래퍼 통합 서비스 — 한전ON(EWM092D00) 우선, 기존 Playwright/Selenium 폴백.

API 키가 없을 때 한전ON 접속가능 용량조회를 브라우저 자동화로 수행한다.
엔진 우선순위:
  1. 한전ON EWM092D00 (online.kepco.co.kr — Playwright 기반, 주소 cascading)
  2. Playwright 기존 (home.kepco.co.kr — 키워드 검색, 봇탐지 가능성 있음)
  3. Selenium (레거시 폴백)

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


def _run_kepco_online(
    keyword: str,
    sido: str = "",
    sigungu: str = "",
    dong: str = "",
    jibun: str = "",
) -> list[CapacityRecord]:
    """한전ON EWM092D00 (online.kepco.co.kr) Playwright 엔진으로 용량 조회.

    keyword는 호환성을 위해 받지만, sido/sigungu/dong이 제공되면 우선 사용한다.
    """
    from src.data.kepco_online import KepcoOnlineScraper

    scraper = KepcoOnlineScraper()
    if sido:
        return scraper.fetch_capacity_by_region(sido=sido, sigungu=sigungu, dong=dong, jibun=jibun)
    # keyword만 제공된 경우 — 파싱 시도 (시도명 추출)
    # "충청남도 천안시 서북구 불당동 142-1" 같은 형태
    parts = keyword.strip().split()
    if parts:
        return scraper.fetch_capacity(
            sido=parts[0],
            si=parts[1] if len(parts) > 1 else "",
            gu=parts[2] if len(parts) > 2 else "",
            dong=parts[3] if len(parts) > 3 else "",
            jibun=parts[4] if len(parts) > 4 else "",
        )
    raise ScraperError("검색 키워드가 비어있습니다.")


def _run_playwright(keyword: str) -> list[CapacityRecord]:
    """Playwright 엔진으로 용량 조회 (기존 home.kepco.co.kr)."""
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


def fetch_capacity_by_online(
    sido: str,
    sigungu: str,
    dong: str = "",
    jibun: str = "",
) -> list[CapacityRecord]:
    """한전ON(EWM092D00) Playwright 스크래퍼로 직접 용량 조회.

    API 키 없이 사용 가능. 주소 기반 cascading 선택 후 DOM에서 결과를 파싱.

    Args:
        sido: 시/도 (예: "충청남도")
        sigungu: 시군구 (예: "천안시 서북구")
        dong: 읍/면/동 (예: "불당동")
        jibun: 번지 (선택)

    Returns:
        CapacityRecord 리스트

    Raises:
        ScraperError: 브라우저 자동화 실패
    """
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "🚀 [kepco_online] 시도 %d/%d: %s %s %s",
                attempt,
                MAX_RETRIES,
                sido,
                sigungu,
                dong,
            )
            records = _run_kepco_online(
                keyword="",
                sido=sido,
                sigungu=sigungu,
                dong=dong,
                jibun=jibun,
            )
            logger.info(
                "✅ [kepco_online] 조회 성공 — %d건 반환",
                len(records),
            )
            return records
        except ScraperError as exc:
            last_exc = exc
            if "설치" in exc.message or "import" in exc.message.lower():
                break
            logger.warning(
                "⚠️ [kepco_online] 시도 %d 실패: %s",
                attempt,
                exc.message[:200],
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "⚠️ [kepco_online] 시도 %d 예외: %s",
                attempt,
                str(exc)[:200],
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    assert last_exc is not None
    raise last_exc
