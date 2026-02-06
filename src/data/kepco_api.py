"""한전 전력데이터 개방포털 OpenAPI 클라이언트.

엔드포인트: https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do
인증: API Key(KEPCO_API_KEY)
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.core.config import settings
from src.core.exceptions import KepcoAPIError, KepcoNoDataError
from src.data.models import AddressParams, CapacityRecord


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    return []


class KepcoApiClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        delay_seconds: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = (api_key if api_key is not None else settings.kepco_api_key).strip()
        self._base_url = base_url or settings.kepco_api_base_url
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.kepco_api_timeout_seconds
        )
        self._delay = (
            delay_seconds if delay_seconds is not None else settings.kepco_api_delay_seconds
        )

        if not self._api_key:
            raise KepcoAPIError(
                "KEPCO_API_KEY가 설정되지 않았습니다. "
                "환경변수(.env 포함) 또는 Streamlit Secrets에 KEPCO_API_KEY를 설정하세요.",
                status_code=None,
            )

        self._client = client or httpx.Client(timeout=self._timeout)

    def close(self) -> None:
        self._client.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    def fetch_capacity(self, params: AddressParams) -> list[CapacityRecord]:
        """지정 지역의 배전선로 여유용량을 조회."""
        if self._delay > 0:
            time.sleep(self._delay)

        query = {
            "metroCd": params.metro_cd,
            "cityCd": params.city_cd,
            "addrLidong": params.dong,
            "addrLi": params.ri,
            "addrJibun": params.jibun,
            "apiKey": self._api_key,
            "returnType": "json",
        }

        try:
            resp = self._client.get(self._base_url, params=query)
        except httpx.TimeoutException as exc:
            raise KepcoAPIError("한전 API 요청 시간 초과", status_code=None) from exc
        except httpx.NetworkError as exc:
            raise KepcoAPIError("한전 API 네트워크 오류", status_code=None) from exc

        if resp.status_code >= 400:
            raise KepcoAPIError(
                f"한전 API HTTP 오류: {resp.status_code}",
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise KepcoAPIError(
                "한전 API 응답 JSON 파싱 실패", status_code=resp.status_code
            ) from exc

        raw_records = _extract_records(payload)
        if not raw_records:
            # 일부 케이스는 응답에 에러 메시지 필드가 존재할 수 있음
            msg = None
            if isinstance(payload, dict):
                msg = payload.get("message") or payload.get("resultMsg")
            raise KepcoNoDataError(
                f"한전 API 응답에 데이터가 없습니다{f': {msg}' if msg else ''}",
                status_code=resp.status_code,
            )

        records: list[CapacityRecord] = []
        for item in raw_records:
            try:
                records.append(CapacityRecord(**item))
            except Exception:
                # 단일 레코드 문제로 전체 실패 방지
                continue
        if not records:
            raise KepcoAPIError(
                "한전 API 응답 파싱 실패 (레코드 검증 실패)",
                status_code=resp.status_code,
            )
        return records
