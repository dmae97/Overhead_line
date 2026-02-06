"""한전 Selenium 폴백 스크래퍼 — online.kepco.co.kr 위임 래퍼.

기존에는 home.kepco.co.kr 접속가능 용량조회 페이지를 Selenium으로 스크래핑했으나,
한전이 해당 페이지를 변경하여 여유용량 데이터가 더 이상 제공되지 않는다.

현재 이 모듈은 호환성을 위해 유지되며,
내부적으로 KepcoOnlineScraper(online.kepco.co.kr)에 위임한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrapeOptions:
    """Selenium 스크래퍼 옵션 (호환성 유지)."""

    headless: bool = True
    page_load_timeout_seconds: float = 40.0
    result_timeout_seconds: float = 30.0


def _parse_keyword_to_region(keyword: str) -> dict[str, str]:
    """키워드 문자열을 sido/si/gu/dong/jibun으로 파싱 시도."""
    parts = keyword.strip().split()
    if not parts:
        raise ScraperError("검색 키워드가 비어있습니다.")

    result = {"sido": "", "si": "", "gu": "", "dong": "", "jibun": ""}
    result["sido"] = parts[0]

    if len(parts) >= 2:
        token = parts[1]
        if any(token.endswith(s) for s in ["시", "군"]):
            result["si"] = token
        elif any(token.endswith(s) for s in ["구"]):
            result["gu"] = token
        elif any(token.endswith(s) for s in ["읍", "면", "동", "리", "로", "길"]):
            result["dong"] = token
        else:
            result["si"] = token

    if len(parts) >= 3:
        token = parts[2]
        if any(token.endswith(s) for s in ["구", "군"]):
            result["gu"] = token
        elif any(token.endswith(s) for s in ["읍", "면", "동", "리", "로", "길"]):
            result["dong"] = token
        else:
            if not result["gu"]:
                result["gu"] = token
            else:
                result["dong"] = token

    if len(parts) >= 4:
        token = parts[3]
        if any(token.endswith(s) for s in ["읍", "면", "동", "리", "로", "길"]) or not result["dong"]:
            result["dong"] = token
        else:
            result["jibun"] = token

    if len(parts) >= 5 and not result["jibun"]:
        result["jibun"] = " ".join(parts[4:])

    return result


class KepcoCapacityScraper:
    """Selenium 기반 한전 접속가능 용량조회 스크래퍼.

    내부적으로 KepcoOnlineScraper에 위임한다.
    """

    def __init__(self, url: str | None = None, options: ScrapeOptions | None = None) -> None:
        self._url = url
        self._options = options or ScrapeOptions()

    def fetch_capacity_by_keyword(self, keyword: str) -> list[CapacityRecord]:
        """키워드로 검색 후 여유용량 레코드를 반환."""
        from src.data.kepco_online import KepcoOnlineScraper

        logger.info("🔄 Selenium 래퍼: 키워드 '%s' → KepcoOnlineScraper 위임", keyword)
        region = _parse_keyword_to_region(keyword)
        logger.info("📍 키워드 파싱 결과: %s", region)

        scraper = KepcoOnlineScraper()
        return scraper.fetch_capacity(
            sido=region["sido"],
            si=region["si"],
            gu=region["gu"],
            dong=region["dong"],
            jibun=region["jibun"],
        )
