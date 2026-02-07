"""한전 Playwright 기반 여유용량 스크래퍼 — online.kepco.co.kr 위임 래퍼.

기존에는 home.kepco.co.kr 접속가능 용량조회 페이지를 타겟했으나,
한전이 해당 페이지를 '공용망 보강공사 현황'으로 변경하여 여유용량 데이터가 더 이상 제공되지 않는다.

현재 여유용량 조회는 한전ON(online.kepco.co.kr/EWM092D00)에서만 가능하므로,
이 모듈은 키워드를 파싱하여 KepcoOnlineScraper에 위임한다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.exceptions import ScraperError

if TYPE_CHECKING:
    from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)


class PlaywrightOptions:
    """Playwright 스크래퍼 실행 옵션 (호환성 유지)."""

    def __init__(self, **kwargs: Any) -> None:
        # 과거 옵션 객체를 넘기던 코드와의 호환을 위해 kwargs를 그대로 보관한다.
        # (현재 래퍼 구현에서는 실질적으로 사용하지 않지만, 외부에서 속성 접근을
        # 기대할 수 있으므로 동적으로 attribute를 설정한다.)
        self._raw: dict[str, Any] = dict(kwargs)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self) -> dict[str, Any]:
        """원본 옵션을 dict로 반환."""
        return dict(self._raw)


def _parse_keyword_to_region(keyword: str) -> dict[str, str]:
    """키워드 문자열을 sido/si/gu/dong/jibun으로 파싱 시도.

    예시:
      "세종특별자치시 조치원읍"
        → {"sido": "세종특별자치시", "si": "", "gu": "", "dong": "조치원읍"}
      "충청남도 천안시 서북구 불당동"
        → {"sido": "충청남도", "si": "천안시", "gu": "서북구", "dong": "불당동"}
      "경기도 수원시 팔달구 매산로"
        → {"sido": "경기도", "si": "수원시", "gu": "팔달구", "dong": "매산로"}
    """
    parts = keyword.strip().split()
    if not parts:
        raise ScraperError("검색 키워드가 비어있습니다.")

    result = {"sido": "", "si": "", "gu": "", "dong": "", "jibun": ""}

    # 시도 (첫 토큰)
    result["sido"] = parts[0]

    if len(parts) >= 2:
        # 두 번째 토큰: 시/군/구 또는 동
        token = parts[1]
        if any(token.endswith(s) for s in ["시", "군"]):
            result["si"] = token
        elif any(token.endswith(s) for s in ["구"]):
            result["gu"] = token
        elif any(token.endswith(s) for s in ["읍", "면", "동", "리", "로", "길"]):
            result["dong"] = token
        else:
            result["si"] = token  # 기본적으로 시로 간주

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
        if (
            any(token.endswith(s) for s in ["읍", "면", "동", "리", "로", "길"])
            or not result["dong"]
        ):
            result["dong"] = token
        else:
            result["jibun"] = token

    if len(parts) >= 5 and not result["jibun"]:
        # 나머지는 번지로
        result["jibun"] = " ".join(parts[4:])

    return result


class KepcoPlaywrightScraper:
    """Playwright 기반 한전 접속가능 용량조회 스크래퍼.

    내부적으로 KepcoOnlineScraper(online.kepco.co.kr/EWM092D00)에 위임한다.
    home.kepco.co.kr 페이지가 더 이상 여유용량 데이터를 제공하지 않으므로,
    키워드를 파싱하여 한전ON 스크래퍼로 전달한다.
    """

    def __init__(self, url: str | None = None, options: PlaywrightOptions | None = None) -> None:
        self._url = url  # 호환성 유지용, 실제로는 사용하지 않음
        self._options = options

    def fetch_capacity_by_keyword(self, keyword: str) -> list[CapacityRecord]:
        """키워드(주소/지번 등)로 검색 후 여유용량 레코드를 반환.

        Args:
            keyword: 검색할 주소 키워드 (예: "세종특별자치시 조치원읍")

        Returns:
            CapacityRecord 리스트

        Raises:
            ScraperError: 조회 실패
        """
        from src.data.kepco_online import KepcoOnlineScraper

        logger.info("Playwright 래퍼: 키워드 '%s' → KepcoOnlineScraper 위임", keyword)

        region = _parse_keyword_to_region(keyword)
        logger.info("키워드 파싱 결과: %s", region)

        scraper = KepcoOnlineScraper()
        return scraper.fetch_capacity(
            sido=region["sido"],
            si=region["si"],
            gu=region["gu"],
            dong=region["dong"],
            jibun=region["jibun"],
        )
