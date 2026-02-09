from __future__ import annotations

from src.data.models import CapacityRecord
from src.ui.map_view import _build_schematic_points_and_segments


def test_schematic_segments_color_red_when_min_is_zero() -> None:
    # 같은 그룹(변전소/변압기) 내 DL 2개를 배치하면,
    # 변압기->DL 연결선 중 min_capacity=0인 DL은 빨강(#dc3545)으로 들어간다.
    r_good = CapacityRecord(
        substCd="S01",
        substNm="Sub A",
        mtrNo="MTR 1",
        dlCd="D01",
        dlNm="DL 1",
        vol1="5000",
        vol2="5000",
        vol3="5000",
    )
    r_bad = CapacityRecord(
        substCd="S01",
        substNm="Sub A",
        mtrNo="MTR 1",
        dlCd="D02",
        dlNm="DL 2",
        vol1="5000",
        vol2="5000",
        vol3="0",
    )

    grouped, sub_points, mtr_points, dl_points, seg_sub_mtr, seg_mtr_dl = (
        _build_schematic_points_and_segments(
            records=[r_good, r_bad],
            base_lat=36.4,
            base_lon=127.8,
            spread=0.1,
        )
    )

    assert ("Sub A", "MTR 1") in grouped
    assert "Sub A" in sub_points
    assert ("Sub A", "MTR 1") in mtr_points
    assert len(dl_points) == 2
    assert seg_sub_mtr["lat"]

    red = seg_mtr_dl["#dc3545"]["lat"]
    assert len(red) >= 3
    # 두 점을 이은 뒤 None으로 끊는 형태
    assert red[-1] is None
