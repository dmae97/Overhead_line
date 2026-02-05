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
    sample_data_path: Path = field(
        default_factory=lambda: _PROJECT_ROOT / "data" / "sample_capacity.json"
    )

    capacity_threshold_green: int = 3000
    capacity_threshold_yellow: int = 1000
    capacity_threshold_orange: int = 1


settings = Settings()
