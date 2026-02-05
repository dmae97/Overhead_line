"""재사용 가능한 UI 컴포넌트 — 색상 코딩, 상태 배지 등."""

from __future__ import annotations

from src.core.config import settings


def capacity_color(capacity_kw: int) -> str:
    """여유용량(kW)에 따른 색상 hex 코드 반환.

    비즈니스 규칙:
    - ≥3,000 kW → 초록 (연계 가능, 여유)
    - ≥1,000 kW → 노랑 (연계 가능, 주의)
    - ≥1 kW    → 주황 (연계 어려움)
    - 0 kW     → 빨강 (연계 불가)
    """
    if capacity_kw >= settings.capacity_threshold_green:
        return "#28a745"
    if capacity_kw >= settings.capacity_threshold_yellow:
        return "#ffc107"
    if capacity_kw >= settings.capacity_threshold_orange:
        return "#fd7e14"
    return "#dc3545"


def capacity_emoji(capacity_kw: int) -> str:
    """여유용량(kW)에 따른 상태 이모지 반환."""
    if capacity_kw >= settings.capacity_threshold_green:
        return "🟢"
    if capacity_kw >= settings.capacity_threshold_yellow:
        return "🟡"
    if capacity_kw >= settings.capacity_threshold_orange:
        return "🟠"
    return "🔴"


def capacity_label(capacity_kw: int) -> str:
    """여유용량(kW)에 따른 상태 텍스트 반환."""
    if capacity_kw >= settings.capacity_threshold_green:
        return "여유"
    if capacity_kw >= settings.capacity_threshold_yellow:
        return "주의"
    if capacity_kw >= settings.capacity_threshold_orange:
        return "어려움"
    return "불가"


def format_capacity(capacity_kw: int) -> str:
    """여유용량을 이모지 + 숫자 포맷으로 반환. 예: '🟢 3,200 kW'"""
    emoji = capacity_emoji(capacity_kw)
    return f"{emoji} {capacity_kw:,} kW"
