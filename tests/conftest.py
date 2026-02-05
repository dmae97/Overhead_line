import pytest

from src.data.models import CapacityRecord, RegionInfo

SAMPLE_API_RESPONSE = [
    {
        "substCd": "S001",
        "substNm": "천안",
        "jsSubstPwr": "50000",
        "substPwr": "30000",
        "mtrNo": "#1",
        "jsMtrPwr": "20000",
        "mtrPwr": "10000",
        "dlCd": "D001",
        "dlNm": "불당1",
        "jsDlPwr": "10000",
        "dlPwr": "6800",
        "vol1": "20000",
        "vol2": "10000",
        "vol3": "3200",
    },
    {
        "substCd": "S001",
        "substNm": "천안",
        "jsSubstPwr": "50000",
        "substPwr": "30000",
        "mtrNo": "#1",
        "jsMtrPwr": "20000",
        "mtrPwr": "10000",
        "dlCd": "D002",
        "dlNm": "불당2",
        "jsDlPwr": "10000",
        "dlPwr": "8500",
        "vol1": "20000",
        "vol2": "10000",
        "vol3": "1500",
    },
    {
        "substCd": "S001",
        "substNm": "천안",
        "jsSubstPwr": "50000",
        "substPwr": "30000",
        "mtrNo": "#2",
        "jsMtrPwr": "20000",
        "mtrPwr": "20000",
        "dlCd": "D003",
        "dlNm": "쌍용1",
        "jsDlPwr": "10000",
        "dlPwr": "9800",
        "vol1": "20000",
        "vol2": "0",
        "vol3": "200",
    },
]


@pytest.fixture
def sample_records() -> list[CapacityRecord]:
    return [CapacityRecord(**item) for item in SAMPLE_API_RESPONSE]


@pytest.fixture
def sample_region() -> RegionInfo:
    return RegionInfo(sido="충청남도", sigungu="천안시 서북구", dong="불당동")
