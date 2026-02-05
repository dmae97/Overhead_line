"""UI 컴포넌트 단위 테스트."""

from __future__ import annotations

from src.ui.components import (
    capacity_color,
    capacity_emoji,
    capacity_label,
    format_capacity,
)


class TestCapacityColor:
    def test_green(self) -> None:
        assert capacity_color(3000) == "#28a745"
        assert capacity_color(5000) == "#28a745"

    def test_yellow(self) -> None:
        assert capacity_color(1000) == "#ffc107"
        assert capacity_color(2999) == "#ffc107"

    def test_orange(self) -> None:
        assert capacity_color(1) == "#fd7e14"
        assert capacity_color(999) == "#fd7e14"

    def test_red(self) -> None:
        assert capacity_color(0) == "#dc3545"


class TestCapacityEmoji:
    def test_green(self) -> None:
        assert capacity_emoji(3000) == "🟢"

    def test_yellow(self) -> None:
        assert capacity_emoji(1500) == "🟡"

    def test_orange(self) -> None:
        assert capacity_emoji(500) == "🟠"

    def test_red(self) -> None:
        assert capacity_emoji(0) == "🔴"


class TestCapacityLabel:
    def test_labels(self) -> None:
        assert capacity_label(5000) == "여유"
        assert capacity_label(2000) == "주의"
        assert capacity_label(500) == "어려움"
        assert capacity_label(0) == "불가"


class TestFormatCapacity:
    def test_format(self) -> None:
        assert format_capacity(3200) == "🟢 3,200 kW"
        assert format_capacity(0) == "🔴 0 kW"
