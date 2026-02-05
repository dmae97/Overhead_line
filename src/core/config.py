"""앱 설정 관리 모듈 — 환경변수 로드 및 앱 전역 설정 제공."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """앱 전역 설정. 환경변수에서 값을 읽으며 frozen=True로 런타임 변경을 방지."""

    address_cache_ttl: int = 86400  # 24h

    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")

    history_db_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "data" / "history.db")

    kepco_api_key: str = field(default_factory=lambda: os.getenv("KEPCO_API_KEY", "").strip())
    kepco_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "KEPCO_API_BASE_URL",
            "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do",
        ).strip()
    )
    kepco_api_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("KEPCO_API_TIMEOUT_SECONDS", "15"))
    )
    kepco_api_delay_seconds: float = field(
        default_factory=lambda: float(os.getenv("KEPCO_API_DELAY_SECONDS", "0"))
    )

    kepco_on_capacity_url: str = field(
        default_factory=lambda: os.getenv(
            "KEPCO_ON_CAPACITY_URL",
            "https://home.kepco.co.kr/kepco/CO/H/E/COHEPP001/COHEPP00110.do?menuCd=FN420106",
        ).strip()
    )
    selenium_headless: bool = field(
        default_factory=lambda: os.getenv("SELENIUM_HEADLESS", "true").lower() != "false"
    )
    selenium_page_load_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS", "40"))
    )
    selenium_result_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("SELENIUM_RESULT_TIMEOUT_SECONDS", "30"))
    )

    capacity_threshold_green: int = 3000
    capacity_threshold_yellow: int = 1000
    capacity_threshold_orange: int = 1


settings = Settings()
