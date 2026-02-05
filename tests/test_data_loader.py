"""data_loader 모듈 단위 테스트."""

from __future__ import annotations

import json

import pandas as pd

from src.data.data_loader import (
    load_records_from_dataframe,
    load_records_from_uploaded_file,
    load_sample_records,
)
from src.data.models import CapacityRecord


class TestLoadSampleRecords:
    def test_returns_non_empty_list(self) -> None:
        records = load_sample_records()
        assert len(records) > 0

    def test_records_are_capacity_record(self) -> None:
        records = load_sample_records()
        for r in records:
            assert isinstance(r, CapacityRecord)

    def test_sample_data_has_valid_capacities(self) -> None:
        records = load_sample_records()
        for r in records:
            assert r.substation_capacity >= 0
            assert r.transformer_capacity >= 0
            assert r.dl_capacity >= 0


class TestLoadRecordsFromDataframe:
    def test_api_column_names(self) -> None:
        """한전 API 원본 컬럼명으로 된 DataFrame."""
        df = pd.DataFrame(
            [
                {
                    "substCd": "S001",
                    "substNm": "천안",
                    "mtrNo": "#1",
                    "dlNm": "불당1",
                    "vol1": "20000",
                    "vol2": "10000",
                    "vol3": "3200",
                }
            ]
        )
        records = load_records_from_dataframe(df)
        assert len(records) == 1
        assert records[0].subst_nm == "천안"
        assert records[0].dl_capacity == 3200

    def test_korean_column_names(self) -> None:
        """한글 컬럼명으로 된 DataFrame."""
        df = pd.DataFrame(
            [
                {
                    "변전소명": "풍세",
                    "변압기번호": "#1",
                    "DL명": "공원",
                    "변전소여유(kW)": "35000",
                    "변압기여유(kW)": "18000",
                    "DL여유(kW)": "4100",
                }
            ]
        )
        records = load_records_from_dataframe(df)
        assert len(records) == 1
        assert records[0].subst_nm == "풍세"
        assert records[0].dl_capacity == 4100

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame()
        records = load_records_from_dataframe(df)
        assert records == []

    def test_unknown_columns(self) -> None:
        df = pd.DataFrame([{"random_col": "value", "another": "123"}])
        records = load_records_from_dataframe(df)
        assert records == []


class TestLoadRecordsFromUploadedFile:
    def test_csv_file(self) -> None:
        csv_content = "substNm,mtrNo,dlNm,vol1,vol2,vol3\n천안,#1,불당1,20000,10000,3200\n"
        records = load_records_from_uploaded_file(
            csv_content.encode("utf-8-sig"),
            "test.csv",
        )
        assert len(records) == 1
        assert records[0].subst_nm == "천안"

    def test_json_file(self) -> None:
        data = [
            {
                "substNm": "천안",
                "mtrNo": "#1",
                "dlNm": "불당1",
                "vol1": "20000",
                "vol2": "10000",
                "vol3": "3200",
            }
        ]
        json_bytes = json.dumps(data).encode("utf-8")
        records = load_records_from_uploaded_file(json_bytes, "test.json")
        assert len(records) == 1
        assert records[0].subst_nm == "천안"

    def test_unsupported_format(self) -> None:
        records = load_records_from_uploaded_file(b"some data", "test.txt")
        assert records == []

    def test_invalid_csv(self) -> None:
        records = load_records_from_uploaded_file(b"\xff\xfe", "test.csv")
        assert records == []
