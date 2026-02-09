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
    sigungu     TEXT    NOT NULL DEFAULT '',
    sido        TEXT    NOT NULL DEFAULT '',
    mode        TEXT    NOT NULL DEFAULT '',
    jibun       TEXT    NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    connectable_count INTEGER NOT NULL DEFAULT 0,
    not_connectable_count INTEGER NOT NULL DEFAULT 0,
    min_cap_min INTEGER NOT NULL DEFAULT 0,
    min_cap_median INTEGER NOT NULL DEFAULT 0,
    min_cap_max INTEGER NOT NULL DEFAULT 0,
    queried_at  TEXT    NOT NULL
);
"""


_MIGRATION_COLUMNS: list[tuple[str, str]] = [
    ("sigungu", "TEXT NOT NULL DEFAULT ''"),
    ("sido", "TEXT NOT NULL DEFAULT ''"),
    ("mode", "TEXT NOT NULL DEFAULT ''"),
    ("jibun", "TEXT NOT NULL DEFAULT ''"),
    ("connectable_count", "INTEGER NOT NULL DEFAULT 0"),
    ("not_connectable_count", "INTEGER NOT NULL DEFAULT 0"),
    ("min_cap_min", "INTEGER NOT NULL DEFAULT 0"),
    ("min_cap_median", "INTEGER NOT NULL DEFAULT 0"),
    ("min_cap_max", "INTEGER NOT NULL DEFAULT 0"),
]


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
                self._ensure_columns(conn)
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.exception("조회 이력 테이블 생성 실패")
            raise HistoryDBError(f"테이블 생성 실패: {exc}") from exc

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        """기존 DB에 누락된 컬럼이 있으면 비파괴적으로 추가한다."""
        try:
            rows = conn.execute("PRAGMA table_info(query_history)").fetchall()
            existing = {str(r[1]) for r in rows}
            for name, sql_type in _MIGRATION_COLUMNS:
                if name in existing:
                    continue
                conn.execute(f"ALTER TABLE query_history ADD COLUMN {name} {sql_type}")
        except sqlite3.Error:
            # 마이그레이션 실패는 치명적이지만, 원인을 명확히 남긴다.
            logger.exception("조회 이력 테이블 마이그레이션 실패")
            raise

    def save(self, record: QueryHistoryRecord) -> int:
        sql = """
            INSERT INTO query_history (
                region_name, metro_cd, city_cd, dong, sigungu, sido, mode, jibun,
                result_count,
                connectable_count, not_connectable_count,
                min_cap_min, min_cap_median, min_cap_max,
                queried_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        record.sigungu,
                        record.sido,
                        record.mode,
                        record.jibun,
                        record.result_count,
                        record.connectable_count,
                        record.not_connectable_count,
                        record.min_cap_min,
                        record.min_cap_median,
                        record.min_cap_max,
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
        keys = set(row.keys())

        def _get_str(name: str, default: str = "") -> str:
            if name not in keys:
                return default
            val = row[name]
            return "" if val is None else str(val)

        def _get_int(name: str, default: int = 0) -> int:
            if name not in keys:
                return default
            val = row[name]
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        return QueryHistoryRecord(
            id=row["id"],
            region_name=row["region_name"],
            metro_cd=row["metro_cd"],
            city_cd=row["city_cd"],
            dong=row["dong"],
            result_count=row["result_count"],
            sigungu=_get_str("sigungu"),
            sido=_get_str("sido"),
            mode=_get_str("mode"),
            jibun=_get_str("jibun"),
            connectable_count=_get_int("connectable_count"),
            not_connectable_count=_get_int("not_connectable_count"),
            min_cap_min=_get_int("min_cap_min"),
            min_cap_median=_get_int("min_cap_median"),
            min_cap_max=_get_int("min_cap_max"),
            queried_at=datetime.fromisoformat(row["queried_at"]),
        )
