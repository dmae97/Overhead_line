"""지도 시각화 UI.

현재 OpenAPI/스크래핑 결과에는 선로의 실제 지리 좌표/경로가 포함되지 않는다.
따라서 이 모듈은 '조회 이력(지역 단위)'을 한반도 지도 위에 점으로 표시하는
근사 시각화를 제공한다.

정확한 시군구/읍면동 단위 좌표가 필요하면 별도의 centroid 데이터셋을
추가로 주입해야 한다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import plotly.graph_objects as go
import streamlit as st

from src.ui.components import capacity_color

if TYPE_CHECKING:
    from src.data.models import QueryHistoryRecord


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

    fig = go.Figure()
    fig.add_trace(
        go.Scattergeo(
            lat=lats,
            lon=lons,
            mode="markers",
            marker=dict(size=sizes, color=colors, opacity=0.85, line=dict(width=1, color="#222")),
            hoverinfo="text",
            text=hover,
        )
    )

    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=520,
        geo=dict(
            projection_type="mercator",
            center=dict(lat=36.4, lon=127.8),
            lataxis_range=[33.0, 39.6],
            lonaxis_range=[124.3, 131.9],
            showland=True,
            landcolor="rgb(245, 245, 245)",
            showcountries=True,
            countrycolor="rgba(0,0,0,0.15)",
            showocean=True,
            oceancolor="rgb(235, 244, 255)",
            coastlinecolor="rgba(0,0,0,0.2)",
            coastlinewidth=1,
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

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
