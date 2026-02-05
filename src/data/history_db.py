from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from src.core.config import settings
from src.core.exceptions import HistoryDBError
from src.data.models import QueryHistoryRecord

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS query_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    region_name TEXT    NOT NULL,
    metro_cd    TEXT    NOT NULL,
    city_cd     TEXT    NOT NULL,
    dong        TEXT    NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    queried_at  TEXT    NOT NULL
);
"""


class HistoryRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or settings.history_db_path
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        try:
            conn = self._connect()
            try:
                conn.execute(_CREATE_TABLE_SQL)
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.exception("조회 이력 테이블 생성 실패")
            raise HistoryDBError(f"테이블 생성 실패: {exc}") from exc

    def save(self, record: QueryHistoryRecord) -> int:
        sql = """
            INSERT INTO query_history (
                region_name, metro_cd, city_cd, dong, result_count, queried_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """
        timestamp = record.queried_at.isoformat()
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    sql,
                    (
                        record.region_name,
                        record.metro_cd,
                        record.city_cd,
                        record.dong,
                        record.result_count,
                        timestamp,
                    ),
                )
                conn.commit()
                row_id = cursor.lastrowid
                assert row_id is not None
                return row_id
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.exception("조회 이력 저장 실패")
            raise HistoryDBError(f"이력 저장 실패: {exc}") from exc

    def list_recent(self, limit: int = 20) -> list[QueryHistoryRecord]:
        sql = "SELECT * FROM query_history ORDER BY queried_at DESC LIMIT ?"
        try:
            conn = self._connect()
            try:
                rows = conn.execute(sql, (limit,)).fetchall()
                return [self._row_to_record(row) for row in rows]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.exception("조회 이력 목록 조회 실패")
            raise HistoryDBError(f"이력 조회 실패: {exc}") from exc

    def delete(self, record_id: int) -> bool:
        sql = "DELETE FROM query_history WHERE id = ?"
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(sql, (record_id,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.exception("조회 이력 삭제 실패")
            raise HistoryDBError(f"이력 삭제 실패: {exc}") from exc

    def count(self) -> int:
        sql = "SELECT COUNT(*) FROM query_history"
        try:
            conn = self._connect()
            try:
                row = conn.execute(sql).fetchone()
                return int(row[0])
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.exception("조회 이력 건수 조회 실패")
            raise HistoryDBError(f"이력 건수 조회 실패: {exc}") from exc

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> QueryHistoryRecord:
        return QueryHistoryRecord(
            id=row["id"],
            region_name=row["region_name"],
            metro_cd=row["metro_cd"],
            city_cd=row["city_cd"],
            dong=row["dong"],
            result_count=row["result_count"],
            queried_at=datetime.fromisoformat(row["queried_at"]),
        )
