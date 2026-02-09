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


def _map_capacity_color(capacity_kw: int) -> str:
    """지도에서 쓰는 용량 색상.

    - 시안성 개선: 노랑(#ffc107)은 지도에서 잘 안 보여서 주황(#fd7e14)으로 합친다.
    - 그 외는 기존 capacity_color를 유지한다.
    """

    base = capacity_color(capacity_kw)
    if base == "#ffc107":
        return "#fd7e14"
    return base


def _build_schematic_points_and_segments(
    records: list[CapacityRecord],
    base_lat: float,
    base_lon: float,
    spread: float,
) -> tuple[
    dict[tuple[str, str], list[CapacityRecord]],
    dict[str, _PointInfo],
    dict[tuple[str, str], _PointInfo],
    dict[str, _PointInfo],
    dict[str, list[float | None]],
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

    subst_points: dict[str, _PointInfo] = {}
    mtr_points: dict[tuple[str, str], _PointInfo] = {}
    dl_points: dict[str, _PointInfo] = {}

    seg_sub_mtr: dict[str, list[float | None]] = {"lat": [], "lon": []}
    seg_mtr_dl_by_color: dict[str, dict[str, list[float | None]]] = {
        "#28a745": {"lat": [], "lon": []},
        "#fd7e14": {"lat": [], "lon": []},
        "#dc3545": {"lat": [], "lon": []},
    }

    # 변전소(수전) 중심점
    subst_centers: dict[str, tuple[float, float]] = {}

    for subst_key in sorted({k[0] for k in grouped}):
        sub_lat, sub_lon = _jitter_point(
            base_lat,
            base_lon,
            f"sub:{base_lat:.4f}:{base_lon:.4f}:{subst_key}",
            radius_deg=float(spread),
        )
        # 변전소 용량(최소)
        subst_records = [r for (s, _), rows in grouped.items() if s == subst_key for r in rows]
        subst_cap = min((r.substation_capacity for r in subst_records), default=0)
        subst_centers[subst_key] = (float(sub_lat), float(sub_lon))
        subst_points[subst_key] = {
            "lat": float(sub_lat),
            "lon": float(sub_lon),
            "cap": int(subst_cap),
            "color": "#111827",
            "hover": "<br>".join(
                [
                    f"<b>수전(변전소)</b>: {subst_key}",
                    f"substation_capacity: {int(subst_cap):,} kW",
                ]
            ),
        }

    # 변압기/ DL 포인트 + 연결선
    for (subst_key, mtr_key), items in grouped.items():
        sub_center = subst_centers.get(subst_key, (base_lat, base_lon))
        m_lat, m_lon = _jitter_point(
            sub_center[0],
            sub_center[1],
            f"mtr:{subst_key}:{mtr_key}",
            radius_deg=float(spread) * 0.55,
        )
        mtr_cap = min((r.transformer_capacity for r in items), default=0)
        mtr_min = min((r.min_capacity for r in items), default=0)
        mtr_points[(subst_key, mtr_key)] = {
            "lat": float(m_lat),
            "lon": float(m_lon),
            "cap": int(mtr_min),
            "color": "#334155",
            "hover": "<br>".join(
                [
                    f"<b>변압기</b>: {mtr_key}",
                    f"substation: {subst_key}",
                    f"transformer_capacity: {int(mtr_cap):,} kW",
                    f"min_capacity(group): {int(mtr_min):,} kW",
                ]
            ),
        }

        # 수전 -> 변압기 연결(중립선)
        sub_lat, sub_lon = subst_centers.get(subst_key, (base_lat, base_lon))
        seg_sub_mtr["lat"].extend([float(sub_lat), float(m_lat), None])
        seg_sub_mtr["lon"].extend([float(sub_lon), float(m_lon), None])

        for r in items:
            dl_key = (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)"
            point_key = f"{subst_key}:{mtr_key}:{dl_key}"
            d_lat, d_lon = _jitter_point(
                float(m_lat),
                float(m_lon),
                f"dl:{subst_key}:{mtr_key}:{dl_key}",
                radius_deg=float(spread) * 0.28,
            )
            cap = int(r.min_capacity)
            color = _map_capacity_color(cap)
            dl_points[point_key] = {
                "lat": float(d_lat),
                "lon": float(d_lon),
                "cap": cap,
                "color": color,
                "hover": "<br>".join(
                    [
                        f"<b>{dl_key}</b>",
                        f"최소 여유: {cap:,} kW",
                    ]
                ),
            }

            bucket = seg_mtr_dl_by_color.get(color)
            if bucket is None:
                # 혹시 다른 색이 생겨도 안전하게 처리
                seg_mtr_dl_by_color[color] = {"lat": [], "lon": []}
                bucket = seg_mtr_dl_by_color[color]
            bucket["lat"].extend([float(m_lat), float(d_lat), None])
            bucket["lon"].extend([float(m_lon), float(d_lon), None])

    return grouped, subst_points, mtr_points, dl_points, seg_sub_mtr, seg_mtr_dl_by_color


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
            marker=dict(
                size=sizes,
                color=colors,
                opacity=0.9,
            ),
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
        uirevision="query-map",
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
    """현재 조회된 선로를 지도에 '근사'로 배치해 용량/연결을 보여준다.

    KEPCO 응답에는 선로 geometry가 없어서, 변전소→변압기→DL 구조를 기반으로
    지오코딩 중심점 주변에 스키매틱(임의 분산) 형태로 배치한다.
    """

    st.subheader("🗺️ 선로 지도(근사) · 용량/연결")
    if not records:
        st.info("표시할 선로 데이터가 없습니다.")
        return

    # 지도 배경(Osm) 자체 아이콘(주황 삼각형)은 '봉우리/산' 표식일 수 있다.
    st.caption(
        "배경지도에 보이는 작은 주황 삼각형은 OSM 지형 아이콘일 수 있습니다. "
        "이 화면에서 수전/변압기/DL은 검은 마커/색 원/색 선으로 표시됩니다."
    )

    base_lat, base_lon = 36.4, 127.8
    if region is not None:
        query_parts = ["대한민국", region.sido, region.sigungu]
        if region.dong and region.dong != "전체":
            query_parts.append(region.dong)
        if region.ri:
            query_parts.append(region.ri)
        geo = geocode_korea_region(" ".join([p for p in query_parts if p]))
        if geo is not None:
            base_lat, base_lon = geo
        else:
            coord = _SIDO_CENTROIDS.get(region.sido)
            if coord is not None:
                base_lat, base_lon = coord

    base_style = st.selectbox(
        "베이스맵",
        options=["carto-positron", "open-street-map"],
        index=0,
        help="carto-positron은 심볼이 적어 시인성이 좋습니다.",
    )
    zoom = st.slider("기본 줌", min_value=6.0, max_value=14.0, value=11.0, step=0.1)
    spread = st.slider(
        "점 분산(근사)",
        min_value=0.02,
        max_value=0.25,
        value=0.08,
        step=0.01,
        help="geometry가 없어서 임의 분산 배치합니다.",
    )

    # OSM 오버레이(무료) 옵션
    show_osm = st.checkbox(
        "공개 전력선 레이어(OSM/무료, 추정)",
        value=False,
        help="OSM의 power=line/minor_line/cable 선형을 지도에 오버레이합니다.",
    )
    prefer_distribution = True
    osm_radius_km = 12
    osm_max_lines = 140
    osm_max_kv = 66
    if show_osm:
        with st.expander("OSM 옵션", expanded=True):
            prefer_distribution = st.checkbox(
                "배전 느낌(필터 강화)",
                value=True,
                help="minor_line 우선 + 낮은 전압 우선 + 자동 다운샘플.",
            )
            osm_radius_km = st.slider("검색 반경(km)", 2, 40, 12, 2)
            osm_max_lines = st.slider("최대 표시 선 수", 30, 400, 140, 10)
            osm_max_kv = st.slider(
                "최대 전압(kV)",
                11,
                345,
                66,
                11,
                help="배전 위주면 22~66kV 권장. voltage 없는 선은 남겨둡니다.",
            )

    # 데이터 필터
    subst_options = sorted(
        {(r.subst_nm or r.subst_cd or "").strip() for r in records if r.subst_nm or r.subst_cd}
    )
    default_subst = subst_options[:1] if len(subst_options) > 1 else subst_options
    selected_subst = st.multiselect(
        "변전소 필터(가독성용)",
        options=subst_options,
        default=default_subst,
        help="선로가 많으면 일부 변전소만 보는 걸 권장합니다.",
    )

    filtered_records = (
        [r for r in records if (r.subst_nm or r.subst_cd or "").strip() in set(selected_subst)]
        if selected_subst
        else records
    )
    if not filtered_records:
        st.info("선택한 변전소 범위에 데이터가 없습니다.")
        return

    # 선로 선택(지도 클릭/호버와 동일한 key 사용)
    option_rows: list[tuple[str, str, CapacityRecord]] = []
    for r in filtered_records:
        s = (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)"
        m = (r.mtr_no or "").strip() or "(unknown-mtr)"
        d = (r.dl_cd or r.dl_nm or "").strip() or "(unknown-dl)"
        point_key = f"{s}:{m}:{d}"
        label = f"{s} / {m} / {d} · {r.min_capacity:,} kW"
        option_rows.append((label, point_key, r))

    option_rows = sorted(option_rows, key=lambda x: x[0])
    key_to_label = {k: lab for lab, k, _ in option_rows}
    key_to_record = {k: rec for _, k, rec in option_rows}
    select_keys = [k for _, k, _ in option_rows]

    selected_key = st.selectbox(
        "선로 선택(지도에서 점 클릭 가능)",
        options=select_keys,
        format_func=lambda k: key_to_label.get(str(k), str(k)),
        key="map_selected_dl_key",
    )

    selected_record = key_to_record.get(str(selected_key))
    if selected_record is None:
        selected_record = option_rows[0][2]

    selected_subst_key = (
        selected_record.subst_nm or selected_record.subst_cd or ""
    ).strip() or "(unknown-subst)"
    selected_mtr_key = (selected_record.mtr_no or "").strip() or "(unknown-mtr)"
    selected_dl_key = (
        selected_record.dl_cd or selected_record.dl_nm or ""
    ).strip() or "(unknown-dl)"

    edge_scope = st.radio(
        "연결선 범위",
        options=["선택한 변압기", "선택한 변전소", "전체"],
        index=0,
        horizontal=True,
    )
    show_all_points = st.checkbox("포인트 전체 표시", value=False)

    # 그룹핑(하단 표용)
    grouped_all: dict[tuple[str, str], list[CapacityRecord]] = defaultdict(list)
    for r in filtered_records:
        s = (r.subst_nm or r.subst_cd or "").strip() or "(unknown-subst)"
        m = (r.mtr_no or "").strip() or "(unknown-mtr)"
        grouped_all[(s, m)].append(r)

    if edge_scope == "선택한 변압기":
        scope_records = grouped_all.get((selected_subst_key, selected_mtr_key), [])
    elif edge_scope == "선택한 변전소":
        scope_records = [
            r for (s, _), rows in grouped_all.items() if s == selected_subst_key for r in rows
        ]
    else:
        scope_records = filtered_records

    point_records = filtered_records if show_all_points else scope_records
    if not scope_records:
        st.info("연결선을 표시할 데이터가 없습니다.")
        return

    # 포인트는 point_records, 선은 scope_records 기준으로 계산
    _, sub_pts, mtr_pts, dl_pts, _, _ = _build_schematic_points_and_segments(
        records=point_records,
        base_lat=float(base_lat),
        base_lon=float(base_lon),
        spread=float(spread),
    )
    grouped, sub_e, mtr_e, dl_e, seg_sub_mtr, seg_mtr_dl = _build_schematic_points_and_segments(
        records=scope_records,
        base_lat=float(base_lat),
        base_lon=float(base_lon),
        spread=float(spread),
    )

    fig = go.Figure()

    # OSM 전력선 레이어(선택)
    if show_osm:
        bbox = make_bbox(float(base_lat), float(base_lon), radius_km=float(osm_radius_km))
        with st.spinner("OSM 전력선 geometry 로딩 중..."):
            lines = fetch_osm_power_lines(bbox)

        if lines:
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
            st.caption(f"OSM 전력선: {total}개 로드 → 표시 {len(shown)}개")

            power_style = {
                "minor_line": ("rgba(30,64,175,0.45)", 3),
                "cable": ("rgba(2,132,199,0.35)", 3),
                "line": ("rgba(15,23,42,0.30)", 3),
            }
            for ln in shown:
                lats = ln.lats
                lons = ln.lons
                if len(lats) > 220:
                    step = max(1, int(len(lats) / 220))
                    lats = lats[::step]
                    lons = lons[::step]

                col, width = power_style.get(ln.power, ("rgba(15,23,42,0.28)", 3))
                title = ln.name
                if ln.voltage:
                    title = f"{title} ({ln.voltage}V)"
                fig.add_trace(
                    go.Scattermapbox(
                        lat=lats,
                        lon=lons,
                        mode="lines",
                        line=dict(color=col, width=width),
                        hoverinfo="text",
                        text=title,
                        showlegend=False,
                    )
                )

    # 구조선(수전->변압기)
    if seg_sub_mtr["lat"]:
        fig.add_trace(
            go.Scattermapbox(
                lat=seg_sub_mtr["lat"],
                lon=seg_sub_mtr["lon"],
                mode="lines",
                line=dict(color="rgba(15,23,42,0.25)", width=3),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    # 용량선(변압기->DL)
    for color, coords in seg_mtr_dl.items():
        if not coords["lat"]:
            continue
        width = 8
        if color == "#dc3545":
            width = 10
        elif color == "#fd7e14":
            width = 9
        fig.add_trace(
            go.Scattermapbox(
                lat=coords["lat"],
                lon=coords["lon"],
                mode="lines",
                line=dict(color=color, width=width),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    # DL 점
    dl_items = list(dl_pts.items())
    dl_keys = [k for k, _ in dl_items]
    dl_lats = [v["lat"] for _, v in dl_items]
    dl_lons = [v["lon"] for _, v in dl_items]
    dl_cols = [v["color"] for _, v in dl_items]
    dl_hover = [v["hover"] for _, v in dl_items]
    fig.add_trace(
        go.Scattermapbox(
            lat=dl_lats,
            lon=dl_lons,
            mode="markers",
            marker=dict(
                size=15,
                color=dl_cols,
                opacity=0.95,
            ),
            hovertemplate="%{text}<extra></extra>",
            text=dl_hover,
            customdata=dl_keys,
            hoverlabel=dict(bgcolor="rgba(255,255,255,0.95)", font=dict(size=13, color="#0f172a")),
            showlegend=False,
        )
    )

    # 수전/변압기 마커(선 범위 기준)
    if sub_e:
        s_lats = [v["lat"] for v in sub_e.values()]
        s_lons = [v["lon"] for v in sub_e.values()]
        s_hover = [v["hover"] for v in sub_e.values()]
        fig.add_trace(
            go.Scattermapbox(
                lat=s_lats,
                lon=s_lons,
                mode="markers",
                marker=dict(
                    size=21,
                    color="#111827",
                    symbol="triangle",
                    opacity=0.95,
                ),
                hovertemplate="%{text}<extra></extra>",
                text=s_hover,
                hoverlabel=dict(
                    bgcolor="rgba(255,255,255,0.95)", font=dict(size=13, color="#0f172a")
                ),
                showlegend=False,
            )
        )

    if mtr_e:
        m_lats = [v["lat"] for v in mtr_e.values()]
        m_lons = [v["lon"] for v in mtr_e.values()]
        m_hover = [v["hover"] for v in mtr_e.values()]
        fig.add_trace(
            go.Scattermapbox(
                lat=m_lats,
                lon=m_lons,
                mode="markers",
                marker=dict(
                    size=17,
                    color="#0f172a",
                    symbol="square",
                    opacity=0.9,
                ),
                hovertemplate="%{text}<extra></extra>",
                text=m_hover,
                hoverlabel=dict(
                    bgcolor="rgba(255,255,255,0.95)", font=dict(size=13, color="#0f172a")
                ),
                showlegend=False,
            )
        )

    # 선택 선로 강조(링)
    sel_key = f"{selected_subst_key}:{selected_mtr_key}:{selected_dl_key}"
    sp = dl_pts.get(sel_key)
    if sp is not None:
        fig.add_trace(
            go.Scattermapbox(
                lat=[sp["lat"]],
                lon=[sp["lon"]],
                mode="markers",
                marker=dict(
                    size=26,
                    color="rgba(255,255,255,0.85)",
                    opacity=0.95,
                ),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    uirev = f"capacity-map:{region.display_name if region else 'default'}"
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=680,
        mapbox=dict(
            style=base_style,
            center=dict(lat=float(base_lat), lon=float(base_lon)),
            zoom=float(zoom),
        ),
        uirevision=uirev,
    )

    col_map, col_info = st.columns([0.73, 0.27], gap="large")

    with col_map:
        plot_state = st.plotly_chart(
            fig,
            use_container_width=True,
            config={"scrollZoom": True, "displayModeBar": False},
            key="capacity_map_chart",
            on_select="rerun",
            selection_mode="points",
        )

    # 클릭(포인트 선택) 이벤트 처리: DL 점의 customdata(point_key)를 읽어 selectbox 값을 갱신
    clicked_key: str | None = None
    if hasattr(plot_state, "get"):
        sel = plot_state.get("selection")
        if isinstance(sel, dict):
            pts = sel.get("points")
            if isinstance(pts, list) and pts:
                for p in reversed(pts):
                    if not isinstance(p, dict):
                        continue
                    cd = p.get("customdata")
                    if isinstance(cd, str):
                        clicked_key = cd
                        break
                    if isinstance(cd, (list, tuple)) and cd and isinstance(cd[0], str):
                        clicked_key = cd[0]
                        break

    if clicked_key and clicked_key in key_to_record and clicked_key != str(selected_key):
        st.session_state["map_selected_dl_key"] = clicked_key
        st.rerun()

    with col_info:
        st.subheader("선택 선로 상세")

        st.markdown(
            """
**표시 규칙**
- 수전(변전소): 검은 △
- 변압기: 검은 ■
- DL: 색 ● (용량)
""".strip()
        )

        st.divider()
        st.metric("변전소", selected_record.subst_nm or selected_record.subst_cd)
        st.metric("변압기", selected_record.mtr_no)
        st.metric("DL", selected_record.dl_nm or selected_record.dl_cd)
        st.metric("최소 여유", f"{selected_record.min_capacity:,} kW")

        st.write(f"변전소 여유: {format_capacity(selected_record.substation_capacity)}")
        st.write(f"변압기 여유: {format_capacity(selected_record.transformer_capacity)}")
        st.write(f"DL 여유: {format_capacity(selected_record.dl_capacity)}")

        connected = grouped_all.get((selected_subst_key, selected_mtr_key), [])
        if not connected:
            st.info("연결된 선로를 찾지 못했습니다.")
            return

        with st.expander("연결된 선로(같은 변전소/변압기)", expanded=True):
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
            st.dataframe(df, use_container_width=True, hide_index=True, height=320)
