"""kepco_api 모듈 단위 테스트 (httpx MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import KepcoAPIError, KepcoNoDataError
from src.data.kepco_api import KepcoApiClient
from src.data.models import AddressParams


def _make_client_with_transport(transport: httpx.BaseTransport) -> KepcoApiClient:
    http_client = httpx.Client(transport=transport, timeout=1.0)
    return KepcoApiClient(api_key="x" * 40, client=http_client)


def test_missing_api_key_raises() -> None:
    with pytest.raises(KepcoAPIError):
        KepcoApiClient(api_key="")


def test_fetch_capacity_parses_data_list() -> None:
    payload = {
        "data": [
            {
                # 실제 OpenAPI는 숫자 필드를 int로 주는 경우가 많다.
                "substCd": 2462,
                "substNm": "공주",
                "mtrNo": "#1",
                "dlNm": "정안",
                "vol1": 98973,
                "vol2": 0,
                "vol3": 1199,
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = _make_client_with_transport(transport)

    records = client.fetch_capacity(AddressParams(metro_cd="44", city_cd="131"))
    assert len(records) == 1
    assert records[0].subst_nm == "공주"
    assert records[0].dl_capacity == 1199

    client.close()


def test_fetch_capacity_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    client = _make_client_with_transport(transport)

    with pytest.raises(KepcoAPIError) as exc:
        client.fetch_capacity(AddressParams(metro_cd="44", city_cd="131"))
    assert exc.value.status_code == 401

    client.close()


def test_fetch_capacity_invalid_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    transport = httpx.MockTransport(handler)
    client = _make_client_with_transport(transport)

    with pytest.raises(KepcoAPIError):
        client.fetch_capacity(AddressParams(metro_cd="44", city_cd="131"))

    client.close()


def test_fetch_capacity_no_data_raises() -> None:
    payload = {"data": [], "message": "no results"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = _make_client_with_transport(transport)

    with pytest.raises(KepcoNoDataError):
        client.fetch_capacity(AddressParams(metro_cd="44", city_cd="131"))

    client.close()
