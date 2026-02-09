"""지도 시각화 UI.

현재 OpenAPI/스크래핑 결과에는 선로의 실제 지리 좌표/경로가 포함되지 않는다.

따라서 이 모듈은 두 가지 '근사' 시각화를 제공한다.
1) 조회 이력(지역 단위): 시/도 중심점 기반 점 표시
2) 현재 조회 선로(레코드): 지역 중심점 주변에 안정적(jitter)으로 점을 배치하고,
   변전소/변압기 그룹 내 DL들을 용량(색상) 기준으로 연결하는 '스키매틱' 연결선

정확한 선로 경로/좌표가 필요하면 별도의 좌표 데이터셋(예: centroid/지오코딩)
주입이 필요하다.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import TYPE_CHECKING, TypedDict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.data.geo import fetch_osm_power_lines, geocode_korea_region, make_bbox, parse_voltage_value
from src.ui.components import capacity_color, format_capacity

if TYPE_CHECKING:
    from src.data.models import CapacityRecord, QueryHistoryRecord, RegionInfo


class _PointInfo(TypedDict):
    lat: float
    lon: float
    cap: int
    color: str
    hover: str


def _build_schematic_points_and_segments(
    records: list[CapacityRecord],
    base_lat: float,
    base_lon: float,
    spread: float,
) -> tuple[
    dict[tuple[str, str], list[CapacityRecord]],
    dict[str, _PointInfo],
    dict[str, dict[str, list[float | None]]],
]:
    """좌표 없는 레코드에서 스키매틱 점/연결선을 생성한다.

    Streamlit 렌더링과 분리해서 로직을 테스트 가능하게 유지한다.
    """

    grouped: dict[tuple[str, str], list[CapacityRecord]] = defaultdict(list)
    for r in records:
        subst_key = (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)"
        mtr_key = (r.mtr_no or "").strip() or "(unknown-mtr)"
        grouped[(subst_key, mtr_key)].append(r)

    points: dict[str, _PointInfo] = {}

    for (subst_key, mtr_key), items in grouped.items():
        group_center_key = f"{base_lat:.4f}:{base_lon:.4f}:{subst_key}:{mtr_key}"
        g_lat, g_lon = _jitter_point(base_lat, base_lon, group_center_key, radius_deg=float(spread))

        for r in items:
            dl_key = (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)"
            point_key = f"{subst_key}:{mtr_key}:{dl_key}"
            lat, lon = _jitter_point(g_lat, g_lon, point_key, radius_deg=float(spread) * 0.45)
            cap = int(r.min_capacity)
            points[point_key] = {
                "lat": float(lat),
                "lon": float(lon),
                "cap": cap,
                "color": capacity_color(cap),
                "hover": "<br>".join(
                    [
                        f"<b>{dl_key}</b>",
                        f"substation: {subst_key}",
                        f"transformer: {mtr_key}",
                        f"min_capacity: {cap:,} kW",
                    ]
                ),
            }

    segments_by_color: dict[str, dict[str, list[float | None]]] = {
        "#28a745": {"lat": [], "lon": []},
        "#ffc107": {"lat": [], "lon": []},
        "#fd7e14": {"lat": [], "lon": []},
        "#dc3545": {"lat": [], "lon": []},
    }

    for (subst_key, mtr_key), items in grouped.items():
        items_sorted = sorted(items, key=lambda x: x.min_capacity, reverse=True)
        keys = [
            f"{subst_key}:{mtr_key}:{((x.dl_cd or x.dl_nm or '').strip() or '(unknown-dl)')}"
            for x in items_sorted
        ]
        for a, b in zip(keys, keys[1:], strict=False):
            pa = points.get(a)
            pb = points.get(b)
            if not pa or not pb:
                continue
            cap = min(pa["cap"], pb["cap"])
            color = capacity_color(cap)
            bucket = segments_by_color.get(color)
            if bucket is None:
                continue
            bucket["lat"].extend([pa["lat"], pb["lat"], None])
            bucket["lon"].extend([pa["lon"], pb["lon"], None])

    return grouped, points, segments_by_color


# 시/도 중심점 (lat, lon) — 근사치
_SIDO_CENTROIDS: dict[str, tuple[float, float]] = {
    "서울특별시": (37.5665, 126.9780),
    "부산광역시": (35.1796, 129.0756),
    "대구광역시": (35.8714, 128.6014),
    "인천광역시": (37.4563, 126.7052),
    "광주광역시": (35.1595, 126.8526),
    "대전광역시": (36.3504, 127.3845),
    "울산광역시": (35.5384, 129.3114),
    "세종특별자치시": (36.4801, 127.2890),
    "경기도": (37.4138, 127.5183),
    "강원특별자치도": (37.8228, 128.1555),
    "충청북도": (36.6358, 127.4914),
    "충청남도": (36.5184, 126.8000),
    "전북특별자치도": (35.7175, 127.1530),
    "전라남도": (34.8679, 126.9910),
    "경상북도": (36.4919, 128.8889),
    "경상남도": (35.4606, 128.2132),
    "제주특별자치도": (33.4890, 126.4983),
}


def _pick_metric(row: QueryHistoryRecord, metric: str) -> int:
    if metric == "min":
        return int(row.min_cap_min)
    if metric == "median":
        return int(row.min_cap_median)
    if metric == "max":
        return int(row.min_cap_max)
    return int(row.min_cap_median)


def _hash_unit(key: str) -> float:
    """문자열을 [0, 1) 구간의 안정적 난수로 변환."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # 앞 16 hex만 사용해도 충분
    n = int(digest[:16], 16)
    return (n % 10**12) / 10**12


