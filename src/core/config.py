"""앱 설정 관리 모듈 — 환경변수/Secrets 로드 및 전역 설정 제공.

Streamlit Community Cloud에서는 `.env` 대신 Secrets(`.streamlit/secrets.toml`)로
환경변수를 주입하는 경우가 많다. 이 모듈은 다음 우선순위로 값을 로드한다.

1) OS 환경변수 (os.environ)
2) Streamlit secrets.toml (프로젝트/.streamlit 또는 사용자 홈)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _load_secrets() -> dict[str, Any]:
    candidates = [
        _PROJECT_ROOT / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]
    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError:
            continue

        try:
            data = tomllib.loads(content)
        except Exception:
            continue

        if isinstance(data, dict):
            return data
    return {}


_SECRETS = _load_secrets()


def _get_raw(key: str) -> str | None:
    value = os.getenv(key)
    if value is not None:
        return value

    secret = _SECRETS.get(key)
    if secret is None:
        return None
    if isinstance(secret, (str, int, float, bool)):
        return str(secret)
    return None


def _get_str(key: str, default: str) -> str:
    value = _get_raw(key)
    if value is None:
        return default
    return str(value).strip()


def _get_bool(key: str, default: bool) -> bool:
    value = _get_raw(key)
    if value is None:
        return default
    return str(value).strip().lower() == "true"


def _get_bool_default_true(key: str) -> bool:
    value = _get_raw(key)
    if value is None:
        return True
    return str(value).strip().lower() != "false"


def _get_float(key: str, default: float) -> float:
    value = _get_raw(key)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """앱 전역 설정. 환경변수에서 값을 읽으며 frozen=True로 런타임 변경을 방지."""

    address_cache_ttl: int = 86400  # 24h

    debug: bool = field(default_factory=lambda: _get_bool("DEBUG", False))

    history_db_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "data" / "history.db")

    kepco_api_key: str = field(default_factory=lambda: _get_str("KEPCO_API_KEY", ""))
    kepco_api_base_url: str = field(
        default_factory=lambda: _get_str(
            "KEPCO_API_BASE_URL",
            "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do",
        )
    )
    kepco_api_timeout_seconds: float = field(
        default_factory=lambda: _get_float("KEPCO_API_TIMEOUT_SECONDS", 15.0)
    )
    kepco_api_delay_seconds: float = field(
        default_factory=lambda: _get_float("KEPCO_API_DELAY_SECONDS", 0.0)
    )

    kepco_on_capacity_url: str = field(
        default_factory=lambda: _get_str(
            "KEPCO_ON_CAPACITY_URL",
            "https://home.kepco.co.kr/kepco/CO/H/E/COHEPP001/COHEPP00110.do?menuCd=FN420106",
        )
    )
    selenium_headless: bool = field(
        default_factory=lambda: _get_bool_default_true("SELENIUM_HEADLESS")
    )
    selenium_page_load_timeout_seconds: float = field(
        default_factory=lambda: _get_float("SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS", 40.0)
    )
    selenium_result_timeout_seconds: float = field(
        default_factory=lambda: _get_float("SELENIUM_RESULT_TIMEOUT_SECONDS", 30.0)
    )

    capacity_threshold_green: int = 3000
    capacity_threshold_yellow: int = 1000
    capacity_threshold_orange: int = 1


settings = Settings()
