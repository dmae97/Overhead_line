"""한전 배전선로 여유용량 스캐너 — Streamlit 메인 앱.

한전 전력데이터 개방포털 OpenAPI를 통해 실시간 조회한다.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from src.core.config import settings
from src.core.exceptions import KepcoAPIError, ScraperError
from src.data.address import to_kepco_params
from src.data.data_loader import load_records_from_uploaded_file
from src.data.history_db import HistoryRepository
from src.data.models import CapacityRecord, QueryHistoryRecord, RegionInfo
from src.ui.charts import render_capacity_bar_chart, render_capacity_breakdown_chart
from src.ui.dashboard import render_history_panel, render_result_table
from src.ui.group_view import render_substation_group_view
from src.ui.sidebar import render_region_selector
from src.utils.cache import fetch_capacity_cached
from src.utils.export import render_download_buttons

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _now_ts() -> float:
    return time.time()


def _get_session_cache() -> dict:
    cache = st.session_state.get("_refresh_cache")
    if isinstance(cache, dict):
        return cache
    st.session_state["_refresh_cache"] = {}
    return st.session_state["_refresh_cache"]


def _render_refresh_timer() -> None:
    state = st.session_state.get("_timer_state")
    if not isinstance(state, dict):
        return

    last_ts = state.get("last_ts")
    next_ts = state.get("next_ts")
    label = str(state.get("label") or "")
    auto_reload = bool(state.get("auto_reload") or False)

    if not isinstance(last_ts, (int, float)) or not isinstance(next_ts, (int, float)):
        return

    last_dt = datetime.fromtimestamp(float(last_ts)).strftime("%Y-%m-%d %H:%M:%S")
    next_dt = datetime.fromtimestamp(float(next_ts)).strftime("%Y-%m-%d %H:%M:%S")

    html = f"""
    <div style="font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
                border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; padding: 12px 14px;
                background: rgba(0,0,0,0.02);">
      <div style="font-size: 12px; opacity: 0.75;">{label}</div>
      <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:6px;">
        <div style="font-size: 13px;"><b>마지막 갱신</b>: {last_dt}</div>
        <div style="font-size: 13px;"><b>다음 갱신 가능</b>: {next_dt}</div>
        <div style="font-size: 13px;"><b>갱신까지</b>: <span id="olc-countdown">-</span></div>
      </div>
    </div>
    <script>
      (function() {{
        const nextMs = {int(float(next_ts) * 1000)};
        const autoReload = {str(auto_reload).lower()};
        let reloaded = false;
        function fmt(sec) {{
          const s = Math.max(0, sec|0);
          const h = Math.floor(s/3600);
          const m = Math.floor((s%3600)/60);
          const r = s%60;
          if (h > 0) return `${{h}}h ${{m}}m ${{r}}s`;
          if (m > 0) return `${{m}}m ${{r}}s`;
          return `${{r}}s`;
        }}
        function tick() {{
          const now = Date.now();
          const diff = Math.floor((nextMs - now) / 1000);
          const el = document.getElementById('olc-countdown');
          if (!el) return;
          if (diff <= 0) {{
            el.textContent = '조회 가능';
            if (autoReload && !reloaded) {{
              reloaded = true;
              window.location.reload();
            }}
            return;
          }}
          el.textContent = fmt(diff);
        }}
        tick();
        setInterval(tick, 1000);
      }})();
    </script>
    """

    components.html(html, height=92)


def _make_cache_key(mode: str, region: RegionInfo, jibun: str) -> str:
    return f"{mode}:{region.display_name}:{jibun.strip()}"


def _render_query_sidebar() -> tuple[list[CapacityRecord] | None, str]:
    """사이드바에서 실시간 조회 또는 파일 업로드를 처리하고 (records, label)을 반환."""
    st.sidebar.header("⚡ 실시간 조회")
    region: RegionInfo | None = render_region_selector()

    jibun = st.sidebar.text_input(
        "지번(선택)",
        value="",
        help="예: 142-1 (미입력 시 동/면 단위로 조회)",
    ).strip()

    refresh_minutes = st.sidebar.slider(
        "갱신 간격(분)",
        min_value=5,
        max_value=60,
        value=15,
        step=5,
        help="너무 잦은 조회는 CAPTCHA/봇탐지 또는 접속 제한을 유발할 수 있습니다.",
    )

    min_interval_seconds = float(refresh_minutes) * 60.0

    auto_reload = st.sidebar.checkbox(
        "갱신 마감 시 페이지 새로고침",
        value=False,
        help=(
            "다음 갱신 가능 시점이 되면 페이지를 새로고침합니다. "
            "(외부 조회를 자동 실행하진 않습니다)"
        ),
    )

    run = st.sidebar.button("🔍 조회", use_container_width=True, type="primary")

    st.sidebar.divider()
    st.sidebar.subheader("📁 파일 업로드 (옵션)")
    uploaded_file = st.sidebar.file_uploader(
        "CSV / Excel / JSON",
        type=["csv", "xlsx", "xls", "json"],
        help="한전ON/개방포털에서 다운로드한 파일을 업로드해 분석할 수도 있습니다.",
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        records = load_records_from_uploaded_file(file_bytes, uploaded_file.name)
        if records:
            return records, "업로드 데이터"
        st.sidebar.error("파일에서 유효한 데이터를 찾을 수 없습니다.")
        return None, ""

    if not run:
        return None, ""

    if region is None:
        st.sidebar.warning("지역을 먼저 선택하세요.")
        return None, ""

    try:
        params = to_kepco_params(region)
        if jibun:
            params = params.model_copy(update={"jibun": jibun})

        mode = "api" if settings.kepco_api_key else "selenium"
        cache_key = _make_cache_key(mode, region, jibun)
        cache = _get_session_cache()
        cached_item = cache.get(cache_key)
        now = _now_ts()

        if isinstance(cached_item, dict):
            ts = cached_item.get("ts")
            recs = cached_item.get("records")
            label = cached_item.get("label")
            if isinstance(ts, (int, float)) and (now - float(ts)) < min_interval_seconds and recs:
                remaining = int(min_interval_seconds - (now - float(ts)))
                st.sidebar.info(f"최근 조회 결과를 사용합니다. 다음 갱신까지 {remaining}s")
                st.session_state["_timer_state"] = {
                    "last_ts": float(ts),
                    "next_ts": float(ts) + min_interval_seconds,
                    "label": str(label or region.display_name),
                    "auto_reload": auto_reload,
                }
                return recs, str(label or region.display_name)

        with st.spinner(f"{region.display_name} 여유용량 조회 중..."):
            if settings.kepco_api_key:
                records = fetch_capacity_cached(params)
            else:
                # Selenium 폴백: 주소 키워드 기반 (브라우저 자동화)
                keyword = f"{region.display_name} {jibun}".strip()
                try:
                    from src.data.kepco_scraper import KepcoCapacityScraper
                except Exception as exc:
                    py = sys.executable
                    cause = f"{type(exc).__name__}: {exc}"
                    raise ScraperError(
                        "Selenium 폴백 모듈을 로드하지 못했습니다.\n"
                        f"- 원인: {cause}\n"
                        f"- python={py}\n\n"
                        "해결:\n"
                        "1) 로컬: `uv sync` 후 `uv run streamlit run src/app.py`\n"
                        "2) Streamlit Cloud: Selenium 폴백이 제한될 수 있어 KEPCO_API_KEY 설정을 권장합니다."
                    ) from exc

                records = KepcoCapacityScraper().fetch_capacity_by_keyword(keyword)

        cache[cache_key] = {
            "ts": now,
            "records": records,
            "label": region.display_name,
        }
        st.session_state["_timer_state"] = {
            "last_ts": float(now),
            "next_ts": float(now) + min_interval_seconds,
            "label": region.display_name,
            "auto_reload": auto_reload,
        }
        return records, region.display_name
    except KepcoAPIError as exc:
        st.sidebar.error(f"한전 API 오류: {exc.message}")

        # 이전 성공 데이터가 있으면 유지
        cache = _get_session_cache()
        mode = "api"
        cache_key = _make_cache_key(mode, region, jibun)
        cached_item = cache.get(cache_key)
        if isinstance(cached_item, dict) and cached_item.get("records"):
            st.sidebar.warning("마지막 성공 데이터로 표시합니다.")
            ts = cached_item.get("ts")
            if isinstance(ts, (int, float)):
                st.session_state["_timer_state"] = {
                    "last_ts": float(ts),
                    "next_ts": float(ts) + min_interval_seconds,
                    "label": str(cached_item.get("label") or region.display_name),
                    "auto_reload": auto_reload,
                }
            return cached_item["records"], str(cached_item.get("label") or region.display_name)
        return None, ""
    except ScraperError as exc:
        st.sidebar.error(f"웹 조회 오류: {exc.message}")
        st.sidebar.caption(
            "API 키가 없으면 브라우저 자동화로 조회합니다. "
            "CAPTCHA/로그인 요구 등으로 실패할 수 있습니다."
        )

        # 이전 성공 데이터가 있으면 유지
        cache = _get_session_cache()
        mode = "selenium"
        cache_key = _make_cache_key(mode, region, jibun)
        cached_item = cache.get(cache_key)
        if isinstance(cached_item, dict) and cached_item.get("records"):
            st.sidebar.warning("마지막 성공 데이터로 표시합니다.")
            ts = cached_item.get("ts")
            if isinstance(ts, (int, float)):
                st.session_state["_timer_state"] = {
                    "last_ts": float(ts),
                    "next_ts": float(ts) + min_interval_seconds,
                    "label": str(cached_item.get("label") or region.display_name),
                    "auto_reload": auto_reload,
                }
            return cached_item["records"], str(cached_item.get("label") or region.display_name)
        return None, ""
    except Exception:
        logger.exception("실시간 조회 실패")
        st.sidebar.error("조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
        return None, ""


def main() -> None:
    st.set_page_config(
        page_title="⚡ 한전 선로용량 스캐너",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("⚡ 한전 배전선로 여유용량 스캐너")
    st.caption("태양광 발전사업 계통연계 가능 여부를 빠르게 확인하세요.")

    _render_refresh_timer()

    records, data_label = _render_query_sidebar()

    if records is None:
        st.info("👈 사이드바에서 지역을 선택하고 '조회'를 누르세요.")
        if settings.kepco_api_key:
            st.caption("KEPCO_API_KEY가 설정되어 있습니다. (OpenAPI 실시간 조회)")
        else:
            st.caption(
                "KEPCO_API_KEY가 없으면 Selenium 폴백을 시도합니다. (서버 환경에서는 실패할 수 있음)"
            )
        return

    if not records:
        st.warning("조회 결과가 없습니다.")
        return

    st.subheader(f"📊 분석 결과 ({len(records)}건) · {data_label}")

    # 결과를 session_state에 저장
    st.session_state["last_records"] = records

    render_result_table(records)

    st.divider()

    tab1, tab2, tab3 = st.tabs(["📊 최소 여유용량", "📈 레벨별 비교", "🏭 변전소별 그룹핑"])
    with tab1:
        render_capacity_bar_chart(records)
    with tab2:
        render_capacity_breakdown_chart(records)
    with tab3:
        render_substation_group_view(records)

    st.divider()
    render_download_buttons(records, region_name=data_label)

    st.divider()

    # 조회 이력 저장
    try:
        repo = HistoryRepository()
        repo.save(
            QueryHistoryRecord(
                region_name=data_label,
                metro_cd="",
                city_cd="",
                dong="",
                result_count=len(records),
                queried_at=datetime.now(),
            )
        )
    except Exception:
        logger.warning("조회 이력 저장 실패", exc_info=True)

    render_history_panel()


if __name__ == "__main__":
    main()
