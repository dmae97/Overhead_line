from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from src.data.history_db import HistoryRepository
from src.data.models import QueryHistoryRecord

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_repo(tmp_path: Path) -> HistoryRepository:
    return HistoryRepository(db_path=tmp_path / "test_history.db")


def _make_record(
    region_name: str = "충청남도 천안시 서북구 불당동",
    metro_cd: str = "44",
    city_cd: str = "131",
    dong: str = "불당동",
    result_count: int = 3,
    queried_at: datetime | None = None,
) -> QueryHistoryRecord:
    kwargs: dict[str, str | int | datetime] = {
        "region_name": region_name,
        "metro_cd": metro_cd,
        "city_cd": city_cd,
        "dong": dong,
        "result_count": result_count,
    }
    if queried_at is not None:
        kwargs["queried_at"] = queried_at
    return QueryHistoryRecord.model_validate(kwargs)


class TestSave:
    def test_save_returns_positive_id(self, tmp_repo: HistoryRepository) -> None:
        row_id = tmp_repo.save(_make_record())
        assert row_id >= 1

    def test_save_increments_id(self, tmp_repo: HistoryRepository) -> None:
        id1 = tmp_repo.save(_make_record())
        id2 = tmp_repo.save(_make_record(region_name="서울특별시 강남구"))
        assert id2 > id1

    def test_save_persists_all_fields(self, tmp_repo: HistoryRepository) -> None:
        ts = datetime(2026, 1, 15, 9, 30, 0)
        record = _make_record(queried_at=ts)
        tmp_repo.save(record)

        rows = tmp_repo.list_recent(limit=1)
        assert len(rows) == 1
        saved = rows[0]
        assert saved.region_name == "충청남도 천안시 서북구 불당동"
        assert saved.metro_cd == "44"
        assert saved.city_cd == "131"
        assert saved.dong == "불당동"
        assert saved.result_count == 3
        assert saved.queried_at == ts


class TestListRecent:
    def test_empty_db_returns_empty_list(self, tmp_repo: HistoryRepository) -> None:
        assert tmp_repo.list_recent() == []

    def test_returns_newest_first(self, tmp_repo: HistoryRepository) -> None:
        tmp_repo.save(_make_record(queried_at=datetime(2026, 1, 1)))
        tmp_repo.save(_make_record(queried_at=datetime(2026, 1, 3)))
        tmp_repo.save(_make_record(queried_at=datetime(2026, 1, 2)))

        rows = tmp_repo.list_recent()
        timestamps = [r.queried_at for r in rows]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_limit_caps_results(self, tmp_repo: HistoryRepository) -> None:
        for i in range(5):
            tmp_repo.save(_make_record(result_count=i))

        assert len(tmp_repo.list_recent(limit=3)) == 3

    def test_default_limit_is_20(self, tmp_repo: HistoryRepository) -> None:
        for i in range(25):
            tmp_repo.save(_make_record(result_count=i))

        assert len(tmp_repo.list_recent()) == 20


class TestDelete:
    def test_delete_existing_returns_true(self, tmp_repo: HistoryRepository) -> None:
        row_id = tmp_repo.save(_make_record())
        assert tmp_repo.delete(row_id) is True

    def test_delete_nonexistent_returns_false(self, tmp_repo: HistoryRepository) -> None:
        assert tmp_repo.delete(9999) is False

    def test_delete_removes_from_db(self, tmp_repo: HistoryRepository) -> None:
        row_id = tmp_repo.save(_make_record())
        tmp_repo.delete(row_id)
        assert tmp_repo.count() == 0

    def test_delete_only_target_row(self, tmp_repo: HistoryRepository) -> None:
        id1 = tmp_repo.save(_make_record(region_name="A"))
        tmp_repo.save(_make_record(region_name="B"))
        tmp_repo.delete(id1)

        remaining = tmp_repo.list_recent()
        assert len(remaining) == 1
        assert remaining[0].region_name == "B"


class TestCount:
    def test_empty_db_returns_zero(self, tmp_repo: HistoryRepository) -> None:
        assert tmp_repo.count() == 0

    def test_count_after_inserts(self, tmp_repo: HistoryRepository) -> None:
        tmp_repo.save(_make_record())
        tmp_repo.save(_make_record())
        assert tmp_repo.count() == 2

    def test_count_after_delete(self, tmp_repo: HistoryRepository) -> None:
        row_id = tmp_repo.save(_make_record())
        tmp_repo.save(_make_record())
        tmp_repo.delete(row_id)
        assert tmp_repo.count() == 1


class TestTableCreation:
    def test_auto_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "history.db"
        repo = HistoryRepository(db_path=deep_path)
        repo.save(_make_record())
        assert deep_path.exists()

    def test_reuses_existing_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "reuse.db"
        repo1 = HistoryRepository(db_path=db_path)
        repo1.save(_make_record())

        repo2 = HistoryRepository(db_path=db_path)
        assert repo2.count() == 1


class TestIso8601Timestamps:
    def test_stored_as_iso_string(self, tmp_path: Path) -> None:
        import sqlite3

        db_path = tmp_path / "ts.db"
        repo = HistoryRepository(db_path=db_path)
        ts = datetime(2026, 2, 5, 14, 30, 0)
        repo.save(_make_record(queried_at=ts))

        conn = sqlite3.connect(str(db_path))
        raw = conn.execute("SELECT queried_at FROM query_history").fetchone()[0]
        conn.close()
        assert raw == "2026-02-05T14:30:00"

    def test_roundtrip_preserves_datetime(self, tmp_repo: HistoryRepository) -> None:
        ts = datetime(2026, 6, 15, 8, 0, 0)
        tmp_repo.save(_make_record(queried_at=ts))
        rows = tmp_repo.list_recent(limit=1)
        assert rows[0].queried_at == ts


class TestQueryHistoryRecordModel:
    def test_defaults(self) -> None:
        record = QueryHistoryRecord(
            region_name="서울특별시 강남구",
            metro_cd="11",
            city_cd="680",
        )
        assert record.id is None
        assert record.dong == ""
        assert record.result_count == 0
        assert isinstance(record.queried_at, datetime)

    def test_all_fields(self) -> None:
        ts = datetime(2026, 1, 1)
        record = QueryHistoryRecord(
            id=42,
            region_name="충청남도 천안시",
            metro_cd="44",
            city_cd="131",
            dong="불당동",
            result_count=5,
            queried_at=ts,
        )
        assert record.id == 42
        assert record.queried_at == ts
