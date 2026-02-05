from src.data.models import CapacityRecord
from src.ui.group_view import group_records_by_substation


def test_group_records_empty():
    assert group_records_by_substation([]) == {}


def test_group_records_single():
    record = CapacityRecord(
        substCd="S01",
        substNm="Sub A",
        mtrNo="MTR 1",
        dlNm="DL 1",
        vol1="1000",
        vol2="500",
        vol3="100",
    )
    grouped = group_records_by_substation([record])

    assert "Sub A" in grouped
    subst_data = grouped["Sub A"]
    assert "MTR 1" in subst_data
    assert len(subst_data["MTR 1"]) == 1
    assert subst_data["MTR 1"][0] == record


def test_group_records_hierarchy():
    # Sub A -> MTR 1 -> DL 1, DL 2
    # Sub A -> MTR 2 -> DL 3
    # Sub B -> MTR 3 -> DL 4

    r1 = CapacityRecord(
        substCd="S01",
        substNm="Sub A",
        mtrNo="MTR 1",
        dlNm="DL 1",
        vol1="1000",
        vol2="500",
        vol3="100",
    )
    r2 = CapacityRecord(
        substCd="S01",
        substNm="Sub A",
        mtrNo="MTR 1",
        dlNm="DL 2",
        vol1="1000",
        vol2="500",
        vol3="200",
    )
    r3 = CapacityRecord(
        substCd="S01",
        substNm="Sub A",
        mtrNo="MTR 2",
        dlNm="DL 3",
        vol1="1000",
        vol2="600",
        vol3="300",
    )
    r4 = CapacityRecord(
        substCd="S02",
        substNm="Sub B",
        mtrNo="MTR 3",
        dlNm="DL 4",
        vol1="2000",
        vol2="700",
        vol3="400",
    )

    grouped = group_records_by_substation([r1, r2, r3, r4])

    # Check Sub A
    assert "Sub A" in grouped
    assert len(grouped["Sub A"]) == 2
    assert "MTR 1" in grouped["Sub A"]
    assert "MTR 2" in grouped["Sub A"]

    # Check Sub A -> MTR 1
    mtr1 = grouped["Sub A"]["MTR 1"]
    assert len(mtr1) == 2
    assert r1 in mtr1
    assert r2 in mtr1

    # Check Sub B
    assert "Sub B" in grouped
    assert len(grouped["Sub B"]) == 1
