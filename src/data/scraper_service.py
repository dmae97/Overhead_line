"""브라우저 자동화 스크래퍼 통합 서비스 — 3단계 폴백.

API 키가 없을 때 한전ON 접속가능 용량조회를 브라우저 자동화로 수행한다.
엔진 우선순위:
  1. online  — 한전ON EWM092D00 (online.kepco.co.kr) 직접 호출
  2. playwright — KepcoPlaywrightScraper (online.kepco.co.kr 위임 래퍼)
  3. selenium — KepcoCapacityScraper (online.kepco.co.kr 위임 래퍼)

세 엔진 모두 최종적으로 online.kepco.co.kr/EWM092D00 에 접속하지만,
독립적인 브라우저 세션과 재시도를 수행하므로 일시적 오류에 대한 복원력이 높다.

설정:
  - 각 엔진은 최대 MAX_RETRIES회 재시도
  - 에러 유형별 차등 대기 (봇탐지 → 길게, 타임아웃 → 짧게)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Literal

from src.core.exceptions import ScraperError

if TYPE_CHECKING:
    from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)

EngineType = Literal["online", "playwright", "selenium"]

# 엔진당 최대 재시도 횟수 (첫 시도 포함)
MAX_RETRIES = 3
# 기본 재시도 대기 시간 (초)
RETRY_DELAY_SECONDS = 3.0
# 봇탐지/캡챠 관련 에러 시 추가 대기 (초)
BOT_DETECTION_DELAY_SECONDS = 8.0

# 봇탐지 관련 키워드
_BOT_KEYWORDS = ("captcha", "봇", "bot", "차단", "block", "자동화")


def _is_bot_detection_error(exc: Exception) -> bool:
    """에러가 봇탐지/CAPTCHA 관련인지 판별."""
    msg = getattr(exc, "message", str(exc)).lower()
    return any(kw in msg for kw in _BOT_KEYWORDS)


def _retry_delay(exc: Exception, attempt: int) -> float:
    """에러 유형과 시도 횟수에 따른 대기 시간 결정."""
    if _is_bot_detection_error(exc):
        return BOT_DETECTION_DELAY_SECONDS * attempt  # 점진적 증가
    return RETRY_DELAY_SECONDS * attempt  # 기본 점진적 증가


# ---------------------------------------------------------------------------
# 엔진별 실행 함수 (lazy import)
# ---------------------------------------------------------------------------


def _run_kepco_online(
    keyword: str,
    sido: str = "",
    sigungu: str = "",
    dong: str = "",
    li: str = "",
    jibun: str = "",
) -> list[CapacityRecord]:
    """한전ON EWM092D00 (online.kepco.co.kr) Playwright 엔진으로 용량 조회.

    keyword는 호환성을 위해 받지만, sido/sigungu/dong이 제공되면 우선 사용한다.
    """
    from src.data.kepco_online import KepcoOnlineScraper

    scraper = KepcoOnlineScraper()
    if sido:
        return scraper.fetch_capacity_by_region(
            sido=sido,
            sigungu=sigungu,
            dong=dong,
            li=li,
            jibun=jibun,
        )
    # keyword-only: parse into components
    parts = keyword.strip().split()
    if not parts:
        raise ScraperError("검색 키워드가 비어있습니다.")

    # Simple heuristic parsing
    _sido = parts[0]
    _si = parts[1] if len(parts) > 1 else ""
    _gu = parts[2] if len(parts) > 2 else ""
    _dong = parts[3] if len(parts) > 3 else ""
    _jibun = parts[4] if len(parts) > 4 else ""

    return scraper.fetch_capacity(
        sido=_sido,
        si=_si,
        gu=_gu,
        dong=_dong,
        jibun=_jibun,
    )


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


# ---------------------------------------------------------------------------
# 엔진 선택 및 재시도
# ---------------------------------------------------------------------------


def _resolve_engine_order() -> list[EngineType]:
    """엔진 우선순위: online(한전ON) → playwright → selenium."""
    return ["online", "playwright", "selenium"]


def _get_runner(engine_name: EngineType):
    """엔진 이름에 해당하는 실행 함수를 반환."""
    if engine_name == "online":
        return lambda kw: _run_kepco_online(kw)
    if engine_name == "selenium":
        return _run_selenium
    return _run_playwright


def _run_engine_with_retry(
    engine_name: EngineType,
    keyword: str,
) -> list[CapacityRecord]:
    """단일 엔진을 최대 MAX_RETRIES회 재시도하며 실행."""
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
            if "설치" in exc.message or "import" in exc.message.lower():
                logger.warning("⚠️ [%s] 설치 문제로 즉시 포기: %s", engine_name, exc.message[:200])
                break
            logger.warning("⚠️ [%s] 시도 %d 실패: %s", engine_name, attempt, exc.message[:200])
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "⚠️ [%s] 시도 %d 예외: %s: %s",
                engine_name,
                attempt,
                type(exc).__name__,
                str(exc)[:200],
            )

        if attempt < MAX_RETRIES:
            delay = _retry_delay(last_exc, attempt) if last_exc else RETRY_DELAY_SECONDS
            logger.info("⏳ [%s] %.1f초 후 재시도...", engine_name, delay)
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def fetch_capacity_by_browser(keyword: str) -> list[CapacityRecord]:
    """online(한전ON) → playwright → selenium 3단계 폴백으로 용량 조회.

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
        except (ScraperError, Exception) as exc:
            errors.append((engine_name, exc))

    summary_lines = ["모든 브라우저 자동화 엔진이 실패했습니다."]
    for engine_name, exc in errors:
        msg = getattr(exc, "message", str(exc))
        summary_lines.append(f"  - {engine_name}: {msg}")
    summary_lines.append(
        "해결: Playwright(`uv add playwright && playwright install chromium`) 설치를 확인하세요."
    )
    raise ScraperError("\n".join(summary_lines))


def fetch_capacity_by_online(
    sido: str,
    sigungu: str,
    dong: str = "",
    ri: str = "",
    jibun: str = "",
) -> list[CapacityRecord]:
    """한전ON(EWM092D00) Playwright 스크래퍼로 직접 용량 조회.

    API 키 없이 사용 가능. 3계층 전략(JS API + DOM 자동화) + 재시도.

    Args:
        sido: 시/도 (예: "충청남도")
        sigungu: 시군구 (예: "천안시 서북구")
        dong: 읍/면/동 (예: "불당동")
        jibun: 번지 (선택)

    Returns:
        CapacityRecord 리스트

    Raises:
        ScraperError: 모든 시도가 실패한 경우
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
                li=ri,
                jibun=jibun,
            )
            logger.info("✅ [kepco_online] 조회 성공 — %d건 반환", len(records))
            return records
        except ScraperError as exc:
            last_exc = exc
            if "설치" in exc.message or "import" in exc.message.lower():
                logger.warning("⚠️ [kepco_online] 설치 문제로 즉시 포기: %s", exc.message[:200])
                break
            logger.warning(
                "⚠️ [kepco_online] 시도 %d 실패: %s",
                attempt,
                exc.message[:300],
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "⚠️ [kepco_online] 시도 %d 예외: %s: %s",
                attempt,
                type(exc).__name__,
                str(exc)[:200],
            )

        if attempt < MAX_RETRIES:
            delay = _retry_delay(last_exc, attempt) if last_exc else RETRY_DELAY_SECONDS
            logger.info("⏳ [kepco_online] %.1f초 후 재시도...", delay)
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc
