"""무료 지오메트리/지오코딩 유틸.

주의:
- KEPCO OpenAPI/스크래핑 결과에는 선로 좌표/선형이 없다.
- 따라서 이 모듈은 공개(OpenStreetMap) 데이터 기반으로 '전력선(전송/배전 혼합)'을
  지도에 오버레이하는 목적의 보조 기능만 제공한다.
- 공개 데이터는 지역별 커버리지/정확도가 다르며, KEPCO DL(피더)와 1:1 매칭을
  보장하지 않는다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, cast

import httpx
import streamlit as st

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeoBBox:
    """(south, west, north, east)"""

    south: float
    west: float
    north: float
    east: float


@dataclass(frozen=True)
class GeoPolyline:
    name: str
    voltage: str
    power: str
    lats: list[float]
    lons: list[float]


def _normalize_voltage(v: object) -> str:
    s = str(v or "").strip()
    return s


def parse_voltage_value(voltage: str) -> int | None:
    """OSM voltage 태그를 정수(V)로 파싱.

    예:
    - "22900" -> 22900
    - "154000;345000" -> 154000 (최소값)
    - "22 kV" -> 22000 (근사)
    - ""/"unknown" -> None
    """

    raw = (voltage or "").strip().lower()
    if not raw or raw in {"unknown", "n/a", "na"}:
        return None

    # kv 단위가 섞인 경우
    if "kv" in raw:
        raw = raw.replace("kv", "")
        try:
            val = float(raw.strip())
            if math.isfinite(val) and val > 0:
                return int(round(val * 1000.0))
        except Exception:
            return None

    # 세미콜론 등으로 여러 값이 들어올 수 있음
    parts = [p.strip() for p in raw.replace(",", ";").split(";") if p.strip()]
    nums: list[int] = []
    for p in parts:
        # 숫자 외 제거
        cleaned = "".join(ch for ch in p if ch.isdigit())
        if not cleaned:
            continue
        try:
            n = int(cleaned)
        except Exception:
            continue
        if n > 0:
            nums.append(n)

    if not nums:
        return None
    return min(nums)


def _overpass_query_power_lines(bbox: GeoBBox) -> str:
    # power=line / minor_line / cable을 최대한 포함
    return (
        "[out:json][timeout:25];"
        "("
        f"way['power'~'^(line|minor_line|cable)$']({bbox.south},{bbox.west},{bbox.north},{bbox.east});"
        f"relation['power'~'^(line|minor_line|cable)$']({bbox.south},{bbox.west},{bbox.north},{bbox.east});"
        ");"
        "(._;>;);"
        "out body;"
    )


def parse_overpass_power_lines(data: dict) -> list[GeoPolyline]:
    """Overpass JSON을 폴리라인 리스트로 변환.

    네트워크 호출 없이 테스트 가능하도록 파싱 로직을 분리한다.
    """

    elements = data.get("elements")
    if not isinstance(elements, list):
        return []

    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        t = el.get("type")
        if t == "node":
            node_id = el.get("id")
            lat = el.get("lat")
            lon = el.get("lon")
            if (
                isinstance(node_id, int)
                and isinstance(lat, (int, float))
                and isinstance(lon, (int, float))
            ):
                nodes[node_id] = (float(lat), float(lon))
        elif t == "way":
            ways.append(el)

    polylines: list[GeoPolyline] = []
    for w in ways:
        node_ids = w.get("nodes")
        if not isinstance(node_ids, list) or not node_ids:
            continue

        tags = w.get("tags")
        tags = tags if isinstance(tags, dict) else {}
        power = str(tags.get("power") or "")
        name = str(tags.get("name") or tags.get("ref") or "(osm power line)")
        voltage = _normalize_voltage(tags.get("voltage"))

        lats: list[float] = []
        lons: list[float] = []
        for nid in node_ids:
            if not isinstance(nid, int):
                continue
            coord = nodes.get(nid)
            if coord is None:
                continue
            lat, lon = coord
            lats.append(lat)
            lons.append(lon)

        if len(lats) < 2:
            continue
        polylines.append(GeoPolyline(name=name, voltage=voltage, power=power, lats=lats, lons=lons))

    return polylines


@st.cache_data(ttl=86400, show_spinner=False)
def geocode_korea_region(query: str) -> tuple[float, float] | None:
    """Nominatim으로 지역명을 위경도로 지오코딩.

    무료 엔드포인트는 레이트리밋이 있으므로 cache + 타임아웃을 강하게 둔다.
    """

    q = (query or "").strip()
    if not q:
        return None

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": q, "format": "json", "limit": 1}
    headers = {"User-Agent": "overhead-line-scanner/0.3 (contact: none)"}

    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            items = r.json()
    except Exception as exc:
        logger.warning("Nominatim geocode 실패: %s", exc)
        return None

    if not isinstance(items, list) or not items:
        return None

    top = items[0]
    if not isinstance(top, dict):
        return None

    top_dict = cast("dict[str, Any]", top)

    lat_raw = top_dict.get("lat")
    lon_raw = top_dict.get("lon")
    if not isinstance(lat_raw, (int, float, str)) or not isinstance(lon_raw, (int, float, str)):
        return None

    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except Exception:
        return None
    return lat, lon


def make_bbox(lat: float, lon: float, radius_km: float) -> GeoBBox:
    # 간단 근사: 1도 위도 ~= 111km
    r = float(max(1.0, radius_km))
    dlat = r / 111.0
    # 경도는 위도에 따라 스케일
    cos_lat = max(0.2, abs(math.cos(math.radians(lat))))
    dlon = r / (111.0 * cos_lat)
    return GeoBBox(south=lat - dlat, west=lon - dlon, north=lat + dlat, east=lon + dlon)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_osm_power_lines(bbox: GeoBBox) -> list[GeoPolyline]:
    """Overpass에서 bbox 내 전력선 geometry를 가져온다."""

    query = _overpass_query_power_lines(bbox)
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    headers = {"User-Agent": "overhead-line-scanner/0.3 (contact: none)"}

    last_exc: Exception | None = None
    for url in endpoints:
        try:
            with httpx.Client(timeout=30.0, headers=headers) as client:
                r = client.post(url, content=query.encode("utf-8"))
                r.raise_for_status()
                data = r.json()
            lines = parse_overpass_power_lines(data)
            if lines:
                return lines
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc is not None:
        logger.warning("Overpass fetch 실패(모든 엔드포인트): %s", last_exc)
    return []