def _jitter_point(
    base_lat: float,
    base_lon: float,
    key: str,
    radius_deg: float,
) -> tuple[float, float]:
    """base 좌표 주변에 key 기반으로 점을 분산 배치한다."""
    u1 = _hash_unit(key + ":a")
    u2 = _hash_unit(key + ":b")
    angle = 2.0 * math.pi * u1
    # 면적 균등 분포를 위해 sqrt
    r = radius_deg * math.sqrt(u2)
    dlat = r * math.sin(angle)
    dlon = r * math.cos(angle)
    return base_lat + dlat, base_lon + dlon


def render_korea_query_map(rows: list[QueryHistoryRecord]) -> None:
    """조회 이력을 한반도 지도에 표시한다."""

    st.subheader("🗺️ 한반도 지도(조회 이력)")
    if not rows:
        st.info("조회 이력이 없어서 지도에 표시할 데이터가 없습니다.")
        return

    metric = st.radio(
        "색상 기준",
        options=["median", "min", "max"],
        index=0,
        horizontal=True,
        help="이력별(지역별) 최소여유용량 통계 중 어떤 값을 기준으로 색상을 칠할지 선택합니다.",
    )

    # sido 기준으로 묶어서 점 하나로 표시 (근사치)
    grouped: dict[str, list[QueryHistoryRecord]] = defaultdict(list)
    unknown: list[QueryHistoryRecord] = []
    for r in rows:
        sido = (r.sido or "").strip()
        if not sido:
            unknown.append(r)
            continue
        grouped[sido].append(r)

    lats: list[float] = []
    lons: list[float] = []
    colors: list[str] = []
    sizes: list[int] = []
    hover: list[str] = []

    for sido, items in sorted(grouped.items()):
        coord = _SIDO_CENTROIDS.get(sido)
        if coord is None:
            continue
        lat, lon = coord
        metric_values = [_pick_metric(x, metric) for x in items]
        val = (
            int(min(metric_values))
            if metric == "min"
            else int(sum(metric_values) / max(1, len(metric_values)))
        )
        # 평균(또는 min) 기준으로 색상
        color = capacity_color(val)

        total_queries = len(items)
        total_results = sum(int(x.result_count) for x in items)
        size = max(10, min(28, 10 + total_queries * 2))

        lats.append(lat)
        lons.append(lon)
        colors.append(color)
        sizes.append(size)
        hover.append(
            "<br>".join(
                [
                    f"<b>{sido}</b>",
                    f"queries: {total_queries}",
                    f"results: {total_results}",
                    f"metric({metric}): {val:,} kW",
                ]
            )
        )

    if not lats:
        st.warning(
            "지도에 표시할 수 있는 시/도 좌표 매핑이 없습니다. (sido 값이 비어있거나 매핑 누락)"
        )
        return

    zoom = st.slider("줌", min_value=4.0, max_value=9.0, value=5.1, step=0.1)

    fig = go.Figure()
    fig.add_trace(
        go.Scattermapbox(
            lat=lats,
            lon=lons,
            mode="markers",
            marker=dict(size=sizes, color=colors, opacity=0.85),
            hoverinfo="text",
            text=hover,
        )
    )

    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=560,
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=36.4, lon=127.8),
            zoom=float(zoom),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

    if unknown:
        with st.expander("좌표 매핑 불가(시/도 정보 없음)"):
            st.write("아래 이력은 sido가 비어 있어 지도에 표시하지 못했습니다.")
            st.dataframe(
                [
                    {
                        "region": x.region_name,
                        "mode": x.mode,
                        "results": x.result_count,
                        "min": x.min_cap_min,
                        "median": x.min_cap_median,
                        "max": x.min_cap_max,
                    }
                    for x in unknown
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_capacity_connection_map(
    records: list[CapacityRecord],
    region: RegionInfo | None,
) -> None:
    """현재 조회된 선로 레코드를 지도 위에 근사 배치하고 연결한다.

    - 실제 지리 경로가 아니라, 지역 중심점 주변에 DL들을 배치한 스키매틱 뷰다.
    - 변전소/변압기 그룹 내에서 DL을 용량(최소 여유) 기준으로 정렬해 연결선으로 잇는다.
    """

    st.subheader("🧭 현재 선로 지도(근사) + 용량 연결")
    if not records:
        st.info("표시할 선로 데이터가 없습니다.")
        return

    if region is None:
        st.warning("지역 정보가 없어 지도 중심을 기본값으로 표시합니다.")

    base_coord = _SIDO_CENTROIDS.get(region.sido) if region else None
    base_lat, base_lon = base_coord if base_coord else (36.4, 127.8)

    st.caption(
        "주의: KEPCO 데이터에는 선로 좌표/경로가 없어, 지도 상 연결은 근사(스키매틱)입니다. "
        "아래의 '공개 전력선 레이어(OSM)'는 OpenStreetMap 기반이며, "
        "커버리지는 지역별로 다를 수 있습니다."
    )

    show_osm = st.checkbox(
        "공개 전력선 레이어(OSM/무료, 추정)",
        value=False,
        help="OSM(power=line/minor_line/cable) 데이터를 Overpass로 가져와 오버레이합니다.",
    )
    prefer_distribution = st.checkbox(
        "배전 느낌(필터 강화)",
        value=True,
        disabled=not show_osm,
        help="minor_line 우선, 낮은 전압 우선으로 정렬하고 과도한 선형은 자동 축소합니다.",
    )
    osm_radius_km = st.slider(
        "OSM 검색 반경(km)",
        min_value=2,
        max_value=40,
        value=12,
        step=2,
        disabled=not show_osm,
    )
    osm_max_lines = st.slider(
        "OSM 최대 표시 선 수",
        min_value=30,
        max_value=400,
        value=140,
        step=10,
        disabled=not show_osm,
    )
    osm_max_kv = st.slider(
        "OSM 최대 전압(kV)",
        min_value=11,
        max_value=345,
        value=66,
        step=11,
        disabled=not show_osm,
        help=(
            "배전 위주로 보려면 22~66kV 정도가 무난합니다. "
            "voltage 태그가 없는 선은 제외하지 않습니다."
        ),
    )

    zoom = st.slider("줌(현재 선로)", min_value=6.0, max_value=13.0, value=10.2, step=0.1)
    spread = st.slider(
        "점 분산(근사)",
        min_value=0.02,
        max_value=0.30,
        value=0.10,
        step=0.01,
        help="선로 좌표가 없어서 임의 분산 배치합니다. 값이 클수록 점/선이 넓게 퍼집니다.",
    )

    # 필터(선로가 많으면 지도/선이 과밀해짐)
    subst_options = sorted(
        {(r.subst_nm or r.subst_cd or "").strip() for r in records if r.subst_nm or r.subst_cd}
    )
    selected_subst = st.multiselect(
        "변전소 필터(옵션)",
        options=subst_options,
        default=subst_options[:3] if len(subst_options) > 3 else subst_options,
        help="선로 수가 많으면 일부 변전소만 선택해서 보는 것을 권장합니다.",
    )
    filtered = (
        [r for r in records if (r.subst_nm or r.subst_cd or "").strip() in set(selected_subst)]
        if selected_subst
        else records
    )

    # 선로 선택(지도 클릭 이벤트는 Streamlit에서 안정적이지 않아 selectbox로 제공)
    options: list[tuple[str, CapacityRecord]] = []
    for r in records:
        subst_key = (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)"
        mtr_key = (r.mtr_no or "").strip() or "(unknown-mtr)"
        dl_key = (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)"
        label = f"{subst_key} / {mtr_key} / {dl_key} · {r.min_capacity:,} kW"
        options.append((label, r))
    options = sorted(options, key=lambda x: x[0])

    selected_record: CapacityRecord = st.selectbox(
        "선로 선택",
        options=[x[1] for x in options],
        format_func=lambda r: next((lab for lab, rr in options if rr == r), "(unknown)"),
    )

    selected_subst_key = (
        selected_record.subst_nm or selected_record.subst_cd or ""
    ).strip() or "(unknown-subst)"
    selected_mtr_key = (selected_record.mtr_no or "").strip() or "(unknown-mtr)"

    grouped, points, segments_by_color = _build_schematic_points_and_segments(
        records=filtered,
        base_lat=float(base_lat),
        base_lon=float(base_lon),
        spread=float(spread),
    )

    fig = go.Figure()

    # (선택) OSM 전력선 레이어
    if show_osm:
        # centroid가 너무 거칠면 Nominatim으로 보정
        query_parts = []
        if region is not None:
            query_parts.append("대한민국")
            query_parts.append(region.sido)
            query_parts.append(region.sigungu)
            if region.dong and region.dong != "전체":
                query_parts.append(region.dong)
        geocode_query = " ".join([p for p in query_parts if p])
        geo = geocode_korea_region(geocode_query) if geocode_query else None
        if geo is not None:
            base_lat, base_lon = geo

        bbox = make_bbox(base_lat, base_lon, radius_km=float(osm_radius_km))
        with st.spinner("OSM 전력선 geometry 로딩 중..."):
            lines = fetch_osm_power_lines(bbox)

        if not lines:
            st.info("이 영역에서는 OSM 전력선 데이터를 찾지 못했습니다. (커버리지/레이트리밋 가능)")
        else:
            total = len(lines)

            def _power_rank(p: str) -> int:
                pr = (p or "").strip()
                if pr == "minor_line":
                    return 0
                if pr == "cable":
                    return 1
                if pr == "line":
                    return 2
                return 9

            def _line_key(ln) -> tuple[int, int, int]:
                v = parse_voltage_value(getattr(ln, "voltage", "") or "")
                v_key = int(v) if isinstance(v, int) else 1_000_000_000
                # 짧은 선형은 디테일(도심 배전)일 가능성이 있어 살짝 우선
                npts = len(getattr(ln, "lats", []) or [])
                return (_power_rank(getattr(ln, "power", "")), v_key, npts)

            filtered_lines = lines
            if prefer_distribution:
                max_v = int(osm_max_kv) * 1000
                filtered_lines = []
                for ln in lines:
                    v = parse_voltage_value(ln.voltage)
                    if isinstance(v, int) and v > max_v:
                        continue
                    filtered_lines.append(ln)
                filtered_lines = sorted(filtered_lines, key=_line_key)

            shown = filtered_lines[: int(osm_max_lines)]
            if prefer_distribution:
                st.caption(
                    f"OSM 전력선: {total}개 로드 → "
                    f"필터 후 {len(filtered_lines)}개 → 표시 {len(shown)}개"
                )
            else:
                st.caption(f"OSM 전력선: {total}개 로드 → 표시 {len(shown)}개")

            power_style = {
                "minor_line": ("rgba(30,64,175,0.35)", 2),
                "cable": ("rgba(2,132,199,0.25)", 2),
                "line": ("rgba(15,23,42,0.22)", 2),
            }

            for ln in shown:
                title = ln.name
                if ln.voltage:
                    title = f"{title} ({ln.voltage}V)"
                col, width = power_style.get(ln.power, ("rgba(15,23,42,0.22)", 2))
                fig.add_trace(
                    go.Scattermapbox(
                        lat=ln.lats,
                        lon=ln.lons,
                        mode="lines",
                        line=dict(color=col, width=width),
                        hoverinfo="text",
                        text=title,
                        name="OSM power",
                        showlegend=False,
                    )
                )

    # 선 먼저
    for color, coords in segments_by_color.items():
        if not coords["lat"]:
            continue
        fig.add_trace(
            go.Scattermapbox(
                lat=coords["lat"],
                lon=coords["lon"],
                mode="lines",
                line=dict(color=color, width=3),
                hoverinfo="skip",
                name=f"lines:{color}",
                showlegend=False,
            )
        )

    # 점(선로)
    lats = [v["lat"] for v in points.values()]
    lons = [v["lon"] for v in points.values()]
    cols = [v["color"] for v in points.values()]
    hovers = [v["hover"] for v in points.values()]

    fig.add_trace(
        go.Scattermapbox(
            lat=lats,
            lon=lons,
            mode="markers",
            marker=dict(size=10, color=cols, opacity=0.9),
            hoverinfo="text",
            text=hovers,
            name="DL",
            showlegend=False,
        )
    )

    # 선택 선로 강조
    selected_dl_key = (
        selected_record.dl_cd or selected_record.dl_nm or ""
    ).strip() or "(unknown-dl)"
    selected_point_key = f"{selected_subst_key}:{selected_mtr_key}:{selected_dl_key}"
    sp = points.get(selected_point_key)
    if sp is not None:
        fig.add_trace(
            go.Scattermapbox(
                lat=[sp["lat"]],
                lon=[sp["lon"]],
                mode="markers",
                marker=dict(size=18, color="#111827", opacity=0.85),
                hoverinfo="text",
                text=["<b>SELECTED</b><br>" + sp["hover"]],
                showlegend=False,
            )
        )

    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=640,
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=float(base_lat), lon=float(base_lon)),
            zoom=float(zoom),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

    st.divider()
    st.subheader("선택 선로 상세")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("변전소", selected_record.subst_nm or selected_record.subst_cd)
    c2.metric("변압기", selected_record.mtr_no)
    c3.metric("DL", selected_record.dl_nm or selected_record.dl_cd)
    c4.metric("최소 여유", f"{selected_record.min_capacity:,} kW")

    d1, d2, d3 = st.columns(3)
    d1.write(f"변전소 여유: {format_capacity(selected_record.substation_capacity)}")
    d2.write(f"변압기 여유: {format_capacity(selected_record.transformer_capacity)}")
    d3.write(f"DL 여유: {format_capacity(selected_record.dl_capacity)}")

    st.subheader("연결된 선로(같은 변전소/변압기)")
    connected = grouped.get((selected_subst_key, selected_mtr_key), [])
    if not connected:
        st.info("연결된 선로를 찾지 못했습니다.")
        return

    rows = []
    for r in sorted(connected, key=lambda x: x.min_capacity):
        rows.append(
            {
                "DL": (r.dl_nm or r.dl_cd),
                "최소 여유(kW)": r.min_capacity,
                "변전소 여유": r.substation_capacity,
                "변압기 여유": r.transformer_capacity,
                "DL 여유": r.dl_capacity,
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
