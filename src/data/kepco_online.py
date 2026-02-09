"""한전ON (online.kepco.co.kr) Playwright 기반 여유용량 스크래퍼 — 고도화 버전.

타겟 페이지: https://online.kepco.co.kr/EWM092D00 (주소로 검색)

3계층 폴백 전략:
  L1) Playwright → 내부 JS API 직접 호출 (page.evaluate + fetch)
      브라우저 세션/쿠키를 자동 활용하므로 가장 빠르고 안정적
  L2) Playwright → DOM 풀 자동화 (select_option + 검색 버튼 클릭)
      L1 실패 시 폴백. 대기/재시도 로직 대폭 강화

각 계층 내 개선사항:
  - WebSquare 준비 대기 ($w 전역 객체 확인)
  - select 옵션 로드 대기 (time.sleep → wait_for_function)
  - 검색 결과 다중 필드 검증 (dl_nm OR vol1 OR subst_nm)
  - 검색 실패 시 최대 3회 재클릭
  - Dialog/Alert 자동 해제
  - 실패 시 스크린샷 + HTML 덤프 → 로그
  - 자동화 감지 우회 강화
  - Launch args 최적화 (Streamlit Cloud 호환)
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.exceptions import ScraperError
from src.data.models import CapacityRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수: WebSquare selectbox 요소 ID
# ---------------------------------------------------------------------------
_SELECT_IDS = {
    "sido": "mf_wfm_layout_sbx_sido_input_0",
    "si": "mf_wfm_layout_sbx_si_input_0",
    "gu": "mf_wfm_layout_sbx_gu_input_0",
    "lidong": "mf_wfm_layout_sbx_lidong_input_0",
    "li": "mf_wfm_layout_sbx_li_input_0",
    "bunji": "mf_wfm_layout_sbx_bunji_input_0",
}

# 결과 DOM 요소 ID (wframe01 내부)
_RESULT_IDS = {
    "subst_nm": "mf_wfm_layout_wframe01_txt_subst_nm_label",
    "mtr_no": "mf_wfm_layout_wframe01_txt_mtr_no_label",
    "dl_nm": "mf_wfm_layout_wframe01_txt_dl_nm_label",
    # 변전소
    "subst_capa": "mf_wfm_layout_wframe01_txt_subst_capa_dsc",
    "subst_pwr": "mf_wfm_layout_wframe01_txt_subst_pwr_dsc",
    "g_subst_capa": "mf_wfm_layout_wframe01_txt_g_subst_capa_dsc",
    "vol1_1": "mf_wfm_layout_wframe01_txt_subst_vol1_dsc_1",
    "vol1_2": "mf_wfm_layout_wframe01_txt_subst_vol1_dsc_2",
    # 변압기
    "mtr_capa": "mf_wfm_layout_wframe01_txt_mtr_capa_dsc",
    "mtr_pwr": "mf_wfm_layout_wframe01_txt_mtr_pwr_dsc",
    "g_mtr_capa": "mf_wfm_layout_wframe01_txt_g_mtr_capa_dsc",
    "vol2_1": "mf_wfm_layout_wframe01_txt_mtr_vol2_dsc_1",
    "vol2_2": "mf_wfm_layout_wframe01_txt_mtr_vol2_dsc_2",
    # 배전선로 (DL)
    "dl_capa": "mf_wfm_layout_wframe01_txt_dl_capa_dsc",
    "dl_pwr": "mf_wfm_layout_wframe01_txt_dl_pwr_dsc",
    "g_dl_capa": "mf_wfm_layout_wframe01_txt_g_dl_capa_dsc",
    "vol3_1": "mf_wfm_layout_wframe01_txt_dl_vol3_dsc_1",
    "vol3_2": "mf_wfm_layout_wframe01_txt_dl_vol3_dsc_2",
    # 여유상태 텍스트
    "subst_yn": "mf_wfm_layout_wframe01_txt_substYn",
    "mtr_yn": "mf_wfm_layout_wframe01_txt_mtrYn",
    "dl_yn": "mf_wfm_layout_wframe01_txt_dlYn",
}

# 검색 버튼 / 결과 프레임
_SEARCH_BTN_ID = "mf_wfm_layout_btn_search"
_RESULT_FRAME_ID = "mf_wfm_layout_wframe01"

DEFAULT_EWM_URL = "https://online.kepco.co.kr/EWM092D00"

# 대기 상한 (ms)
_WS_READY_TIMEOUT_MS = 20_000  # WebSquare $w 로드 대기
_SELECT_OPTION_TIMEOUT_MS = 8_000  # 개별 select 옵션 로드 대기
_SEARCH_RESULT_TIMEOUT_MS = 20_000  # 검색 결과 DOM 대기
_MAX_SEARCH_CLICKS = 3  # 검색 재클릭 최대 횟수

# 디버그 스냅샷 저장 디렉토리
_DEBUG_DIR = Path(tempfile.gettempdir()) / "kepco_debug"


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------


def _clean_number(text: str) -> str:
    """WebSquare 숫자 텍스트에서 콤마·공백·단위를 제거하고 순수 숫자 문자열 반환.

    예: "159,,000" → "159000", "13,000kW" → "13000", "" → "0"
    """
    if not text:
        return "0"
    cleaned = re.sub(r"[,\s]", "", text.strip())
    if not cleaned:
        return "0"
    digits = re.sub(r"[^\d\-.]", "", cleaned)
    return digits if digits else "0"


def _find_system_chromium() -> str | None:
    """시스템에 설치된 Chromium/Chrome 바이너리 경로를 찾는다."""
    candidates = [
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def _ensure_playwright_browsers() -> None:
    """Playwright 브라우저 바이너리가 없으면 자동 설치를 시도한다."""
    logger.info("📦 Playwright chromium 브라우저 자동 설치 시도...")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            logger.info("✅ Playwright chromium 설치 완료")
        else:
            logger.warning(
                "⚠️ Playwright chromium 설치 실패 (rc=%d): %s",
                proc.returncode,
                (proc.stderr or proc.stdout)[:300],
            )
    except Exception as exc:
        logger.warning("⚠️ Playwright 자동설치 중 예외: %s", exc)


def _save_debug_snapshot(page: Any, label: str) -> None:
    """실패 디버깅용 스크린샷 + HTML 덤프를 저장한다."""
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        # 스크린샷
        ss_path = _DEBUG_DIR / f"{label}_{ts}.png"
        page.screenshot(path=str(ss_path), full_page=True)
        logger.info("📸 디버그 스크린샷 저장: %s", ss_path)
        # HTML 덤프
        html_path = _DEBUG_DIR / f"{label}_{ts}.html"
        html_path.write_text(page.content(), encoding="utf-8")
        logger.info("📄 디버그 HTML 저장: %s", html_path)
    except Exception as exc:
        logger.warning("디버그 스냅샷 저장 실패: %s", exc)


# ---------------------------------------------------------------------------
# 옵션 데이터클래스
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OnlineScraperOptions:
    """한전ON 스크래퍼 옵션."""

    headless: bool = field(default_factory=lambda: settings.playwright_headless)
    page_load_timeout_ms: int = field(
        default_factory=lambda: int(settings.playwright_page_load_timeout_seconds * 1000)
    )
    result_timeout_seconds: float = field(
        default_factory=lambda: settings.playwright_result_timeout_seconds
    )
    browser_type: str = field(default_factory=lambda: settings.playwright_browser_type)


# ---------------------------------------------------------------------------
# 메인 스크래퍼 클래스
# ---------------------------------------------------------------------------


class KepcoOnlineScraper:
    """한전ON EWM092D00 Playwright 기반 용량 조회 스크래퍼 — 고도화 버전.

    사용법::

        scraper = KepcoOnlineScraper()
        records = scraper.fetch_capacity(
            sido="충청남도",
            si="천안시",
            gu="서북구",
            dong="불당동",
        )

    3계층 전략:
      L1) 브라우저 내 JS fetch()로 내부 API 직접 호출
      L2) DOM 풀 자동화 (개선판)
    """

    def __init__(
        self,
        url: str | None = None,
        options: OnlineScraperOptions | None = None,
    ) -> None:
        self._url = url or DEFAULT_EWM_URL
        self._options = options or OnlineScraperOptions()

    # ===================================================================
    # 공개 메서드
    # ===================================================================

    def fetch_capacity(
        self,
        sido: str,
        si: str = "",
        gu: str = "",
        dong: str = "",
        li: str = "",
        jibun: str = "",
    ) -> list[CapacityRecord]:
        """주소 정보로 여유용량을 조회하여 CapacityRecord 리스트를 반환.

        Args:
            sido: 시/도 (예: "충청남도")
            si: 시 (예: "천안시")
            gu: 구/군 (예: "서북구")
            dong: 동/면 (예: "불당동")
            li: 리 (선택)
            jibun: 상세번지 (선택)

        Returns:
            CapacityRecord 리스트 (최소 1건)

        Raises:
            ScraperError: 모든 전략이 실패한 경우
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScraperError(
                "playwright 패키지가 설치되어 있지 않습니다.\n"
                "설치: `pip install playwright && playwright install chromium`"
            ) from exc

        errors: list[str] = []

        with sync_playwright() as pw:
            browser = None
            page = None
            try:
                browser = self._launch_browser(pw)
                page = self._create_page(browser)

                # 페이지 로드 + WebSquare 준비
                self._navigate_and_wait(page)

                # L1: 브라우저 내 JS API 직접 호출
                try:
                    records = self._strategy_js_api(page, sido, si, gu, dong, li, jibun)
                    if records:
                        logger.info("✅ L1(JS API) 전략 성공 — %d건", len(records))
                        return records
                except Exception as exc:
                    msg = f"L1(JS API) 실패: {type(exc).__name__}: {exc}"
                    errors.append(msg)
                    logger.warning("⚠️ %s", msg)

                # L2: DOM 풀 자동화 (강화판)
                try:
                    records = self._strategy_dom_automation(page, sido, si, gu, dong, li, jibun)
                    if records:
                        logger.info("✅ L2(DOM 자동화) 전략 성공 — %d건", len(records))
                        return records
                except Exception as exc:
                    msg = f"L2(DOM 자동화) 실패: {type(exc).__name__}: {exc}"
                    errors.append(msg)
                    logger.warning("⚠️ %s", msg)
                    # 실패 시 디버그 스냅샷
                    if page:
                        _save_debug_snapshot(page, "L2_fail")

                raise ScraperError(
                    f"'{sido} {si} {gu} {dong}' 조회 실패 (모든 전략 소진).\n" + "\n".join(errors)
                )

            except ScraperError:
                raise
            except Exception as exc:
                logger.exception("한전ON 스크래핑 치명적 오류")
                if page:
                    _save_debug_snapshot(page, "fatal")
                raise ScraperError(
                    f"한전ON 브라우저 자동화 오류: {type(exc).__name__}: {exc}"
                ) from exc
            finally:
                if browser:
                    with suppress(Exception):
                        browser.close()

    def fetch_capacity_by_region(
        self,
        sido: str,
        sigungu: str,
        dong: str = "",
        li: str = "",
        jibun: str = "",
    ) -> list[CapacityRecord]:
        """RegionInfo 스타일 입력으로 조회.

        sigungu를 "시 + 구/군"으로 분리하여 내부 fetch_capacity 호출.
        """
        si, gu = self._split_sigungu(sigungu, sido)
        return self.fetch_capacity(sido=sido, si=si, gu=gu, dong=dong, li=li, jibun=jibun)

    @staticmethod
    def _split_sigungu(sigungu: str, sido: str) -> tuple[str, str]:
        """시군구명을 시/구로 분리.

        예:
          "천안시 서북구" → ("천안시", "서북구")
          "천안시" → ("천안시", "")
          "세종특별자치시" → ("", "")
        """
        if not sigungu or sigungu == sido:
            return ("", "")
        parts = sigungu.strip().split()
        if len(parts) >= 2:
            return (parts[0], " ".join(parts[1:]))
        return (sigungu.strip(), "")

    # ===================================================================
    # 브라우저 / 페이지 셋업
    # ===================================================================

    def _launch_browser(self, pw: Any) -> Any:
        """Playwright 브라우저 인스턴스 실행 (3단계 폴백)."""
        browser_type_name = self._options.browser_type.lower()
        launcher = getattr(pw, browser_type_name, pw.chromium)

        launch_args = []
        if browser_type_name == "chromium":
            launch_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--force-color-profile=srgb",
                "--metrics-recording-only",
                "--no-first-run",
            ]

        # 1차: Playwright 관리 바이너리
        try:
            return launcher.launch(headless=self._options.headless, args=launch_args)
        except Exception as first_err:
            logger.warning("⚠️ Playwright 바이너리 실패: %s", str(first_err)[:200])

        # 2차: 자동 설치 후 재시도
        _ensure_playwright_browsers()
        try:
            return launcher.launch(headless=self._options.headless, args=launch_args)
        except Exception as second_err:
            logger.warning("⚠️ 자동설치 후 실패: %s", str(second_err)[:200])

        # 3차: 시스템 chromium 폴백
        system_chromium = _find_system_chromium()
        if system_chromium and browser_type_name == "chromium":
            try:
                return launcher.launch(
                    headless=self._options.headless,
                    executable_path=system_chromium,
                    args=launch_args,
                )
            except Exception as third_err:
                raise ScraperError(
                    f"Playwright 브라우저 실행 실패 (3단계 모두 실패): {third_err}"
                ) from third_err

        raise ScraperError(
            "Playwright 브라우저를 실행할 수 없습니다.\n해결: `playwright install chromium` 실행"
        )

    def _create_page(self, browser: Any) -> Any:
        """자동화 감지 우회 + dialog 핸들러가 설정된 페이지를 생성."""
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        # 자동화 감지 우회 스크립트
        context.add_init_script("""
            // navigator.webdriver 숨기기
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            // chrome 런타임 위장
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
            // 플러그인 위장
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            // languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ko-KR', 'ko', 'en-US', 'en']
            });
            // permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (params) =>
                params.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(params);
        """)

        page = context.new_page()
        page.set_default_timeout(self._options.page_load_timeout_ms)

        # Dialog(alert/confirm/prompt) 자동 해제
        page.on("dialog", lambda dialog: dialog.dismiss())

        return page

    def _navigate_and_wait(self, page: Any) -> None:
        """EWM092D00 페이지를 로드하고 WebSquare가 준비될 때까지 대기."""
        logger.info("📡 한전ON EWM092D00 페이지 로딩: %s", self._url)

        # domcontentloaded 사용 — networkidle은 SPA에서 불안정
        page.goto(self._url, wait_until="domcontentloaded")
        logger.info("📄 DOM 로드 완료, WebSquare 초기화 대기 중...")

        # WebSquare 전역 객체($w) 대기
        try:
            page.wait_for_function(
                "() => typeof $w !== 'undefined' && typeof $w.getComponentById === 'function'",
                timeout=_WS_READY_TIMEOUT_MS,
            )
            logger.info("✅ WebSquare 준비 완료")
        except Exception:
            logger.warning(
                "⏰ WebSquare $w 대기 타임아웃 (%dms) — 계속 진행",
                _WS_READY_TIMEOUT_MS,
            )

        # 추가: 첫 번째 select(sido)에 옵션이 로드될 때까지 대기
        self._wait_for_select_options(page, _SELECT_IDS["sido"])
        logger.info("✅ 페이지 준비 완료: %s", page.url)

    # ===================================================================
    # L1: 브라우저 내 JS API 직접 호출
    # ===================================================================

    def _strategy_js_api(
        self,
        page: Any,
        sido: str,
        si: str,
        gu: str,
        dong: str,
        li: str,
        jibun: str,
    ) -> list[CapacityRecord]:
        """L1 전략: page.evaluate()로 한전ON 내부 REST API를 직접 호출.

        브라우저 세션/쿠키를 자동 활용하므로 인증 문제 없음.
        select 조작 없이 API만으로 데이터 획득.
        """
        logger.info("🔬 L1 전략: JS API 직접 호출 시도")

        # gbn 값 후보: "" (기본), "5" (전체 필드 검색 모드)
        gbn_candidates = ["", "5"]

        for gbn_value in gbn_candidates:
            addr_params = {
                "gbn": gbn_value,
                "addr_do": sido,
                "addr_si": si,
                "addr_gu": gu,
                "addr_lidong": dong,
                "addr_li": li,
                "addr_jibun": jibun or "1",
            }

            logger.info("🔬 L1 retrieveMeshNo 호출 (gbn='%s')", gbn_value)

            try:
                result = page.evaluate(
                    """(params) => {
                    return new Promise((resolve, reject) => {
                        const xhr = new XMLHttpRequest();
                        xhr.open('POST', '/ew/cpct/retrieveMeshNo', true);
                        xhr.setRequestHeader('Content-Type', 'application/json;charset=UTF-8');
                        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                        xhr.timeout = 15000;
                        xhr.onload = function() {
                            if (xhr.status === 200) {
                                try {
                                    resolve(JSON.parse(xhr.responseText));
                                } catch(e) {
                                    resolve({_raw: xhr.responseText.substring(0, 2000)});
                                }
                            } else {
                                reject(new Error('HTTP ' + xhr.status));
                            }
                        };
                        xhr.onerror = function() { reject(new Error('XHR error')); };
                        xhr.ontimeout = function() { reject(new Error('XHR timeout')); };
                        xhr.send(JSON.stringify({dma_addrGbn: params}));
                    });
                }""",
                    addr_params,
                )

                logger.info(
                    "🔬 L1 retrieveMeshNo 응답 (gbn='%s'): %s",
                    gbn_value,
                    str(result)[:500],
                )

                records = self._parse_api_response(result)
                if records:
                    return records
            except Exception as exc:
                logger.warning("⚠️ L1 gbn='%s' 호출 실패: %s", gbn_value, exc)

        # 내부 API 호출이 실패한 경우, 초기 로드/이전 조회 결과가 DOM에 남아있을 수 있어
        # DOM 파싱으로 "성공" 처리하면 잘못된 데이터를 반환할 위험이 있다.
        # (→ L2: DOM 자동화 전략으로 안전하게 폴백)
        return []

    def _parse_api_response(self, data: Any) -> list[CapacityRecord]:
        """내부 API 응답에서 CapacityRecord를 추출 시도."""
        if not isinstance(data, dict):
            return []

        # 한전ON 내부 API 응답 구조 분석 (가능한 필드들)
        # dma_result 또는 dlt_result 등에 데이터가 있을 수 있음
        for key in ["dma_result", "dlt_result", "result", "data"]:
            item = data.get(key)
            if isinstance(item, dict):
                return self._extract_record_from_dict(item)
            if isinstance(item, list) and item:
                records = []
                for entry in item:
                    if isinstance(entry, dict):
                        recs = self._extract_record_from_dict(entry)
                        records.extend(recs)
                if records:
                    return records

        # 최상위에 직접 결과가 있는 경우
        if data.get("subst_nm") or data.get("dl_nm"):
            return self._extract_record_from_dict(data)

        return []

    @staticmethod
    def _extract_record_from_dict(d: dict) -> list[CapacityRecord]:
        """딕셔너리에서 용량 레코드 추출."""
        subst_nm = str(d.get("subst_nm", d.get("substNm", "")))
        dl_nm = str(d.get("dl_nm", d.get("dlNm", "")))
        if not subst_nm and not dl_nm:
            return []

        record = CapacityRecord(
            substNm=subst_nm,
            mtrNo=str(d.get("mtr_no", d.get("mtrNo", ""))),
            dlNm=dl_nm,
            jsSubstPwr=_clean_number(str(d.get("js_subst_pwr", d.get("jsSubstPwr", "0")))),
            substPwr=_clean_number(str(d.get("subst_pwr", d.get("substPwr", "0")))),
            jsMtrPwr=_clean_number(str(d.get("js_mtr_pwr", d.get("jsMtrPwr", "0")))),
            mtrPwr=_clean_number(str(d.get("mtr_pwr", d.get("mtrPwr", "0")))),
            jsDlPwr=_clean_number(str(d.get("js_dl_pwr", d.get("jsDlPwr", "0")))),
            dlPwr=_clean_number(str(d.get("dl_pwr", d.get("dlPwr", "0")))),
            vol1=_clean_number(str(d.get("vol1", d.get("subst_vol1", "0")))),
            vol2=_clean_number(str(d.get("vol2", d.get("mtr_vol2", "0")))),
            vol3=_clean_number(str(d.get("vol3", d.get("dl_vol3", "0")))),
        )
        return [record]

    # ===================================================================
    # L2: DOM 풀 자동화 (강화판)
    # ===================================================================

    def _strategy_dom_automation(
        self,
        page: Any,
        sido: str,
        si: str,
        gu: str,
        dong: str,
        li: str,
        jibun: str,
    ) -> list[CapacityRecord]:
        """L2 전략: select 조작 + 검색 버튼 클릭 + DOM 파싱.

        기존 방식을 대폭 개선:
          - select 옵션 로드 대기 (wait_for_function)
          - 검색 최대 3회 재클릭
          - 결과 다중 필드 검증
        """
        logger.info("🔧 L2 전략: DOM 풀 자동화 시도")

        # 페이지를 새로 로드 (L1에서 상태가 바뀌었을 수 있음)
        page.goto(self._url, wait_until="domcontentloaded")
        with suppress(Exception):
            page.wait_for_function(
                "() => typeof $w !== 'undefined'",
                timeout=_WS_READY_TIMEOUT_MS,
            )
        self._wait_for_select_options(page, _SELECT_IDS["sido"])

        # 주소 선택 (cascading)
        self._select_address_robust(page, sido, si, gu, dong, li, jibun)

        # 검색 실행 (최대 _MAX_SEARCH_CLICKS 회)
        result_found = False
        for click_num in range(1, _MAX_SEARCH_CLICKS + 1):
            logger.info("🔍 검색 버튼 클릭 (%d/%d)", click_num, _MAX_SEARCH_CLICKS)
            self._click_search_button(page)

            if self._wait_for_results(page):
                result_found = True
                break

            logger.warning("⏰ 클릭 %d: 결과 미감지, 재시도...", click_num)
            time.sleep(1)

        if not result_found:
            # 마지막 시도: DOM에 이미 데이터가 있는지 확인 (display:none 이슈)
            logger.info("🔍 최종 DOM 데이터 확인...")

        records = self._parse_dom_results(page)
        if not records:
            _save_debug_snapshot(page, "L2_no_results")
            raise ScraperError(f"'{sido} {si} {gu} {dong}' 검색 결과를 DOM에서 찾지 못했습니다.")

        return records

    def _select_address_robust(
        self,
        page: Any,
        sido: str,
        si: str,
        gu: str,
        dong: str,
        li: str,
        jibun: str,
    ) -> None:
        """Cascading selectbox에 주소를 설정 — 각 단계마다 옵션 로드 대기."""
        steps = [
            ("sido", sido),
            ("si", si),
            ("gu", gu),
            ("lidong", dong),
            ("li", li),
        ]

        for name, value in steps:
            if not value or value == "전체":
                continue

            select_id = _SELECT_IDS[name]

            # 옵션 목록 로드 대기
            self._wait_for_select_options(page, select_id)

            try:
                options = self._get_select_options(page, select_id)
                meaningful = [
                    o.strip()
                    for o in options
                    if o and o.strip() and not o.strip().endswith("선택") and o.strip() != "선택"
                ]
                if not meaningful:
                    raise ScraperError(
                        f"'{name}' select 옵션 로딩 실패 (봇탐지/차단 가능). 옵션={options[:5]}"
                    )

                # 정확 매칭 우선 → 포함 매칭 → 첫 글자 매칭
                matched_value = self._find_best_option(value, options)
                if not matched_value:
                    raise ScraperError(
                        f"'{name}' selectbox에서 '{value}' 옵션을 찾을 수 없습니다. "
                        f"옵션 예시={meaningful[:10]}"
                    )

                # WebSquare 호환 select 값 설정
                if not self._set_select_value_robust(page, select_id, matched_value):
                    raise ScraperError(f"'{name}' select 값 설정 실패: '{matched_value}'")
                logger.info("✅ %s 선택: '%s'", name, matched_value)

                # 다음 select의 옵션이 로드될 때까지 대기
                next_steps = {
                    "sido": "si",
                    "si": "gu",
                    "gu": "lidong",
                    "lidong": "li",
                    "li": "bunji",
                }
                next_name = next_steps.get(name)
                if next_name and next_name in _SELECT_IDS:
                    # 다음 단계에 값이 필요한지 확인
                    next_value_needed = False
                    for future_name, future_value in steps:
                        if future_name == next_name and future_value and future_value != "전체":
                            next_value_needed = True
                            break
                    if next_value_needed or name in ("lidong", "li"):
                        self._wait_for_select_options(page, _SELECT_IDS[next_name])

            except ScraperError:
                raise
            except Exception as exc:
                raise ScraperError(f"{name} 선택 실패 ({value}): {exc}") from exc

        # 번지 선택
        self._select_bunji(page, jibun)

    def _select_bunji(self, page: Any, jibun: str) -> None:
        """번지(bunji) select 처리 — 값이 있으면 매칭, 없으면 첫 번째 유효 항목."""
        bunji_id = _SELECT_IDS["bunji"]
        try:
            self._wait_for_select_options(page, bunji_id, timeout_ms=5000)
            options = self._get_select_options(page, bunji_id)

            if len(options) <= 1:
                logger.info("ℹ️ 번지 옵션 없음 — 스킵")
                return

            if jibun and jibun in options:
                self._set_select_value_robust(page, bunji_id, jibun)
                logger.info("✅ 번지 선택: '%s'", jibun)
            else:
                # 첫 번째 유효 항목 (보통 index=1이 첫 번지)
                selected = options[1] if len(options) > 1 else None
                if selected:
                    self._set_select_value_robust(page, bunji_id, selected)
                    logger.info("✅ 번지 자동선택: '%s'", selected)
                else:
                    logger.info("ℹ️ 번지 자동선택 불가 — 유효 옵션 없음")
            time.sleep(0.5)
        except Exception as exc:
            logger.warning("⚠️ 번지 선택 실패: %s", exc)

    @staticmethod
    def _find_best_option(value: str, options: list[str]) -> str | None:
        """옵션 리스트에서 최적 매칭을 찾는다.

        우선순위: 정확매칭 > 포함매칭(value in option) > 포함매칭(option in value)
        """
        # 빈 값 / 플레이스홀더 제거 ("선택", "시/도 선택" 등)
        valid = []
        for opt in options:
            text = opt.strip()
            if not text:
                continue
            if text == "선택" or text.endswith("선택"):
                continue
            valid.append(text)
        if not valid:
            return None

        # 1) 정확 매칭
        if value in valid:
            return value

        # 2) 포함 매칭 (value가 option에 포함)
        for opt in valid:
            if value in opt:
                return opt

        # 3) 역방향 포함 매칭 (option이 value에 포함)
        for opt in valid:
            if opt in value:
                return opt

        return None

    @staticmethod
    def _wait_for_select_options(
        page: Any,
        select_id: str,
        timeout_ms: int = _SELECT_OPTION_TIMEOUT_MS,
    ) -> None:
        """특정 select 요소의 "의미있는" 옵션이 로드될 때까지 대기.

        WebSquare select는 초기 로드 시 placeholder + 빈 옵션(예: ["시/도 선택", ""])처럼
        옵션 길이만 2가 되는 경우가 있어, 단순 length>1 조건은 오탐이 발생한다.
        """
        try:
            page.wait_for_function(
                f"""() => {{
                    const sel = document.getElementById('{select_id}');
                    if (!sel || !sel.options) return false;
                    for (let i = 0; i < sel.options.length; i++) {{
                        const t = (sel.options[i].text || '').trim();
                        if (t.length > 0 && !t.endsWith('선택')) return true;
                    }}
                    return false;
                }}""",
                timeout=timeout_ms,
            )
        except Exception:
            # 타임아웃이어도 계속 진행 (옵션이 아예 없는 select일 수 있음)
            return

    @staticmethod
    def _get_select_options(page: Any, select_id: str) -> list[str]:
        """native select 요소의 옵션 텍스트 목록을 반환."""
        return page.evaluate(f"""() => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return [];
            const opts = [];
            for (let i = 0; i < sel.options.length; i++) {{
                opts.push(sel.options[i].text);
            }}
            return opts;
        }}""")

    @staticmethod
    def _set_select_value_robust(page: Any, select_id: str, label: str) -> bool:
        """WebSquare 호환 select 값 설정.

        1차: $w.getComponentById API (WebSquare 네이티브)
        2차: page.select_option (Playwright native select)
        3차: JavaScript로 직접 selectedIndex + change event dispatch

        Args:
            page: Playwright Page 객체
            select_id: native select 요소 ID (예: mf_wfm_layout_sbx_sido_input_0)
            label: 선택할 옵션 텍스트

        Returns:
            선택 성공 여부
        """
        # WebSquare 컴포넌트 ID 추출: "mf_" 접두어 및 "_input_0" 접미어 제거
        comp_id = select_id
        if comp_id.startswith("mf_"):
            comp_id = comp_id[3:]
        if comp_id.endswith("_input_0"):
            comp_id = comp_id[:-8]

        # Attempt 1: WebSquare $w API
        try:
            result = page.evaluate(
                f"""(label) => {{
                try {{
                    var comp = $w.getComponentById('{comp_id}');
                    if (comp) {{
                        // getItemCount + getItemText + setSelectedIndex
                            var count = comp.getItemCount ? comp.getItemCount() : 0;
                            for (var i = 0; i < count; i++) {{
                                var text = comp.getItemText ? comp.getItemText(i) : '';
                                if (
                                    text === label ||
                                    text.indexOf(label) >= 0 ||
                                    label.indexOf(text) >= 0
                                ) {{
                                    comp.setSelectedIndex(i);
                                    return 'ws_api';
                                }}
                        }}
                        // direct setValue 폴백
                        if (comp.setValue) {{
                            comp.setValue(label);
                            return 'ws_setValue';
                        }}
                    }}
                }} catch(e) {{}}
                return '';
            }}""",
                label,
            )
            if result:
                logger.info(
                    "✅ WebSquare API로 선택: %s = '%s' (method=%s)",
                    select_id,
                    label,
                    result,
                )
                time.sleep(0.3)
                return True
        except Exception:
            logger.debug(
                "WebSquare API select 설정 실패: %s = '%s'",
                select_id,
                label,
                exc_info=True,
            )

        # Attempt 2: Playwright native page.select_option
        try:
            page.select_option(f"#{select_id}", label=label)
            logger.info("✅ Native select_option으로 선택: %s = '%s'", select_id, label)
            time.sleep(0.3)
            return True
        except Exception:
            logger.debug(
                "Native select_option 실패: %s = '%s'",
                select_id,
                label,
                exc_info=True,
            )

        # Attempt 3: JavaScript selectedIndex + change event dispatch
        try:
            result = page.evaluate(
                f"""(label) => {{
                var sel = document.getElementById('{select_id}');
                if (!sel) return false;
                for (var i = 0; i < sel.options.length; i++) {{
                    if (sel.options[i].text === label || sel.options[i].text.indexOf(label) >= 0) {{
                        sel.selectedIndex = i;
                        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return true;
                    }}
                }}
                return false;
            }}""",
                label,
            )
            if result:
                logger.info("✅ JS dispatchEvent로 선택: %s = '%s'", select_id, label)
                time.sleep(0.3)
                return True
        except Exception:
            logger.debug(
                "JS dispatchEvent select 설정 실패: %s = '%s'",
                select_id,
                label,
                exc_info=True,
            )

        logger.warning("❌ 모든 select 설정 방법 실패: %s = '%s'", select_id, label)
        return False

    @staticmethod
    def _click_search_button(page: Any) -> None:
        """검색 버튼을 클릭한다."""
        # 방법 1: ID로 직접 클릭
        search_btn = page.query_selector(f"#{_SEARCH_BTN_ID}")
        if search_btn:
            search_btn.click()
            return

        # 방법 2: evaluate로 클릭 이벤트 발생
        page.evaluate(f"""() => {{
            const btn = document.getElementById('{_SEARCH_BTN_ID}');
            if (btn) {{
                btn.click();
                return true;
            }}
            // 폴백: 텍스트로 찾기
            const buttons = document.querySelectorAll(
                'button, a[role="button"], div[role="button"]'
            );
            for (const b of buttons) {{
                if (b.textContent.includes('검색')) {{
                    b.click();
                    return true;
                }}
            }}
            return false;
        }}""")

    def _wait_for_results(self, page: Any) -> bool:
        """검색 결과가 DOM에 나타날 때까지 대기. 성공하면 True.

        1차: wait_for_function으로 결과 필드 감지 (최대 _SEARCH_RESULT_TIMEOUT_MS)
        2차: 폴백 — 수동 DOM 폴링 (1초 간격, 최대 10회)
        """
        check_ids = [
            _RESULT_IDS["dl_nm"],
            _RESULT_IDS["subst_nm"],
            _RESULT_IDS["vol1_1"],
            _RESULT_IDS["vol3_1"],
        ]

        # Attempt 1: Playwright wait_for_function
        try:
            page.wait_for_function(
                """(ids) => {
                    return ids.some(id => {
                        const el = document.getElementById(id);
                        return el && el.textContent.trim().length > 0;
                    });
                }""",
                check_ids,
                timeout=_SEARCH_RESULT_TIMEOUT_MS,
            )
            logger.info("✅ 결과 데이터 로드 감지됨 (wait_for_function)")
            time.sleep(1)  # 나머지 필드 렌더링 대기
            return True
        except Exception:
            logger.info("⏰ wait_for_function 타임아웃, DOM 폴링 폴백 시도...")

        # Attempt 2: 수동 DOM 폴링 폴백
        max_polls = 10
        for poll in range(1, max_polls + 1):
            time.sleep(1)
            try:
                found = page.evaluate(
                    """(ids) => {
                        return ids.some(id => {
                            const el = document.getElementById(id);
                            return el && el.textContent.trim().length > 0;
                        });
                    }""",
                    check_ids,
                )
                if found:
                    logger.info("✅ 결과 데이터 로드 감지됨 (DOM 폴링 %d/%d)", poll, max_polls)
                    time.sleep(0.5)
                    return True
            except Exception as exc:
                # 반복 호출되는 구간이라 과도한 traceback 로그는 피한다.
                # (첫 실패는 원인 파악을 위해 traceback 포함)
                if poll == 1:
                    logger.debug("DOM 폴링 evaluate 실패(최초): %s", exc, exc_info=True)
                else:
                    logger.debug("DOM 폴링 evaluate 실패: %s", exc)
            logger.debug("⏳ DOM 폴링 %d/%d: 결과 미감지", poll, max_polls)

        return False

    # ===================================================================
    # DOM 파싱 (L1, L2 공통)
    # ===================================================================

    @staticmethod
    def _parse_dom_results(page: Any) -> list[CapacityRecord]:
        """결과 프레임(wframe01) DOM에서 용량 데이터를 추출하여 CapacityRecord로 변환.

        wframe01이 display:none 상태여도 데이터는 DOM에 주입되어 있다.
        """
        result_ids_json = json.dumps(_RESULT_IDS)

        raw = page.evaluate(
            f"""() => {{
            const result = {{}};
            const ids = {result_ids_json};
            for (const [key, elId] of Object.entries(ids)) {{
                const el = document.getElementById(elId);
                result[key] = el ? el.textContent.trim() : '';
            }}
            return result;
        }}"""
        )

        subst_nm = raw.get("subst_nm", "")
        mtr_no = raw.get("mtr_no", "")
        dl_nm = raw.get("dl_nm", "")

        if not subst_nm and not dl_nm:
            logger.warning("결과 데이터가 비어있습니다. raw=%s", raw)
            return []

        vol1 = _clean_number(raw.get("vol1_1", "0"))
        vol2 = _clean_number(raw.get("vol2_1", "0"))
        vol3 = _clean_number(raw.get("vol3_1", "0"))

        js_subst_pwr = _clean_number(raw.get("subst_capa", "0"))
        subst_pwr = _clean_number(raw.get("subst_pwr", "0"))
        js_mtr_pwr = _clean_number(raw.get("mtr_capa", "0"))
        mtr_pwr = _clean_number(raw.get("mtr_pwr", "0"))
        js_dl_pwr = _clean_number(raw.get("dl_capa", "0"))
        dl_pwr = _clean_number(raw.get("dl_pwr", "0"))

        record = CapacityRecord(
            substNm=subst_nm,
            mtrNo=mtr_no,
            dlNm=dl_nm,
            jsSubstPwr=js_subst_pwr,
            substPwr=subst_pwr,
            jsMtrPwr=js_mtr_pwr,
            mtrPwr=mtr_pwr,
            jsDlPwr=js_dl_pwr,
            dlPwr=dl_pwr,
            vol1=vol1,
            vol2=vol2,
            vol3=vol3,
        )

        logger.info(
            "📊 파싱 결과: 변전소=%s, 변압기=%s, DL=%s | vol1=%s, vol2=%s, vol3=%s",
            subst_nm,
            mtr_no,
            dl_nm,
            vol1,
            vol2,
            vol3,
        )

        return [record]

    # _parse_results 는 기존 테스트 호환을 위해 유지
    _parse_results = _parse_dom_results
