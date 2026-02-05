"""데이터 로더 모듈 — CSV/Excel 파일 업로드 및 샘플 데이터 로드.

한전 API 없이 동작하도록 설계되었다.
사용자가 한전ON에서 다운로드한 CSV/Excel 파일을 업로드하거나,
내장된 샘플 데이터를 사용할 수 있다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.data.models import CapacityRecord

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

_SAMPLE_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sample_capacity.json"

# CSV/Excel 컬럼 매핑: (한전 원본 컬럼명) → (CapacityRecord alias 필드명)
# 사용자가 한전ON에서 다운로드한 파일의 컬럼명이 다양할 수 있으므로 유연하게 매핑
_COLUMN_ALIASES: dict[str, list[str]] = {
    "substCd": ["substCd", "변전소코드", "변전소 코드", "subst_cd"],
    "substNm": ["substNm", "변전소명", "변전소 명", "subst_nm"],
    "jsSubstPwr": ["jsSubstPwr", "변전소용량", "변전소 용량", "변전소용량(kW)", "js_subst_pwr"],
    "substPwr": [
        "substPwr",
        "변전소누적연계",
        "변전소 누적연계용량",
        "변전소누적연계(kW)",
        "subst_pwr",
    ],
    "mtrNo": ["mtrNo", "변압기번호", "변압기 번호", "MTR번호", "mtr_no"],
    "jsMtrPwr": ["jsMtrPwr", "변압기용량", "변압기 용량", "변압기용량(kW)", "js_mtr_pwr"],
    "mtrPwr": ["mtrPwr", "변압기누적연계", "변압기 누적연계용량", "변압기누적연계(kW)", "mtr_pwr"],
    "dlCd": ["dlCd", "DL코드", "DL 코드", "dl_cd"],
    "dlNm": ["dlNm", "DL명", "DL 명", "dl_nm", "배전선로명"],
    "jsDlPwr": ["jsDlPwr", "DL용량", "DL 용량", "DL용량(kW)", "js_dl_pwr"],
    "dlPwr": ["dlPwr", "DL누적연계", "DL 누적연계용량", "DL누적연계(kW)", "dl_pwr"],
    "vol1": ["vol1", "변전소여유", "변전소 여유용량", "변전소여유(kW)", "변전소여유용량(kW)"],
    "vol2": ["vol2", "변압기여유", "변압기 여유용량", "변압기여유(kW)", "변압기여유용량(kW)"],
    "vol3": ["vol3", "DL여유", "DL 여유용량", "DL여유(kW)", "DL여유용량(kW)"],
}


def load_sample_records() -> list[CapacityRecord]:
    """내장된 샘플 데이터를 로드하여 CapacityRecord 리스트로 반환."""
    try:
        raw = json.loads(_SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
        records = []
        for item in raw:
            try:
                records.append(CapacityRecord(**item))
            except Exception as e:
                logger.warning("샘플 레코드 파싱 실패 (skip): %s — %s", item, e)
        logger.info("샘플 데이터 로드 완료: %d건", len(records))
        return records
    except FileNotFoundError:
        logger.error("샘플 데이터 파일 없음: %s", _SAMPLE_DATA_PATH)
        return []
    except Exception as e:
        logger.error("샘플 데이터 로드 실패: %s", e)
        return []


def _resolve_column(df_columns: list[str], target_key: str) -> str | None:
    """DataFrame 컬럼 중 target_key에 매핑되는 실제 컬럼명을 찾아 반환."""
    aliases = _COLUMN_ALIASES.get(target_key, [target_key])
    for alias in aliases:
        if alias in df_columns:
            return alias
    return None


def load_records_from_dataframe(df: pd.DataFrame) -> list[CapacityRecord]:
    """pandas DataFrame을 CapacityRecord 리스트로 변환.

    컬럼명이 한전 API 원본, 한글, snake_case 등 다양한 형식이어도
    자동으로 매핑하여 파싱한다.
    """
    columns = df.columns.tolist()
    column_map: dict[str, str] = {}

    for target_key in _COLUMN_ALIASES:
        resolved = _resolve_column(columns, target_key)
        if resolved:
            column_map[target_key] = resolved

    if not column_map:
        logger.error("업로드 파일에서 인식 가능한 컬럼이 없습니다: %s", columns)
        return []

    records: list[CapacityRecord] = []
    for _, row in df.iterrows():
        item: dict[str, str] = {}
        for target_key, src_col in column_map.items():
            val = row.get(src_col)
            item[target_key] = str(val) if val is not None else ""
        try:
            records.append(CapacityRecord(**item))
        except Exception as e:
            logger.warning("레코드 파싱 실패 (skip): %s — %s", item, e)

    logger.info("파일 데이터 로드 완료: %d건", len(records))
    return records


def load_records_from_uploaded_file(file_content: bytes, filename: str) -> list[CapacityRecord]:
    """업로드된 파일(CSV/Excel/JSON)을 CapacityRecord 리스트로 변환."""
    import pandas as pd

    lower_name = filename.lower()
    try:
        if lower_name.endswith(".csv"):
            import io

            text = file_content.decode("utf-8-sig")
            df = pd.read_csv(io.StringIO(text))
        elif lower_name.endswith((".xlsx", ".xls")):
            import io

            df = pd.read_excel(io.BytesIO(file_content))
        elif lower_name.endswith(".json"):
            raw = json.loads(file_content.decode("utf-8"))
            if isinstance(raw, list):
                records = []
                for item in raw:
                    try:
                        records.append(CapacityRecord(**item))
                    except Exception as e:
                        logger.warning("JSON 레코드 파싱 실패 (skip): %s — %s", item, e)
                return records
            df = pd.DataFrame(raw if isinstance(raw, list) else [raw])
        else:
            logger.error("지원하지 않는 파일 형식: %s", filename)
            return []
    except Exception as e:
        logger.error("파일 읽기 실패 (%s): %s", filename, e)
        return []

    # DataFrame 컬럼의 공백 제거
    df.columns = [str(c).strip() for c in df.columns]
    return load_records_from_dataframe(df)
