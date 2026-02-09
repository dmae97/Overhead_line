"""한전 배전선로 여유용량 스캐너 — Streamlit 메인 앱.

한전 전력데이터 개방포털 OpenAPI를 통해 실시간 조회한다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from src.core.config import settings
from src.core.exceptions import KepcoAPIError, KepcoNoDataError, ScraperError
from src.data.address import to_kepco_params
from src.data.data_loader import load_records_from_uploaded_file
from src.data.history_db import HistoryRepository
from src.data.models import CapacityRecord, QueryHistoryRecord, RegionInfo
from src.ui.charts import render_capacity_bar_chart, render_capacity_breakdown_chart
from src.ui.dashboard import render_history_panel, render_result_table
from src.ui.group_view import render_substation_group_view
from src.ui.map_view import render_capacity_connection_map, render_korea_query_map
from src.ui.network_view import render_hierarchy_sankey
from src.ui.provenance_view import render_provenance
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


def _build_history_record(
    records: list[CapacityRecord],
    data_label: str,
    meta: object,
) -> QueryHistoryRecord:
    """현재 조회 결과로 QueryHistoryRecord를 구성한다."""
    meta_dict: dict[str, Any] = meta if isinstance(meta, dict) else {}
    raw_region = meta_dict.get("region")
    region_dict: dict[str, Any] = raw_region if isinstance(raw_region, dict) else {}
    raw_params = meta_dict.get("params")
    params_dict: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}

    min_caps = [r.min_capacity for r in records]
    min_caps_sorted = sorted(min_caps)
    min_cap_min = int(min_caps_sorted[0]) if min_caps_sorted else 0
    min_cap_max = int(min_caps_sorted[-1]) if min_caps_sorted else 0
    mid = len(min_caps_sorted) // 2
    min_cap_median = int(min_caps_sorted[mid]) if min_caps_sorted else 0
    connectable_count = sum(1 for r in records if r.is_connectable)
    not_connectable_count = len(records) - connectable_count

    return QueryHistoryRecord(
        region_name=data_label,
        metro_cd=str(params_dict.get("metroCd") or ""),
        city_cd=str(params_dict.get("cityCd") or ""),
        dong=str(params_dict.get("addrLidong") or ""),
        sido=str(region_dict.get("sido") or ""),
        sigungu=str(region_dict.get("sigungu") or ""),
        mode=str(meta_dict.get("mode") or ""),
        jibun=str(meta_dict.get("jibun") or ""),
        result_count=len(records),
        connectable_count=int(connectable_count),
        not_connectable_count=int(not_connectable_count),
        min_cap_min=int(min_cap_min),
        min_cap_median=int(min_cap_median),
        min_cap_max=int(min_cap_max),
        queried_at=datetime.now(),
    )


def _save_history_once(record: QueryHistoryRecord) -> None:
    """Streamlit rerun 중복 저장을 막고, 가능하면 DB에 저장한다."""
    ts_key = record.queried_at.strftime("%Y%m%d%H%M%S")
    save_key = f"{record.region_name}:{record.result_count}:{record.mode}:{ts_key}"

    # 같은 rerun에서 중복 저장 방지
    if st.session_state.get("_last_saved_history_key") == save_key:
        return
    st.session_state["_last_saved_history_key"] = save_key

    # 세션 폴백 저장소(지도 표시용)
    session_rows = st.session_state.get("_session_history_rows")
    if not isinstance(session_rows, list):
        session_rows = []
        st.session_state["_session_history_rows"] = session_rows
    session_rows.append(record.model_dump())
    st.session_state["_current_history_record"] = record.model_dump()

    # DB 저장은 실패해도 앱 동작은 유지
    try:
        repo = HistoryRepository()
        repo.save(record)
    except Exception:
        logger.warning("조회 이력 저장 실패", exc_info=True)


def _fetch_online_with_cache(
    region: RegionInfo,
    jibun: str,
    min_interval_seconds: float,
) -> tuple[list[CapacityRecord] | None, str]:
    """한전ON 브라우저 조회 결과를 세션 캐시에 저장 후 반환."""
    try:
        from src.data.scraper_service import fetch_capacity_by_online

        mode = "online"
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
                    "auto_reload": False,
                }
                st.session_state["_last_query_meta"] = {
                    "mode": "online",
                    "region": region.model_dump(),
                    "jibun": jibun,
                    "cached": True,
                }
                return recs, str(label or region.display_name)

        with st.spinner(f"🌐 한전ON에서 {region.display_name} 여유용량 조회 중..."):
            records = fetch_capacity_by_online(
                sido=region.sido,
                sigungu=region.sigungu,
                dong=region.dong if region.dong != "전체" else "",
                ri=region.ri,
                jibun=jibun,
            )

        cache[cache_key] = {
            "ts": now,
            "records": records,
            "label": region.display_name,
        }
        st.session_state["_timer_state"] = {
            "last_ts": float(now),
            "next_ts": float(now) + min_interval_seconds,
            "label": f"{region.display_name} (한전ON)",
            "auto_reload": False,
        }
        st.session_state["_last_query_meta"] = {
            "mode": "online",
            "region": region.model_dump(),
            "jibun": jibun,
            "cached": False,
        }
        return records, f"{region.display_name} (한전ON)"

    except ScraperError as exc:
        st.sidebar.error(f"한전ON 스크래핑 실패: {exc.message}")
        st.sidebar.markdown(
            "**대안: 무료 API 키 발급**\n"
            "1. [한전 전력데이터 개방포털](https://bigdata.kepco.co.kr) 접속\n"
            "2. 회원가입 → 마이페이지 → API 인증키 발급\n"
            "3. `.env` 또는 Streamlit Secrets에 `KEPCO_API_KEY=키` 설정"
        )

        # 샘플 데이터로 대시보드 미리보기 제공
        from src.data.data_loader import load_sample_records

        sample = load_sample_records()
        if sample:
            st.sidebar.success(f"📦 샘플 데이터 {len(sample)}건을 표시합니다.")
            st.session_state["_last_query_meta"] = {
                "mode": "sample",
                "cached": False,
                "reason": "scraper_error_fallback",
            }
            return sample, "샘플 데이터 (데모)"
        return None, ""
    except Exception as exc:
        logger.exception("한전ON 스크래핑 실패")
        st.sidebar.error(f"조회 실패: {exc}")
        return None, ""


def _render_query_sidebar() -> tuple[list[CapacityRecord] | None, str]:
    """사이드바에서 실시간 조회 또는 파일 업로드를 처리하고 (records, label)을 반환."""
    st.sidebar.header("⚡ 실시간 조회")

    last_records = st.session_state.get("last_records")
    last_label = st.session_state.get("last_data_label")

    try:
        region: RegionInfo | None = render_region_selector()
    except Exception as exc:
        logger.warning("지역 선택 UI 오류: %s", exc, exc_info=True)
        if isinstance(last_records, list):
            st.sidebar.warning("지역 선택 UI 오류로 이전 조회 결과를 표시합니다.")
            return last_records, str(last_label or "이전 조회 결과")
        raise

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
        help=(
            "너무 잦은 조회는 CAPTCHA/봇탐지 또는 접속 제한을 유발할 수 있습니다. "
            "브라우저(한전ON) 조회 모드에서는 10~15분 이상을 권장합니다."
        ),
    )

    # OpenAPI는 상대적으로 안정적이지만, 한전ON 브라우저 조회는 자동화 탐지에 더 민감하다.
    recommended_browser_minutes = 15
    effective_browser_minutes = max(int(refresh_minutes), recommended_browser_minutes)
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
        file_id = f"{uploaded_file.name}:{len(file_bytes)}"
        cached_id = st.session_state.get("_uploaded_file_id")
        cached_records = st.session_state.get("_uploaded_records")
        if cached_id == file_id and isinstance(cached_records, list):
            st.session_state["last_records"] = cached_records
            st.session_state["last_data_label"] = "업로드 데이터"
            return cached_records, "업로드 데이터"

        records = load_records_from_uploaded_file(file_bytes, uploaded_file.name)
        if records:
            action_id = _now_ts()
            st.session_state["_last_results_action_id"] = float(action_id)
            st.session_state["_uploaded_file_id"] = file_id
            st.session_state["_uploaded_records"] = records
            st.session_state["_last_query_meta"] = {
                "mode": "upload",
                "filename": uploaded_file.name,
                "cached": False,
                "action_id": float(action_id),
            }
            st.session_state["last_records"] = records
            st.session_state["last_data_label"] = "업로드 데이터"
            return records, "업로드 데이터"
        st.sidebar.error("파일에서 유효한 데이터를 찾을 수 없습니다.")
        return None, ""

    if not run:
        if isinstance(last_records, list):
            st.sidebar.caption("이전 조회 결과를 표시합니다. 새 조회는 '조회' 버튼을 누르세요.")
            return last_records, str(last_label or "이전 조회 결과")
        return None, ""

    if region is None:
        st.sidebar.warning("지역을 먼저 선택하세요.")
        if isinstance(last_records, list):
            return last_records, str(last_label or "이전 조회 결과")
        return None, ""

    # API 키가 없으면 한전ON(EWM092D00) 브라우저 스크래퍼로 폴백
    if not settings.kepco_api_key:
        st.sidebar.warning(
            "⚠️ 한전ON 브라우저 조회는 CAPTCHA/봇탐지에 민감합니다.\n\n"
            f"- 같은 지역 반복 조회는 **{recommended_browser_minutes}분 이상 간격** 권장\n"
            "- 여러 탭/여러 PC에서 동시에 반복 조회하지 마세요\n"
            "- 가능하면 OpenAPI(무료 API 키) 모드 사용을 권장합니다"
        )

        if region.dong == "전체":
            st.sidebar.warning(
                "⚠️ KEPCO_API_KEY 미설정 상태에서는 읍/면/동 '전체' 조회를 지원하지 않습니다. "
                "읍/면/동을 선택하거나 API 키를 설정해주세요."
            )
            if isinstance(last_records, list):
                return last_records, str(last_label or "이전 조회 결과")
            return None, ""
        st.sidebar.warning("⚠️ KEPCO_API_KEY 미설정 → 한전ON 브라우저 조회 모드")

        if effective_browser_minutes != int(refresh_minutes):
            msg = (
                "봇탐지 예방을 위해 브라우저 모드 최소 간격을 "
                f"{effective_browser_minutes}분으로 적용합니다."
            )
            st.sidebar.info(msg)
        browser_min_interval_seconds = float(effective_browser_minutes) * 60.0
        recs, label = _fetch_online_with_cache(region, jibun, browser_min_interval_seconds)
        if recs is not None:
            action_id = _now_ts()
            st.session_state["_last_results_action_id"] = float(action_id)
            meta = st.session_state.get("_last_query_meta")
            if isinstance(meta, dict):
                st.session_state["_last_query_meta"] = {**meta, "action_id": float(action_id)}
            st.session_state["last_records"] = recs
            st.session_state["last_data_label"] = str(label or region.display_name)
        return recs, label

    if region.dong == "전체":
        st.sidebar.info(
            "ℹ️ 읍/면/동 '전체'는 동/리 미지정(OpenAPI 시군구 단위)으로 조회합니다. "
            "지역이 넓을수록 결과가 많아 시간이 걸릴 수 있습니다."
        )
        if jibun:
            st.sidebar.warning(
                "읍/면/동 '전체' 조회에서는 지번을 사용할 수 없습니다. 지번은 무시하고 조회합니다."
            )
            jibun = ""

    try:
        params = to_kepco_params(region)
        if jibun:
            params = params.model_copy(update={"jibun": jibun})

        mode = "api"
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
                # provenance 탭에서 표시할 메타
                st.session_state["_last_query_meta"] = {
                    "mode": "api",
                    "region": region.model_dump(),
                    "jibun": jibun,
                    "params": {
                        "metroCd": params.metro_cd,
                        "cityCd": params.city_cd,
                        "addrLidong": params.dong,
                        "addrLi": params.ri,
                        "addrJibun": params.jibun,
                        "returnType": "json",
                    },
                    "cached": True,
                }
                action_id = _now_ts()
                st.session_state["_last_results_action_id"] = float(action_id)
                meta = st.session_state.get("_last_query_meta")
                if isinstance(meta, dict):
                    st.session_state["_last_query_meta"] = {**meta, "action_id": float(action_id)}
                st.session_state["last_records"] = recs
                st.session_state["last_data_label"] = str(label or region.display_name)
                return recs, str(label or region.display_name)

        with st.spinner(f"{region.display_name} 여유용량 조회 중..."):
            records = fetch_capacity_cached(params)

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
        action_id = _now_ts()
        st.session_state["_last_results_action_id"] = float(action_id)
        st.session_state["_last_query_meta"] = {
            "mode": "api",
            "region": region.model_dump(),
            "jibun": jibun,
            "params": {
                "metroCd": params.metro_cd,
                "cityCd": params.city_cd,
                "addrLidong": params.dong,
                "addrLi": params.ri,
                "addrJibun": params.jibun,
                "returnType": "json",
            },
            "cached": False,
            "action_id": float(action_id),
        }
        st.session_state["last_records"] = records
        st.session_state["last_data_label"] = region.display_name
        return records, region.display_name
    except KepcoNoDataError:
        st.sidebar.warning("조회 결과가 없습니다. 읍/면/동 또는 지번을 변경해 다시 시도해보세요.")
        action_id = _now_ts()
        st.session_state["_last_results_action_id"] = float(action_id)
        st.session_state["last_records"] = []
        st.session_state["last_data_label"] = region.display_name
        return [], region.display_name
    except KepcoAPIError as exc:
        st.sidebar.error(f"한전 API 오류: {exc.message}")

        # 이전 성공 데이터가 있으면 유지
        cache = _get_session_cache()
        cache_key = _make_cache_key("api", region, jibun)
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
            recs = cached_item["records"]
            label = str(cached_item.get("label") or region.display_name)
            action_id = _now_ts()
            st.session_state["_last_results_action_id"] = float(action_id)
            st.session_state["last_records"] = recs
            st.session_state["last_data_label"] = label
            return recs, label
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

    # 어떤 이유로든 sidebar가 records=None을 반환해도, 마지막 결과가 있으면 유지한다.
    if records is None:
        last_records = st.session_state.get("last_records")
        last_label = st.session_state.get("last_data_label")
        if isinstance(last_records, list):
            records = last_records
            data_label = str(last_label or "이전 조회 결과")

    if records is None:
        st.info("👈 사이드바에서 지역을 선택하고 '조회'를 누르세요.")
        if settings.kepco_api_key:
            st.caption("✅ KEPCO_API_KEY가 설정되어 있습니다. (OpenAPI 실시간 조회)")
        else:
            st.warning(
                "⚠️ **KEPCO_API_KEY가 설정되지 않았습니다.**\n\n"
                "실시간 조회를 위해 [한전 전력데이터 개방포털](https://bigdata.kepco.co.kr)에서 "
                "무료 API 키를 발급받아 설정해주세요.\n\n"
                "API 키 설정 방법: Streamlit Cloud → Settings → Secrets에 "
                '`KEPCO_API_KEY = "발급받은키"` 추가'
            )
        return

    # 버튼/위젯 조작으로 rerun 되어도 마지막 결과가 유지되도록 저장
    st.session_state["last_records"] = records
    st.session_state["last_data_label"] = data_label

    if not records:
        st.warning(f"'{data_label or '선택한 지역'}' 조회 결과가 없습니다.")
        return

    st.subheader(f"📊 분석 결과 ({len(records)}건) · {data_label}")

    # 조회 이력은 '새 조회/업로드' 액션에서 1번만 저장
    action_id = st.session_state.get("_last_results_action_id")
    last_saved_action_id = st.session_state.get("_last_saved_action_id")
    if isinstance(action_id, (int, float)) and action_id != last_saved_action_id:
        try:
            meta = st.session_state.get("_last_query_meta")
            history_record = _build_history_record(records, data_label=data_label, meta=meta)
            _save_history_once(history_record)
            st.session_state["_last_saved_action_id"] = float(action_id)
        except Exception:
            logger.warning("조회 이력 구성/저장 실패", exc_info=True)

    render_result_table(records)

    st.divider()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "📊 최소 여유용량",
            "📈 레벨별 비교",
            "🏭 변전소별 그룹핑",
            "🔗 선로 연결도",
            "🗺️ 지도",
            "🧾 실데이터",
        ]
    )

    def _safe_render(fn, *args, **kwargs) -> None:
        """탭 렌더링 중 예외가 발생해도 앱 전체를 죽이지 않는다."""
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            logger.warning("탭 렌더링 오류: %s", exc, exc_info=True)
            st.error(f"이 탭 표시 중 오류가 발생했습니다: {exc}")

    with tab1:
        _safe_render(render_capacity_bar_chart, records)
    with tab2:
        _safe_render(render_capacity_breakdown_chart, records)
    with tab3:
        _safe_render(render_substation_group_view, records)
    with tab4:
        _safe_render(render_hierarchy_sankey, records)
    with tab5:
        try:
            sub1, sub2 = st.tabs(["📌 조회 이력", "🧭 현재 선로(근사 연결)"])

            with sub1:
                rows: list[QueryHistoryRecord] = []
                db_error: str | None = None
                try:
                    repo = HistoryRepository()
                    rows = repo.list_recent(limit=200)
                except Exception as exc:
                    db_error = str(exc)

                if not rows:
                    session_rows = st.session_state.get("_session_history_rows")
                    if isinstance(session_rows, list) and session_rows:
                        try:
                            rows = [
                                QueryHistoryRecord.model_validate(x) for x in session_rows[-200:]
                            ]
                        except Exception:
                            rows = []

                if not rows:
                    current = st.session_state.get("_current_history_record")
                    if isinstance(current, dict):
                        try:
                            rows = [QueryHistoryRecord.model_validate(current)]
                        except Exception:
                            rows = []

                if db_error and not rows:
                    st.warning(f"조회 이력 DB 접근 실패: {db_error}")

                render_korea_query_map(rows)

            with sub2:
                region_obj: RegionInfo | None = None
                meta = st.session_state.get("_last_query_meta")
                if isinstance(meta, dict):
                    raw_region = meta.get("region")
                    if isinstance(raw_region, dict):
                        try:
                            region_obj = RegionInfo.model_validate(raw_region)
                        except Exception:
                            region_obj = None

                render_capacity_connection_map(records, region_obj)
        except Exception as exc:
            logger.warning("지도 탭 렌더링 오류: %s", exc, exc_info=True)
            st.error(f"지도 탭 표시 중 오류: {exc}")
    with tab6:
        try:
            meta = st.session_state.get("_last_query_meta")
            render_provenance(records, meta)
        except Exception as exc:
            logger.warning("실데이터 탭 렌더링 오류: %s", exc, exc_info=True)
            st.error(f"실데이터 탭 표시 중 오류: {exc}")

    st.divider()
    render_download_buttons(records, region_name=data_label)

    st.divider()

    # (이력 저장은 탭 렌더링 이전에 수행)

    render_history_panel()


if __name__ == "__main__":
    main()
